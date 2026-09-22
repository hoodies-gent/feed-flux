from enum import StrEnum
from typing import Any
from uuid import uuid4

from app.models.agent_run import AgentRun, AgentRunEvent
from app.services.database import DatabaseService


class RunStatus(StrEnum):
    QUEUED = "queued"
    RUNNING = "running"
    INTERRUPTED = "interrupted"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


class ErrorCategory(StrEnum):
    TRANSIENT = "transient"
    LLM_TOOL_REPAIRABLE = "llm_tool_repairable"
    USER_REPAIRABLE = "user_repairable"
    TERMINAL = "terminal"


_ALLOWED_TRANSITIONS = {
    RunStatus.QUEUED: {RunStatus.RUNNING, RunStatus.FAILED, RunStatus.CANCELLED},
    RunStatus.RUNNING: {
        RunStatus.INTERRUPTED,
        RunStatus.COMPLETED,
        RunStatus.FAILED,
        RunStatus.CANCELLED,
    },
    RunStatus.INTERRUPTED: {RunStatus.RUNNING, RunStatus.FAILED, RunStatus.CANCELLED},
    RunStatus.COMPLETED: set(),
    RunStatus.FAILED: set(),
    RunStatus.CANCELLED: set(),
}


class AgentRunStore:
    def __init__(self, database: DatabaseService):
        self.database = database

    def create_run(self, thread_id: str, provider: str | None = None) -> dict:
        session = self.database.Session()
        try:
            run = AgentRun(
                run_id=str(uuid4()),
                thread_id=thread_id,
                status=RunStatus.QUEUED.value,
                provider=provider,
            )
            session.add(run)
            session.add(
                AgentRunEvent(
                    run_id=run.run_id,
                    event_type="status",
                    status=RunStatus.QUEUED.value,
                    provider=provider,
                )
            )
            session.commit()
            session.refresh(run)
            return self._run_to_dict(run)
        except Exception:
            session.rollback()
            raise
        finally:
            session.close()

    def get_run(self, run_id: str) -> dict:
        session = self.database.Session()
        try:
            run = session.query(AgentRun).filter_by(run_id=run_id).first()
            if run is None:
                raise KeyError(f"agent run not found: {run_id}")
            return self._run_to_dict(run)
        finally:
            session.close()

    def get_interrupted_run(self, thread_id: str) -> dict:
        session = self.database.Session()
        try:
            run = (
                session.query(AgentRun)
                .filter_by(thread_id=thread_id, status=RunStatus.INTERRUPTED.value)
                .order_by(AgentRun.updated_at.desc(), AgentRun.created_at.desc())
                .first()
            )
            if run is None:
                raise KeyError(f"interrupted agent run not found for thread: {thread_id}")
            return self._run_to_dict(run)
        finally:
            session.close()

    def transition_run(
        self,
        run_id: str,
        status: RunStatus | str,
        *,
        outcome: dict[str, Any] | None = None,
        error_category: ErrorCategory | str | None = None,
    ) -> dict:
        target = RunStatus(status)
        category = ErrorCategory(error_category).value if error_category is not None else None
        session = self.database.Session()
        try:
            run = session.query(AgentRun).filter_by(run_id=run_id).first()
            if run is None:
                raise KeyError(f"agent run not found: {run_id}")

            current = RunStatus(run.status)
            if target not in _ALLOWED_TRANSITIONS[current]:
                raise ValueError(f"invalid agent run transition: {current.value} -> {target.value}")

            run.status = target.value
            if outcome is not None:
                run.outcome = outcome
            if category is not None:
                run.error_category = category
            session.add(
                AgentRunEvent(
                    run_id=run_id,
                    event_type="status",
                    status=target.value,
                    outcome=outcome,
                    error_category=category,
                )
            )
            session.commit()
            session.refresh(run)
            return self._run_to_dict(run)
        except Exception:
            session.rollback()
            raise
        finally:
            session.close()

    def append_event(
        self,
        run_id: str,
        *,
        event_type: str,
        provider: str | None = None,
        tool_name: str | None = None,
        tool_call_id: str | None = None,
        outcome: dict[str, Any] | None = None,
        error_category: ErrorCategory | str | None = None,
    ) -> dict:
        category = ErrorCategory(error_category).value if error_category is not None else None
        session = self.database.Session()
        try:
            if session.query(AgentRun.run_id).filter_by(run_id=run_id).first() is None:
                raise KeyError(f"agent run not found: {run_id}")
            event = AgentRunEvent(
                run_id=run_id,
                event_type=event_type,
                provider=provider,
                tool_name=tool_name,
                tool_call_id=tool_call_id,
                outcome=outcome,
                error_category=category,
            )
            session.add(event)
            session.commit()
            session.refresh(event)
            return self._event_to_dict(event)
        except Exception:
            session.rollback()
            raise
        finally:
            session.close()

    def list_events(self, run_id: str) -> list[dict]:
        session = self.database.Session()
        try:
            events = (
                session.query(AgentRunEvent)
                .filter_by(run_id=run_id)
                .order_by(AgentRunEvent.id)
                .all()
            )
            return [self._event_to_dict(event) for event in events]
        finally:
            session.close()

    def get_usage_summary(self, run_id: str) -> dict[str, int | float | None]:
        outcomes = [
            event["outcome"] or {}
            for event in self.list_events(run_id)
            if event["event_type"] == "provider_usage"
        ]
        usage_items = [outcome.get("usage") or {} for outcome in outcomes]
        costs = [outcome.get("estimated_cost_usd") for outcome in outcomes]
        estimated_cost = (
            round(sum(costs), 10)
            if costs and all(cost is not None for cost in costs)
            else None
        )
        return {
            "input_tokens": sum(int(item.get("input_tokens", 0)) for item in usage_items),
            "cached_input_tokens": sum(
                int(item.get("cached_input_tokens", 0)) for item in usage_items
            ),
            "output_tokens": sum(
                int(item.get("output_tokens", 0)) for item in usage_items
            ),
            "total_tokens": sum(
                int(item.get("total_tokens", 0)) for item in usage_items
            ),
            "estimated_cost_usd": estimated_cost,
        }

    @staticmethod
    def _run_to_dict(run: AgentRun) -> dict:
        return {
            "run_id": run.run_id,
            "thread_id": run.thread_id,
            "status": run.status,
            "provider": run.provider,
            "outcome": run.outcome,
            "error_category": run.error_category,
            "created_at": run.created_at,
            "updated_at": run.updated_at,
        }

    @staticmethod
    def _event_to_dict(event: AgentRunEvent) -> dict:
        return {
            "id": event.id,
            "run_id": event.run_id,
            "event_type": event.event_type,
            "status": event.status,
            "provider": event.provider,
            "tool_name": event.tool_name,
            "tool_call_id": event.tool_call_id,
            "outcome": event.outcome,
            "error_category": event.error_category,
            "created_at": event.created_at,
        }
