import json
import logging
import random
import time

import requests

from app.decisions.contract import (
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

ACTION_CRITERIA: dict[str, str] = {
    "mark_read": (
        "Low-signal informational mail the user wants to keep: FYI threads, "
        "status updates they were only cc'd on. No reply expected."
    ),
    "archive": (
        "Receipts, confirmations and finished threads: out of the inbox but retained."
    ),
    "delete": (
        "Disposable noise: CI run notifications, obvious junk, promos the user never "
        "reads. Nothing the user could need later."
    ),
    "needs_reply": (
        "A person is asking a question, requesting an action, or otherwise expecting "
        "a personal response from the user."
    ),
}

REASON_CRITERIA: dict[str, str] = {
    ReasonCode.NEWSLETTER: "Subscription newsletter or digest.",
    ReasonCode.PROMOTION: "Marketing or promotional mail.",
    ReasonCode.CI_NOTIFICATION: "Automated build, test or deployment notification.",
    ReasonCode.TOOL_NOTIFICATION: "Automated notification from a SaaS tool.",
    ReasonCode.RECEIPT: "Receipt, invoice or payment confirmation.",
    ReasonCode.STATUS_UPDATE: "Project or team status update, informational only.",
    ReasonCode.CALENDAR_UPDATE: "Calendar change such as a cancellation or reschedule notice.",
    ReasonCode.MEETING_REQUEST: "Invitation asking the user to confirm or propose a time.",
    ReasonCode.DIRECT_QUESTION: "A person asks the user a direct question.",
    ReasonCode.ACTION_REQUEST: "A person asks the user to do something specific.",
    ReasonCode.FOLLOW_UP: "Repeated follow-up chasing an earlier unanswered message.",
    ReasonCode.URGENT_ISSUE: "Urgent incident or failure needing attention.",
    ReasonCode.OTHER: "None of the other categories fit.",
}


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
            chunk_usage = body.get("usage") or {}
            usage = DecisionUsage(
                input_tokens=usage.input_tokens + int(chunk_usage.get("input_tokens") or 0),
                output_tokens=usage.output_tokens + int(chunk_usage.get("output_tokens") or 0),
            )

        return TriageDecisionBatch(
            provider=self.name,
            model=self.settings.model,
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
                    "be proposed to the user?"
                ),
                "criteria": ACTION_CRITERIA,
            }
            questions[f"reason_{ref}"] = {
                "type": "choice",
                "instructions": (
                    f"For the inbox email with ref '{ref}', which category best "
                    "explains that classification?"
                ),
                "criteria": REASON_CRITERIA,
            }
        return {"model": self.settings.model, "state": {"emails": state}, "questions": questions}

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
            decisions.append(self._decision(item.item_id, answers.get(f"action_{ref}"), answers.get(f"reason_{ref}")))
        return decisions

    @staticmethod
    def _decision(item_id: str, action_answer, reason_answer) -> TriageDecision:
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

        return TriageDecision(
            item_id=item_id,
            status="ok",
            action=action,
            reason_code=reason_code,
            confidence=confidence,
            probabilities=probabilities,
        )
