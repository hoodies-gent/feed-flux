import json
import logging
import random
import time

import requests

from app.decisions.contract import (
    ACTION_CRITERIA,
    IMPORTANCE_CRITERIA,
    IMPORTANCE_QUESTION,
    REASON_CRITERIA,
    TRIAGE_ACTIONS,
    DecisionUsage,
    ReasonCode,
    TriageDecision,
    TriageDecisionBatch,
    TriageItem,
    align_decisions,
)
from app.decisions.settings import DecisionSettings, decision_settings
from app.services.agent_run_store import ErrorCategory


logger = logging.getLogger(__name__)

# Jev adds 529 (overloaded) to the usual transient set, so the mapping lives here
# rather than in the shared agent runtime classifier.
_RETRYABLE_STATUS = {429, 500, 502, 503, 504, 529}
_USER_REPAIRABLE_STATUS = {401, 403}


class JevRequestError(Exception):
    """Carries only the HTTP status and category — never the response body,
    which can echo the email content that was sent."""

    def __init__(self, status_code: int | None, category: ErrorCategory):
        super().__init__(f"jev request failed (status={status_code or 'none'})")
        self.status_code = status_code
        self.error_category = category


def _category_for_status(status_code: int) -> ErrorCategory:
    if status_code in _RETRYABLE_STATUS:
        return ErrorCategory.TRANSIENT
    if status_code in _USER_REPAIRABLE_STATUS:
        return ErrorCategory.USER_REPAIRABLE
    if status_code == 422:
        return ErrorCategory.LLM_TOOL_REPAIRABLE
    return ErrorCategory.TERMINAL


def _estimated_tokens(payload: dict) -> int:
    return len(json.dumps(payload, ensure_ascii=False)) // 4


