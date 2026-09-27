import logging
from dataclasses import replace

from app.agent.execution_context import current_run_id, current_tool_call_id
from app.decisions.contract import TriageDecisionBatch, TriageItem
from app.decisions.policy import GatePolicy, apply_gate
from app.decisions.settings import DecisionSettings, decision_settings
from app.services.agent_run_store import AgentRunStore
from app.services.database import DatabaseService


logger = logging.getLogger(__name__)

SHADOW_EVENT_TYPE = "decision_shadow"


def observe_triage_shadow(actions: list, needs_reply: list) -> None:
    """Record what the decision provider would have proposed. Shadow mode never
    changes the plan, the tool result or what the user sees, so every failure
    here is swallowed."""
    try:
        _observe(actions, needs_reply)
    except Exception:
        logger.warning("triage shadow observation skipped", exc_info=True)


def _field(item, name: str):
    # LangChain coerces tool args into the args_schema models, so items arrive as
    # TriageActionItem/NeedsReplyItem here and as plain dicts from other callers.
    if isinstance(item, dict):
        return item.get(name)
    return getattr(item, name, None)


def _observe(actions: list, needs_reply: list) -> None:
    settings = decision_settings()
    if settings.mode != "shadow":
        return

    agent_actions: dict[str, str] = {}
    for item in actions or []:
        email_id = _field(item, "email_id")
        if email_id:
            agent_actions.setdefault(email_id, _field(item, "action"))
    for item in needs_reply or []:
        email_id = _field(item, "email_id")
        if email_id:
            agent_actions.setdefault(email_id, "needs_reply")
    if not agent_actions:
        return

    items = _triage_items(list(agent_actions))
    if not items:
        return

    provider = _provider(settings)
    if provider is None:
        logger.warning("triage shadow skipped: unknown provider %s", settings.provider)
        return

    batch = provider.decide_triage(items)
    _record(batch, agent_actions)


def _triage_items(email_ids: list[str]) -> list[TriageItem]:
    from app.models.email import Email

    session = DatabaseService().Session()
    try:
        rows = session.query(Email).filter(Email.id.in_(email_ids)).all()
    finally:
        session.close()
    by_id = {
        row.id: TriageItem(
            item_id=row.id,
            subject=row.subject or "",
            sender=row.sender_name or row.sender_email,
            sender_email=row.sender_email,
            body_preview=row.body_preview,
        )
        for row in rows
    }
    return [by_id[email_id] for email_id in email_ids if email_id in by_id]


def _provider(settings: DecisionSettings):
    if settings.provider == "jev":
        from app.decisions.jev import JevDecisionProvider

        # A shadow observation must never make the user wait: one attempt, short timeout.
        return JevDecisionProvider(
            replace(settings, timeout_seconds=settings.shadow_timeout_seconds, max_attempts=1)
        )
    if settings.provider == "llm":
        from app.decisions.llm import LlmDecisionProvider

        return LlmDecisionProvider(settings=settings)
    if settings.provider == "fake":
        from app.decisions.fake import FakeDecisionProvider

        return FakeDecisionProvider()
    return None


def _record(batch: TriageDecisionBatch, agent_actions: dict[str, str]) -> None:
    policy = GatePolicy()
    items = []
    for decision in batch.decisions:
        gate = apply_gate(decision, policy)
        agent_action = agent_actions.get(decision.item_id)
        items.append(
            {
                "email_id": decision.item_id,
                "agent_action": agent_action,
                "provider_action": decision.action,
                "provider_status": decision.status,
                "reason_code": decision.reason_code.value if decision.reason_code else None,
                "confidence": round(decision.confidence, 4) if decision.confidence is not None else None,
                "important": decision.important,
                "importance_confidence": (
                    round(decision.importance_confidence, 4)
                    if decision.importance_confidence is not None
                    else None
                ),
                "gate_outcome": gate.outcome,
                "gate_rule": gate.rule,
                "agrees_with_agent": (
                    decision.action == agent_action if decision.status == "ok" else None
                ),
                "error_category": decision.error_category,
            }
        )

    outcome = {
        "schema_version": 1,
        "kind": SHADOW_EVENT_TYPE,
        "provider": batch.provider,
        "model": batch.model,
        "items": items,
        "summary": {
            "items": len(items),
            "agreements": sum(1 for item in items if item["agrees_with_agent"]),
            "accepted": sum(1 for item in items if item["gate_outcome"] == "accept"),
            "fallback": sum(1 for item in items if item["gate_outcome"] == "fallback"),
            "review": sum(1 for item in items if item["gate_outcome"] == "review"),
            "requests": batch.requests,
            "failed_requests": batch.failed_requests,
            "latency_ms": batch.latency_ms,
            "input_tokens": batch.usage.input_tokens,
            "output_tokens": batch.usage.output_tokens,
        },
    }

    run_id = current_run_id.get()
    if run_id is None:
        logger.info("triage shadow observed outside a run; not persisted")
        return
    AgentRunStore(DatabaseService()).append_event(
        run_id,
        event_type=SHADOW_EVENT_TYPE,
        provider=batch.provider,
        tool_name="apply_triage_batch",
        tool_call_id=current_tool_call_id.get(),
        outcome=outcome,
    )
