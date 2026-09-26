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
