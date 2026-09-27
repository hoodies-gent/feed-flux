from dataclasses import dataclass
from typing import Literal

from pydantic import BaseModel

from app.decisions.contract import ReasonCode, TriageAction, TriageDecision


GateOutcome = Literal["accept", "fallback", "review"]
GateRule = Literal[
    "accepted",
    "provider_failed",
    "abstained",
    "below_threshold",
    "delete_below_threshold",
    "important_bulk_action",
]
REMOVING_ACTIONS = ("delete", "archive")
ReasonLanguage = Literal["en", "zh"]


@dataclass(frozen=True)
class GatePolicy:
    accept_threshold: float = 0.7
    delete_threshold: float = 0.9
    importance_threshold: float = 0.7


class GateResult(BaseModel):
    item_id: str
    outcome: GateOutcome
    rule: GateRule
    action: TriageAction | None = None
    reason_code: ReasonCode | None = None
    confidence: float | None = None
    important: bool | None = None


def apply_gate(decision: TriageDecision, policy: GatePolicy = GatePolicy()) -> GateResult:
    def result(outcome: GateOutcome, rule: GateRule) -> GateResult:
        return GateResult(
            item_id=decision.item_id,
            outcome=outcome,
            rule=rule,
            action=decision.action,
            reason_code=decision.reason_code,
            confidence=decision.confidence,
            important=decision.important,
        )

    if decision.status == "failed":
        return result("fallback", "provider_failed")
    if decision.status == "abstained" or decision.action is None or decision.confidence is None:
        return result("fallback", "abstained")
    # The provider's own importance signal outranks a confident bulk action: taking mail
    # out of the inbox is what loses it. Skipped when the provider does not answer it.
    if (
        decision.action in REMOVING_ACTIONS
        and decision.important
        and (decision.importance_confidence or 0.0) >= policy.importance_threshold
    ):
        return result("review", "important_bulk_action")
    # A low-confidence delete is never handed to another model to re-litigate; the user decides.
    if decision.action == "delete" and decision.confidence < policy.delete_threshold:
        return result("review", "delete_below_threshold")
    if decision.confidence < policy.accept_threshold:
        return result("fallback", "below_threshold")
    return result("accept", "accepted")


REASON_TEXT: dict[ReasonCode, dict[ReasonLanguage, str]] = {
    ReasonCode.NEWSLETTER: {"en": "newsletter", "zh": "订阅资讯"},
    ReasonCode.PROMOTION: {"en": "promotion", "zh": "推广邮件"},
    ReasonCode.CI_NOTIFICATION: {"en": "CI notification", "zh": "CI 通知"},
    ReasonCode.TOOL_NOTIFICATION: {"en": "tool notification", "zh": "工具通知"},
    ReasonCode.RECEIPT: {"en": "receipt", "zh": "收据/账单"},
    ReasonCode.STATUS_UPDATE: {"en": "status update FYI", "zh": "状态更新"},
    ReasonCode.CALENDAR_UPDATE: {"en": "calendar update", "zh": "日程变更"},
    ReasonCode.MEETING_REQUEST: {"en": "meeting request", "zh": "会议邀约"},
    ReasonCode.DIRECT_QUESTION: {"en": "direct question", "zh": "直接提问"},
    ReasonCode.ACTION_REQUEST: {"en": "action requested", "zh": "需要你处理"},
    ReasonCode.FOLLOW_UP: {"en": "follow-up", "zh": "跟进催复"},
    ReasonCode.URGENT_ISSUE: {"en": "urgent issue", "zh": "紧急问题"},
    ReasonCode.OTHER: {"en": "other", "zh": "其他"},
}


def reason_text(code: ReasonCode, language: ReasonLanguage = "en") -> str:
    return REASON_TEXT[code][language]
