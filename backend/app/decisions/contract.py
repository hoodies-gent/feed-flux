from enum import StrEnum
from typing import Literal, Protocol

from pydantic import BaseModel, Field


TriageAction = Literal["mark_read", "archive", "delete", "needs_reply"]
TRIAGE_ACTIONS: tuple[TriageAction, ...] = ("mark_read", "archive", "delete", "needs_reply")
DecisionStatus = Literal["ok", "abstained", "failed"]


class ReasonCode(StrEnum):
    NEWSLETTER = "newsletter"
    PROMOTION = "promotion"
    CI_NOTIFICATION = "ci_notification"
    TOOL_NOTIFICATION = "tool_notification"
    RECEIPT = "receipt"
    STATUS_UPDATE = "status_update"
    CALENDAR_UPDATE = "calendar_update"
    MEETING_REQUEST = "meeting_request"
    DIRECT_QUESTION = "direct_question"
    ACTION_REQUEST = "action_request"
    FOLLOW_UP = "follow_up"
    URGENT_ISSUE = "urgent_issue"
    OTHER = "other"


# Shared by every adapter so the arms of an A/B answer the same question.
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

IMPORTANCE_QUESTION = (
    "Would the user lose something they need if this email were archived or deleted "
    "without them reading it?"
)
IMPORTANCE_CRITERIA: dict[str, str] = {
    "true": (
        "Yes. It carries a deadline, money, a security or account action, a live "
        "incident, a record worth keeping, or a person waiting on the user."
    ),
    "false": (
        "No. It is disposable or fully re-derivable noise: routine tool notifications, "
        "marketing, digests the user opted into but can skip."
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


class TriageItem(BaseModel):
    item_id: str
    subject: str
    sender: str | None = None
    sender_email: str | None = None
    received: str | None = None
    body_preview: str | None = None


class TriageDecision(BaseModel):
    item_id: str
    status: DecisionStatus
    action: TriageAction | None = None
    reason_code: ReasonCode | None = None
    confidence: float | None = Field(default=None, ge=0.0, le=1.0)
    probabilities: dict[TriageAction, float] = Field(default_factory=dict)
    # Second axis, asked separately from the action so a provider can say an email
    # belongs in the inbox *and* matters. Absent when a provider does not answer it.
    important: bool | None = None
    importance_confidence: float | None = Field(default=None, ge=0.0, le=1.0)
    error_category: str | None = None


class DecisionUsage(BaseModel):
    input_tokens: int = 0
    output_tokens: int = 0


class TriageDecisionBatch(BaseModel):
    provider: str
    model: str | None = None
    decisions: list[TriageDecision]
    usage: DecisionUsage = Field(default_factory=DecisionUsage)
    latency_ms: float = 0.0
    requests: int = 0
    failed_requests: int = 0


class DecisionProvider(Protocol):
    name: str

    def decide_triage(self, items: list[TriageItem]) -> TriageDecisionBatch: ...


def align_decisions(
    items: list[TriageItem],
    decisions: list[TriageDecision],
    *,
    missing_error_category: str = "terminal",
) -> list[TriageDecision]:
    by_id = {decision.item_id: decision for decision in decisions}
    return [
        by_id.get(item.item_id)
        or TriageDecision(
            item_id=item.item_id,
            status="failed",
            error_category=missing_error_category,
        )
        for item in items
    ]
