from typing import Literal

from langchain_core.tools import tool
from pydantic import BaseModel, Field

from app.agent.execution_context import (
    current_profile_id,
    current_run_id,
    current_thread_id,
    current_tool_call_id,
)
from app.services.database import DatabaseService
from app.services.memory_candidate_store import MemoryCandidateStore
from app.services.tool_execution_store import ToolExecutionStore


CandidateMemoryType = Literal["preference", "rule", "constraint"]


class RecordMemoryCandidateInput(BaseModel):
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
                "memory_type": memory_type,
                "workflow_scope": "drafting",
                "contact_scope": contact_scope,
                "key": key,
                "value": value,
                "source_ref": thread_id,
            },
            execute=lambda session: {
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
            },
            run_id=run_id,
            tool_call_id=tool_call_id,
        )
        return result
    finally:
        database.engine.dispose()


def _safe_candidate_result(candidate: dict) -> dict:
    return {
        "id": candidate["id"],
        "memory_type": candidate["memory_type"],
        "workflow_scope": candidate["workflow_scope"],
        "has_contact_scope": bool(candidate["contact_scope"]),
        "status": candidate["status"],
        "evidence_count": candidate["evidence_count"],
    }
