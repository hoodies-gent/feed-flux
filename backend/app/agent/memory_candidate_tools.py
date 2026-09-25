from typing import Literal

from langchain_core.tools import tool
from pydantic import BaseModel, Field

from app.agent.execution_context import (
    current_profile_id,
    current_run_id,
    current_thread_id,
    current_tool_call_id,
)
from app.models.tool_execution import ToolExecution
from app.services.database import DatabaseService
from app.services.memory_candidate_store import MemoryCandidateStore
from app.services.tool_execution_store import ToolExecutionStore


CandidateMemoryType = Literal["preference", "rule", "constraint"]


class RecordMemoryCandidateInput(BaseModel):
    draft_id: int = Field(
        description="ID of the draft successfully revised earlier in this Agent run."
    )
    memory_type: CandidateMemoryType = Field(
        description="Reusable drafting preference category inferred from a user correction."
    )
    contact_scope: str | None = Field(
        default=None,
        description="Contact email this drafting preference applies to; omit for all contacts.",
    )
    key: str = Field(description="Stable canonical name for the possible preference.")
    value: str = Field(description="Concise reusable drafting instruction.")


@tool("record_memory_candidate", args_schema=RecordMemoryCandidateInput)
def record_memory_candidate(
    draft_id: int,
    memory_type: CandidateMemoryType,
    key: str,
    value: str,
    contact_scope: str | None = None,
) -> dict:
    """Record inert evidence for a possible reusable drafting preference."""
    run_id = current_run_id.get()
    tool_call_id = current_tool_call_id.get()
    thread_id = current_thread_id.get()
    if run_id is None or tool_call_id is None or thread_id == "unknown":
        raise RuntimeError(
            "record_memory_candidate requires agent run, tool call, and thread context"
        )

    profile_id = current_profile_id.get()
    database = DatabaseService()
    try:
        store = MemoryCandidateStore(database)
        result, _ = ToolExecutionStore(database).execute_once(
            idempotency_key=f"{run_id}:{tool_call_id}",
            operation="record_memory_candidate",
            request_payload={
                "profile_id": profile_id,
                "draft_id": draft_id,
                "memory_type": memory_type,
                "workflow_scope": "drafting",
                "contact_scope": contact_scope,
                "key": key,
                "value": value,
                "source_ref": thread_id,
            },
            execute=lambda session: _record_with_revision_evidence(
                session,
                store=store,
                run_id=run_id,
                draft_id=draft_id,
                profile_id=profile_id,
                memory_type=memory_type,
                contact_scope=contact_scope,
                key=key,
                value=value,
                thread_id=thread_id,
            ),
            run_id=run_id,
            tool_call_id=tool_call_id,
        )
        return result
    finally:
        database.engine.dispose()


def _record_with_revision_evidence(
    session,
    *,
    store: MemoryCandidateStore,
    run_id: str,
    draft_id: int,
    profile_id: str,
    memory_type: CandidateMemoryType,
    contact_scope: str | None,
    key: str,
    value: str,
    thread_id: str,
) -> dict:
    revisions = (
        session.query(ToolExecution)
        .filter(
            ToolExecution.run_id == run_id,
            ToolExecution.operation.in_(("apply_draft_patch", "save_reply_draft")),
        )
        .all()
    )
    has_revision = any(
        isinstance(execution.result, dict)
        and execution.result.get("draft_id") == draft_id
        and (
            execution.operation == "apply_draft_patch"
            or execution.result.get("marker") == "DRAFT UPDATED"
        )
        for execution in revisions
    )
    if not has_revision:
        raise RuntimeError(
            "record_memory_candidate requires a successful draft revision "
            "for the same run and draft"
        )
    return {
        "candidate": _safe_candidate_result(
            store.record_candidate_in_session(
                session,
                profile_id=profile_id,
                memory_type=memory_type,
                workflow_scope="drafting",
                contact_scope=contact_scope,
                key=key,
                value=value,
                source="agent_correction",
                source_ref=thread_id,
            )
        )
    }


def _safe_candidate_result(candidate: dict) -> dict:
    return {
        "id": candidate["id"],
        "memory_type": candidate["memory_type"],
        "workflow_scope": candidate["workflow_scope"],
        "has_contact_scope": bool(candidate["contact_scope"]),
        "status": candidate["status"],
        "evidence_count": candidate["evidence_count"],
    }
