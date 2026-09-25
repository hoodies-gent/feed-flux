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
from app.services.semantic_memory_store import SemanticMemoryStore
from app.services.tool_execution_store import ToolExecutionStore


MemoryType = Literal["preference", "fact", "rule", "constraint"]


class RememberMemoryInput(BaseModel):
    memory_type: MemoryType = Field(
        description="Stable user information category."
    )
    workflow_scope: str = Field(
        description="Workflow where this memory applies, such as drafting or triage."
    )
    contact_scope: str | None = Field(
        default=None,
        description="Contact email this memory applies to; omit for all contacts.",
    )
    key: str = Field(description="Stable preference or fact name.")
    value: str = Field(description="Confirmed value explicitly provided by the user.")


@tool("remember_memory", args_schema=RememberMemoryInput)
def remember_memory(
    memory_type: MemoryType,
    workflow_scope: str,
    key: str,
    value: str,
    contact_scope: str | None = None,
) -> dict:
    """Remember a stable user preference or fact only after an explicit user request."""
    run_id, tool_call_id = _write_context("remember_memory")
    profile_id = current_profile_id.get()
    database = DatabaseService()
    try:
        store = SemanticMemoryStore(database)
        result, _ = ToolExecutionStore(database).execute_once(
            idempotency_key=f"{run_id}:{tool_call_id}",
            operation="remember_memory",
            request_payload={
                "profile_id": profile_id,
                "memory_type": memory_type,
                "workflow_scope": workflow_scope,
                "contact_scope": contact_scope,
                "key": key,
                "value": value,
            },
            execute=lambda session: {
                "memory": _safe_memory_result(
                    store.remember_in_session(
                        session,
                        profile_id=profile_id,
                        memory_type=memory_type,
                        workflow_scope=workflow_scope,
                        contact_scope=contact_scope,
                        key=key,
                        value=value,
                        source="explicit_user",
                        source_ref=current_thread_id.get(),
                    )
                )
            },
            run_id=run_id,
            tool_call_id=tool_call_id,
        )
        return result
    finally:
        database.engine.dispose()


class ListMemoriesInput(BaseModel):
    memory_type: MemoryType | None = Field(default=None)
    workflow_scope: str | None = Field(default=None)
    contact_scope: str | None = Field(default=None)
    limit: int = Field(default=10, ge=1, le=10)
    cursor: int | None = Field(default=None, ge=1)


@tool("list_memories", args_schema=ListMemoriesInput)
def list_memories(
    memory_type: MemoryType | None = None,
    workflow_scope: str | None = None,
    contact_scope: str | None = None,
    limit: int = 10,
    cursor: int | None = None,
) -> dict:
    """List one approved, filtered page of the user's current confirmed memories."""
    database = DatabaseService()
    try:
        return SemanticMemoryStore(database).list_memories_page(
            profile_id=current_profile_id.get(),
            memory_type=memory_type,
            workflow_scope=workflow_scope,
            contact_scope=contact_scope,
            limit=limit,
            cursor=cursor,
        )
    finally:
        database.engine.dispose()


class UpdateMemoryInput(BaseModel):
    memory_id: int = Field(description="ID of the current memory to update.")
    value: str = Field(description="New value explicitly confirmed by the user.")


@tool("update_memory", args_schema=UpdateMemoryInput)
def update_memory(memory_id: int, value: str) -> dict:
    """Update only the value of a current memory after explicit user confirmation."""
    run_id, tool_call_id = _write_context("update_memory")
    profile_id = current_profile_id.get()
    database = DatabaseService()
    try:
        store = SemanticMemoryStore(database)
        result, _ = ToolExecutionStore(database).execute_once(
            idempotency_key=f"{run_id}:{tool_call_id}",
            operation="update_memory",
            request_payload={
                "profile_id": profile_id,
                "memory_id": memory_id,
                "value": value,
            },
            execute=lambda session: {
                "memory": _safe_memory_result(
                    store.update_in_session(
                        session,
                        profile_id=profile_id,
                        memory_id=memory_id,
                        value=value,
                        source="explicit_user",
                        source_ref=current_thread_id.get(),
                    )
                )
            },
            run_id=run_id,
            tool_call_id=tool_call_id,
        )
        return result
    finally:
        database.engine.dispose()


class ForgetMemoryInput(BaseModel):
    memory_id: int = Field(description="ID of the memory lineage to forget.")


@tool("forget_memory", args_schema=ForgetMemoryInput)
def forget_memory(memory_id: int) -> dict:
    """Permanently scrub a memory's full lineage after explicit user confirmation."""
    run_id, tool_call_id = _write_context("forget_memory")
    profile_id = current_profile_id.get()
    database = DatabaseService()
    try:
        store = SemanticMemoryStore(database)
        result, _ = ToolExecutionStore(database).execute_once(
            idempotency_key=f"{run_id}:{tool_call_id}",
            operation="forget_memory",
            request_payload={"profile_id": profile_id, "memory_id": memory_id},
            execute=lambda session: store.forget_in_session(
                session,
                profile_id=profile_id,
                memory_id=memory_id,
            ),
            run_id=run_id,
            tool_call_id=tool_call_id,
        )
        return result
    finally:
        database.engine.dispose()


class ResetMemoriesInput(BaseModel):
    memory_type: MemoryType | None = Field(default=None)
    workflow_scope: str | None = Field(default=None)
    contact_scope: str | None = Field(default=None)


@tool("reset_memories", args_schema=ResetMemoriesInput)
def reset_memories(
    memory_type: MemoryType | None = None,
    workflow_scope: str | None = None,
    contact_scope: str | None = None,
) -> dict:
    """Permanently scrub matching memories after explicit user confirmation."""
    run_id, tool_call_id = _write_context("reset_memories")
    profile_id = current_profile_id.get()
    database = DatabaseService()
    try:
        store = SemanticMemoryStore(database)
        request_payload = {
            "profile_id": profile_id,
            "memory_type": memory_type,
            "workflow_scope": workflow_scope,
            "contact_scope": contact_scope,
        }
        reset_kwargs = {
            "profile_id": profile_id,
            "memory_type": memory_type,
            "workflow_scope": workflow_scope,
        }
        if contact_scope is not None:
            reset_kwargs["contact_scope"] = contact_scope
        result, _ = ToolExecutionStore(database).execute_once(
            idempotency_key=f"{run_id}:{tool_call_id}",
            operation="reset_memories",
            request_payload=request_payload,
            execute=lambda session: store.reset_in_session(session, **reset_kwargs),
            run_id=run_id,
            tool_call_id=tool_call_id,
        )
        return result
    finally:
        database.engine.dispose()


def _write_context(tool_name: str) -> tuple[str, str]:
    run_id = current_run_id.get()
    tool_call_id = current_tool_call_id.get()
    if run_id is None or tool_call_id is None:
        raise RuntimeError(f"{tool_name} requires an agent run and tool call context")
    return run_id, tool_call_id


def _safe_memory_result(memory: dict) -> dict:
    return {
        "id": memory["id"],
        "lineage_id": memory["lineage_id"],
        "version": memory["version"],
        "supersedes_id": memory["supersedes_id"],
        "profile_id": memory["profile_id"],
        "memory_type": memory["memory_type"],
        "workflow_scope": memory["workflow_scope"],
        "has_contact_scope": bool(memory["contact_scope"]),
        "status": memory["status"],
    }
