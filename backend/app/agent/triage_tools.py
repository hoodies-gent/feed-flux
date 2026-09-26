import time
from typing import Literal

from langchain_core.tools import tool
from pydantic import BaseModel, Field

from app.decisions.contract import DecisionProvider, TriageAction, TriageItem
from app.decisions.policy import GatePolicy, ReasonLanguage, apply_gate, reason_text
from app.decisions.settings import decision_settings
from app.services.database import DatabaseService


class PlanItem(BaseModel):
    email_id: str
    action: TriageAction | None = None
    reason: str
    reason_code: str | None = None
    confidence: float | None = None
    important: bool | None = None


class ReviewItem(PlanItem):
    gate_rule: str


class TriagePlan(BaseModel):
    bulk: list[PlanItem] = Field(default_factory=list)
    needs_reply: list[PlanItem] = Field(default_factory=list)
    review: list[ReviewItem] = Field(default_factory=list)
    provider: str | None = None
    model: str | None = None
    requests: int = 0
    failed_requests: int = 0
    provider_latency_ms: float = 0.0
    total_latency_ms: float = 0.0
    input_tokens: int = 0
    output_tokens: int = 0

    @property
    def item_count(self) -> int:
        return len(self.bulk) + len(self.needs_reply) + len(self.review)


REVIEW_TEXT: dict[ReasonLanguage, str] = {"en": "needs your call", "zh": "待你判断"}


def _unread_items(limit: int) -> list[TriageItem]:
    rows = DatabaseService().get_unread_emails(limit=limit)
    return [
        TriageItem(
            item_id=row["id"],
            subject=row["subject"] or "",
            sender=row["sender"] or row["sender_email"],
            sender_email=row["sender_email"],
            body_preview=row["body_preview"],
        )
        for row in rows
    ]


def build_triage_plan(
    items: list[TriageItem],
    provider: DecisionProvider,
    *,
    language: ReasonLanguage = "en",
    policy: GatePolicy | None = None,
) -> TriagePlan:
    """Turn a batch of unread mail into a review card plan. The provider only
    proposes; the gate decides whether a proposal is shown as a suggestion or
    handed to the user, and reason codes become text here rather than in the model."""
    started = time.monotonic()
    policy = policy or GatePolicy()
    plan = TriagePlan()
    if not items:
        plan.total_latency_ms = round((time.monotonic() - started) * 1000, 2)
        return plan

    batch = provider.decide_triage(items)
    plan.provider = batch.provider
    plan.model = batch.model
    plan.requests = batch.requests
    plan.failed_requests = batch.failed_requests
    plan.provider_latency_ms = batch.latency_ms
    plan.input_tokens = batch.usage.input_tokens
    plan.output_tokens = batch.usage.output_tokens

    for decision in batch.decisions:
        gate = apply_gate(decision, policy)
        text = reason_text(decision.reason_code, language) if decision.reason_code else None
        common = {
            "email_id": decision.item_id,
            "reason_code": decision.reason_code.value if decision.reason_code else None,
            "confidence": decision.confidence,
            "important": decision.important,
        }
        if gate.outcome != "accept":
            plan.review.append(
                ReviewItem(
                    **common,
                    action=decision.action,
                    reason=text or REVIEW_TEXT[language],
                    gate_rule=gate.rule,
                )
            )
        elif decision.action == "needs_reply":
            plan.needs_reply.append(PlanItem(**common, reason=text or REVIEW_TEXT[language]))
        else:
            plan.bulk.append(
                PlanItem(**common, action=decision.action, reason=text or REVIEW_TEXT[language])
            )

    plan.total_latency_ms = round((time.monotonic() - started) * 1000, 2)
    return plan


def build_provider(settings=None) -> DecisionProvider:
    settings = settings or decision_settings()
    if settings.provider == "jev":
        from app.decisions.jev import JevDecisionProvider

        return JevDecisionProvider(settings)
    if settings.provider == "fake":
        from app.decisions.fake import FakeDecisionProvider

        return FakeDecisionProvider()
    from app.decisions.llm import LlmDecisionProvider

    return LlmDecisionProvider(settings=settings)


class TriageUnreadInput(BaseModel):
    limit: int = Field(
        default=20,
        description=(
            "Maximum number of unread emails to triage (hard cap 50). The user names a "
            "number ('top 5', '10 封') → use it; 'all' / '所有' → 50; unspecified → 20."
        ),
    )
    language: Literal["en", "zh"] = Field(
        default="en",
        description="Language of the user's request, so the card reads in their language.",
    )


@tool("triage_unread", args_schema=TriageUnreadInput)
def triage_unread(limit: int = 20, language: str = "en") -> str:
    """Classify the user's unread email and put the result on the review card.

    One call does the whole batch: it reads the unread mail, classifies every
    message and renders the card. Do NOT list or classify the emails yourself,
    and do NOT pass per-email arguments — this tool decides and the user acts
    on the card per row. Give a single one-line acknowledgement afterwards and STOP.
    """
    plan = build_triage_plan(
        _unread_items(limit), build_provider(), language="zh" if language == "zh" else "en"
    )
    return (
        f"PLAN READY: {len(plan.bulk)} bulk items + {len(plan.needs_reply)} needs-reply "
        f"+ {len(plan.review)} for review. The plan is now on the review card and the "
        f"user is acting on it directly. Reply ONE line telling them the plan is on the "
        f"card, then STOP. CRITICAL: match the language of the user's ORIGINAL request. "
        f"Do NOT enumerate the buckets, do NOT list items, do NOT offer to draft."
    )
