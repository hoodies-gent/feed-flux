import json
import logging
import time

from langchain_core.messages import HumanMessage, SystemMessage
from pydantic import BaseModel, Field

from app.agent.llm import get_llm
from app.agent.runtime_errors import classify_runtime_error
from app.agent.usage import usage_event_from_message
from app.decisions.contract import (
    ACTION_CRITERIA,
    REASON_CRITERIA,
    DecisionUsage,
    ReasonCode,
    TriageAction,
    TriageDecision,
    TriageDecisionBatch,
    TriageItem,
    align_decisions,
)
from app.decisions.settings import DecisionSettings, decision_settings
from app.services.agent_run_store import ErrorCategory


logger = logging.getLogger(__name__)


def _prompt() -> str:
    actions = "\n".join(f"- {name}: {text}" for name, text in ACTION_CRITERIA.items())
    reasons = "\n".join(f"- {code}: {text}" for code, text in REASON_CRITERIA.items())
    return (
        "You triage a user's unread email. For every email in the state, propose one "
        "action and one reason code. Answer for every ref, exactly once.\n\n"
        f"Actions:\n{actions}\n\nReason codes:\n{reasons}\n\n"
        "confidence is your probability that the action is the one the user would "
        "pick, between 0 and 1. Use low values when the email is ambiguous. Propose "
        "only; the user executes every action themselves."
    )


class _LlmItemDecision(BaseModel):
    ref: str
    action: TriageAction
    reason_code: ReasonCode
    confidence: float = Field(ge=0.0, le=1.0)


class _LlmBatchDecision(BaseModel):
    decisions: list[_LlmItemDecision]


class LlmDecisionProvider:
    """Fallback arm of the A/B: the same typed contract answered by the existing
    generative provider. Its confidence is self-reported, not calibrated."""

    name = "llm"

    def __init__(
        self,
        llm=None,
        *,
        provider: str | None = None,
        model_name: str | None = None,
        settings: DecisionSettings | None = None,
    ):
        self.settings = settings or decision_settings()
        self._llm = llm
        self._provider = provider
        self._model_name = model_name

    def _model(self):
        if self._llm is None:
            self._llm = get_llm(provider=self._provider, model_name=self._model_name, temperature=0)
        return self._llm

    def decide_triage(self, items: list[TriageItem]) -> TriageDecisionBatch:
        if not items:
            return TriageDecisionBatch(provider=self.name, model=self._model_name, decisions=[])

        started = time.monotonic()
        decisions: list[TriageDecision] = []
        usage = DecisionUsage()
        model_name = self._model_name
        requests_made = 0
        failed_requests = 0

        # Chunked by item count only: unlike Jev there is no separate per-question
        # budget, and the agent path already sends a whole batch in one call.
        size = self.settings.max_items_per_request
        for chunk in [items[at : at + size] for at in range(0, len(items), size)]:
            requests_made += 1
            try:
                result = self._invoke(chunk)
            except Exception as error:
                failed_requests += 1
                category = classify_runtime_error(error)
                logger.warning(
                    "llm decision chunk failed: items=%d category=%s",
                    len(chunk),
                    category.value,
                )
                decisions.extend(
                    TriageDecision(item_id=item.item_id, status="failed", error_category=category.value)
                    for item in chunk
                )
                continue

            raw = result.get("raw") if isinstance(result, dict) else None
            usage_event = usage_event_from_message(raw)
            if usage_event is not None:
                model_name = usage_event.get("model") or model_name
                totals = usage_event["usage"]
                usage = DecisionUsage(
                    input_tokens=usage.input_tokens + totals["input_tokens"],
                    output_tokens=usage.output_tokens + totals["output_tokens"],
                )

            parsed = result.get("parsed") if isinstance(result, dict) else None
            if not isinstance(result, dict) or result.get("parsing_error") is not None or parsed is None:
                failed_requests += 1
                decisions.extend(
                    TriageDecision(
                        item_id=item.item_id,
                        status="failed",
                        error_category=ErrorCategory.LLM_TOOL_REPAIRABLE.value,
                    )
                    for item in chunk
                )
                continue
            decisions.extend(self._decisions(chunk, parsed))

        return TriageDecisionBatch(
            provider=self.name,
            model=model_name,
            decisions=align_decisions(items, decisions),
            usage=usage,
            latency_ms=round((time.monotonic() - started) * 1000, 2),
            requests=requests_made,
            failed_requests=failed_requests,
        )

    def _invoke(self, chunk: list[TriageItem]):
        state = [
            {
                "ref": f"e{index}",
                "subject": item.subject,
                "sender": item.sender,
                "sender_email": item.sender_email,
                "received": item.received,
                "preview": (item.body_preview or "")[: self.settings.preview_chars],
            }
            for index, item in enumerate(chunk)
        ]
        structured = self._model().with_structured_output(_LlmBatchDecision, include_raw=True)
        return structured.invoke(
            [
                SystemMessage(content=_prompt()),
                HumanMessage(content=json.dumps({"emails": state}, ensure_ascii=False)),
            ]
        )

    @staticmethod
    def _decisions(chunk: list[TriageItem], parsed) -> list[TriageDecision]:
        try:
            batch = _LlmBatchDecision.model_validate(parsed)
        except Exception:
            return [
                TriageDecision(
                    item_id=item.item_id,
                    status="failed",
                    error_category=ErrorCategory.LLM_TOOL_REPAIRABLE.value,
                )
                for item in chunk
            ]
        by_ref = {f"e{index}": item.item_id for index, item in enumerate(chunk)}
        decisions = []
        for decision in batch.decisions:
            item_id = by_ref.get(decision.ref)
            if item_id is None:
                continue
            decisions.append(
                TriageDecision(
                    item_id=item_id,
                    status="ok",
                    action=decision.action,
                    reason_code=decision.reason_code,
                    confidence=decision.confidence,
                )
            )
        return decisions