class JevDecisionProvider:
    name = "jev"

    def __init__(
        self,
        settings: DecisionSettings | None = None,
        *,
        session=None,
        sleep=time.sleep,
    ):
        self.settings = settings or decision_settings()
        if not self.settings.api_key:
            raise ValueError("TYPESAFE_API_KEY is missing.")
        self.session = session or requests.Session()
        self._sleep = sleep

    def decide_triage(self, items: list[TriageItem]) -> TriageDecisionBatch:
        if not items:
            return TriageDecisionBatch(provider=self.name, model=self.settings.model, decisions=[])

        started = time.monotonic()
        decisions: list[TriageDecision] = []
        usage = DecisionUsage()
        requests_made = 0
        failed_requests = 0
        served: list[str] = []

        for chunk in self._chunks(items):
            requests_made += 1
            try:
                body = self._post(self._payload(chunk))
            except JevRequestError as error:
                failed_requests += 1
                logger.warning(
                    "jev chunk failed: items=%d status=%s category=%s",
                    len(chunk),
                    error.status_code,
                    error.error_category.value,
                )
                decisions.extend(
                    TriageDecision(
                        item_id=item.item_id,
                        status="failed",
                        error_category=error.error_category.value,
                    )
                    for item in chunk
                )
                continue
            decisions.extend(self._parse(chunk, body))
            answered = body.get("model")
            if answered and answered not in served:
                served.append(answered)
            chunk_usage = body.get("usage") or {}
            usage = DecisionUsage(
                input_tokens=usage.input_tokens + int(chunk_usage.get("input_tokens") or 0),
                output_tokens=usage.output_tokens + int(chunk_usage.get("output_tokens") or 0),
            )

        return TriageDecisionBatch(
            provider=self.name,
            # The version that answered, not the name we asked for. `jev-latest` is
            # an alias that moves when a release ships, so storing the request's
            # name leaves a paid result unattributable. More than one value means
            # the alias moved mid-batch; record that rather than hide it behind the
            # first chunk. Falls back to the configured name only when no chunk
            # came back at all.
            model=", ".join(served) or self.settings.model,
            decisions=align_decisions(items, decisions),
            usage=usage,
            latency_ms=round((time.monotonic() - started) * 1000, 2),
            requests=requests_made,
            failed_requests=failed_requests,
        )

    def _chunks(self, items: list[TriageItem]) -> list[list[TriageItem]]:
        chunks: list[list[TriageItem]] = []
        current: list[TriageItem] = []
        for item in items:
            candidate = current + [item]
            over_budget = _estimated_tokens(self._payload(candidate)) > self.settings.token_budget
            if current and (over_budget or len(candidate) > self.settings.max_items_per_request):
                chunks.append(current)
                current = [item]
            else:
                current = candidate
        if current:
            chunks.append(current)
        return chunks

    def _payload(self, chunk: list[TriageItem]) -> dict:
        state = []
        questions: dict[str, dict] = {}
        for index, item in enumerate(chunk):
            ref = f"e{index}"
            preview = (item.body_preview or "")[: self.settings.preview_chars]
            state.append(
                {
                    "ref": ref,
                    "subject": item.subject,
                    "sender": item.sender,
                    "sender_email": item.sender_email,
                    "received": item.received,
                    "preview": preview,
                }
            )
            questions[f"action_{ref}"] = {
                "type": "choice",
                "instructions": (
                    f"For the inbox email with ref '{ref}', which triage action should "
                    "be proposed to the user? Apply the rubric in `rubrics.action`."
                ),
                # Option keys with null descriptions. `criteria` is required for a
                # choice and is what defines the option set, but the rubric behind
                # each option is identical for every email in the batch, so it is
                # stated once in the state rather than repeated per question. The
                # API documents null for an option that needs no extra detail.
                "criteria": dict.fromkeys(ACTION_CRITERIA),
            }
            questions[f"reason_{ref}"] = {
                "type": "choice",
                "instructions": (
                    f"For the inbox email with ref '{ref}', which category best "
                    "explains that classification? Apply the rubric in `rubrics.reason`."
                ),
                "criteria": dict.fromkeys(REASON_CRITERIA),
            }
            questions[f"important_{ref}"] = {
                # This rubric stays inline, unlike the other two. Moving it into
                # the state left every answer unchanged but made Jev measurably
                # less certain: importance_confidence fell on the borderline
                # cases, cases under the gate threshold went from 14 to 16 of 38,
                # and the dismissal ceiling dropped from 0.85 to 0.54. The gate
                # cuts on this confidence, so this is the one axis where the
                # rubric has to sit where it sharpens the distribution.
                "type": "noul",
                "instructions": f"For the inbox email with ref '{ref}': {IMPORTANCE_QUESTION}",
                "criteria": IMPORTANCE_CRITERIA,
            }
        return {
            "model": self.settings.model,
            # The action and reason rubrics ride along with the emails because the
            # state is ingested once per request while questions are not: repeating
            # them inline made 86% of the request body the same text over and over,
            # and Jev bills by input token with no prompt cache. Neither answer is
            # ever executed — the plan only proposes mark_read — so they are the
            # two axes that can afford the weaker placement.
            "state": {
                "emails": state,
                "rubrics": {"action": ACTION_CRITERIA, "reason": REASON_CRITERIA},
            },
            "questions": questions,
        }

    def _post(self, payload: dict) -> dict:
        url = f"{self.settings.base_url}/v1/systemone"
        headers = {
            "Authorization": f"Bearer {self.settings.api_key}",
            "Content-Type": "application/json",
        }
        last_error = JevRequestError(None, ErrorCategory.TERMINAL)
        for attempt in range(self.settings.max_attempts):
            try:
                response = self.session.post(
                    url, json=payload, headers=headers, timeout=self.settings.timeout_seconds
                )
            except requests.Timeout:
                last_error = JevRequestError(None, ErrorCategory.TRANSIENT)
            except requests.RequestException:
                last_error = JevRequestError(None, ErrorCategory.TRANSIENT)
            else:
                status = response.status_code
                if status < 400:
                    try:
                        return response.json()
                    except ValueError:
                        raise JevRequestError(status, ErrorCategory.TERMINAL) from None
                last_error = JevRequestError(status, _category_for_status(status))
                if last_error.error_category is not ErrorCategory.TRANSIENT:
                    raise last_error

            if attempt < self.settings.max_attempts - 1:
                backoff = min(0.5 * (2**attempt), 4.0)
                self._sleep(backoff * (0.5 + random.random() / 2))
        raise last_error

    def _parse(self, chunk: list[TriageItem], body: dict) -> list[TriageDecision]:
        answers = body.get("answers") or {}
        decisions = []
        for index, item in enumerate(chunk):
            ref = f"e{index}"
            decisions.append(
                self._decision(
                    item.item_id,
                    answers.get(f"action_{ref}"),
                    answers.get(f"reason_{ref}"),
                    answers.get(f"important_{ref}"),
                )
            )
        return decisions

    @staticmethod
    def _decision(item_id: str, action_answer, reason_answer, importance_answer=None) -> TriageDecision:
        def failed(category: ErrorCategory) -> TriageDecision:
            return TriageDecision(item_id=item_id, status="failed", error_category=category.value)

        if not isinstance(action_answer, dict):
            return failed(ErrorCategory.TERMINAL)
        action = action_answer.get("choice")
        if action not in TRIAGE_ACTIONS:
            return failed(ErrorCategory.LLM_TOOL_REPAIRABLE)

        confidence = action_answer.get("confidence")
        confidence = float(confidence) if isinstance(confidence, (int, float)) else None
        if confidence is not None:
            confidence = min(max(confidence, 0.0), 1.0)

        raw_probabilities = action_answer.get("probabilities")
        probabilities = (
            {
                key: float(value)
                for key, value in raw_probabilities.items()
                if key in TRIAGE_ACTIONS and isinstance(value, (int, float))
            }
            if isinstance(raw_probabilities, dict)
            else {}
        )

        reason_choice = reason_answer.get("choice") if isinstance(reason_answer, dict) else None
        reason_code = reason_choice if reason_choice in set(ReasonCode) else None

        important = importance_confidence = None
        if isinstance(importance_answer, dict):
            noul = importance_answer.get("noul")
            if isinstance(noul, (int, float)):
                noul = min(max(float(noul), 0.0), 1.0)
                important = noul >= 0.5
                # A noul near either end is a confident answer; 0.5 is maximal doubt.
                importance_confidence = abs(noul - 0.5) * 2

        return TriageDecision(
            item_id=item_id,
            status="ok",
            action=action,
            reason_code=reason_code,
            confidence=confidence,
            probabilities=probabilities,
            important=important,
            importance_confidence=importance_confidence,
        )
