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
    reason: str
    reason_code: str | None = None
    confidence: float | None = None
    important: bool | None = None
    # What the provider proposed, kept for evaluation. The agent only ever suggests
    # mark_read; what the user sees is which bucket the item landed in.
    proposed_action: TriageAction | None = None
    gate_rule: str | None = None


class ProviderStats(BaseModel):
    provider: str | None = None
    model: str | None = None
    requests: int = 0
    failed_requests: int = 0
    latency_ms: float = 0.0
    input_tokens: int = 0
    output_tokens: int = 0


class TriagePlan(BaseModel):
    needs_reply: list[PlanItem] = Field(default_factory=list)
    important: list[PlanItem] = Field(default_factory=list)
    bulk: list[PlanItem] = Field(default_factory=list)
    unresolved: list[PlanItem] = Field(default_factory=list)
    provider: ProviderStats = Field(default_factory=ProviderStats)
    total_latency_ms: float = 0.0

    @property
    def item_count(self) -> int:
        return len(self.needs_reply) + len(self.important) + len(self.bulk) + len(self.unresolved)

    @property
    def attention_count(self) -> int:
        return len(self.needs_reply) + len(self.important)


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


def _safe_to_dismiss(decision, policy: GatePolicy) -> bool:
    if decision.important:
        return False
    if decision.important is None:
        # Provider does not answer the importance axis; its action stands in for it.
        return True
    return (decision.importance_confidence or 0.0) >= policy.importance_threshold


def build_triage_plan(
    items: list[TriageItem],
    provider: DecisionProvider,
    *,
    language: ReasonLanguage = "en",
    policy: GatePolicy | None = None,
) -> TriagePlan:
    """Split unread mail into what the user should look at and what is safe to dismiss.

    Only mark_read is ever proposed. Leaving mail where it is costs the user
    nothing, while archiving or deleting it destroys the unread signal they rely
    on to find things again. Anything the provider is unsure about is left
    untouched rather than guessed at, which makes an uncertain answer cheap
    instead of risky — and removes any need for a second opinion."""
    started = time.monotonic()
    policy = policy or GatePolicy()
    plan = TriagePlan()
    if not items:
        plan.total_latency_ms = round((time.monotonic() - started) * 1000, 2)
        return plan

    batch = provider.decide_triage(items)
    plan.provider = ProviderStats(
        provider=batch.provider,
        model=batch.model,
        requests=batch.requests,
        failed_requests=batch.failed_requests,
        latency_ms=batch.latency_ms,
        input_tokens=batch.usage.input_tokens,
        output_tokens=batch.usage.output_tokens,
    )

    for decision in batch.decisions:
        gate = apply_gate(decision, policy)
        text = reason_text(decision.reason_code, language) if decision.reason_code else None
        item = PlanItem(
            email_id=decision.item_id,
            reason=text or REVIEW_TEXT[language],
            reason_code=decision.reason_code.value if decision.reason_code else None,
            confidence=decision.confidence,
            important=decision.important,
            proposed_action=decision.action,
        )
        # Only mark_read changes anything, so the gate guards that alone. Which
        # bucket an item is shown in is presentation, and surfacing is always safe.
        if gate.outcome == "accept" and decision.action == "needs_reply":
            plan.needs_reply.append(item)
        elif decision.important:
            plan.important.append(item)
        elif gate.outcome != "accept":
            plan.unresolved.append(item.model_copy(update={"gate_rule": gate.rule}))
        elif _safe_to_dismiss(decision, policy):
            plan.bulk.append(item)
        else:
            plan.unresolved.append(item.model_copy(update={"gate_rule": "importance_unclear"}))

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


def plan_summary(plan: TriagePlan) -> str:
    return (
        f"PLAN READY: {len(plan.needs_reply)} needing a reply, {len(plan.important)} worth "
        f"a look, {len(plan.bulk)} safe to mark read, {len(plan.unresolved)} left untouched. "
        f"The card is in front of the user and they are acting on it directly. Reply ONE "
        f"line telling them the plan is on the card, then STOP. CRITICAL: match the language "
        f"of the user's ORIGINAL request. Do NOT enumerate the buckets, do NOT list items, "
        f"do NOT offer to draft."
    )


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
    """Sort the user's unread email into what needs them and what can be dismissed.

    One call does the whole batch: it reads the unread mail, sorts every message
    and renders the review card. Do NOT list or classify the emails yourself and
    do NOT pass per-email arguments. The card only offers to mark low-signal mail
    read — nothing is archived or deleted, and the user acts per row. Give a single
    one-line acknowledgement afterwards and STOP.
    """
    plan = build_triage_plan(
        _unread_items(limit), build_provider(), language="zh" if language == "zh" else "en"
    )
    return plan_summary(plan)
