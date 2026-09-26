from typing import Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from app.core.profile import LOCAL_PROFILE_ID
from app.services.database import DatabaseService
from app.services.memory_candidate_store import MemoryCandidateStore
from app.services.semantic_memory_store import SemanticMemoryStore


router = APIRouter(prefix="/api/memories", tags=["semantic-memory"])
db = DatabaseService()


class MemoryUpdateRequest(BaseModel):
    value: str


def _memory_mutation_error(error: KeyError | ValueError) -> HTTPException:
    if isinstance(error, KeyError):
        detail = error.args[0] if error.args else "Semantic memory not found"
        return HTTPException(status_code=404, detail=detail)
    return HTTPException(status_code=409, detail=str(error))


@router.get("")
async def list_semantic_memories(
    memory_type: Optional[str] = None,
    workflow_scope: Optional[str] = None,
    contact_scope: Optional[str] = None,
):
    memories = SemanticMemoryStore(db).list_memories(profile_id=LOCAL_PROFILE_ID)
    if memory_type is not None:
        normalized_type = memory_type.strip().casefold()
        memories = [item for item in memories if item["memory_type"] == normalized_type]
    if workflow_scope is not None:
        normalized_workflow = workflow_scope.strip().casefold()
        memories = [
            item
            for item in memories
            if item["workflow_scope"] == normalized_workflow
        ]
    if contact_scope is not None:
        normalized_contact = contact_scope.strip().casefold() or None
        memories = [
            item for item in memories if item["contact_scope"] == normalized_contact
        ]
    return {"memories": memories, "count": len(memories)}


@router.get("/candidates")
async def list_memory_candidates():
    candidates = MemoryCandidateStore(db).list_candidates(
        profile_id=LOCAL_PROFILE_ID,
        status="suggested",
    )
    return {"candidates": candidates, "count": len(candidates)}


@router.post("/candidates/{candidate_id}/accept")
async def accept_memory_candidate(candidate_id: int):
    try:
        return MemoryCandidateStore(db).confirm(
            profile_id=LOCAL_PROFILE_ID,
            candidate_id=candidate_id,
        )
    except (KeyError, ValueError) as error:
        raise _memory_mutation_error(error)


@router.post("/candidates/{candidate_id}/dismiss")
async def dismiss_memory_candidate(candidate_id: int):
    try:
        return MemoryCandidateStore(db).reject(
            profile_id=LOCAL_PROFILE_ID,
            candidate_id=candidate_id,
        )
    except (KeyError, ValueError) as error:
        raise _memory_mutation_error(error)


@router.patch("/{memory_id}")
async def update_semantic_memory(memory_id: int, request: MemoryUpdateRequest):
    try:
        return SemanticMemoryStore(db).update(
            profile_id=LOCAL_PROFILE_ID,
            memory_id=memory_id,
            value=request.value,
            source="management_ui",
            source_ref="memory-management-api",
        )
    except (KeyError, ValueError) as error:
        raise _memory_mutation_error(error)


@router.post("/{memory_id}/disable")
async def disable_semantic_memory(memory_id: int):
    try:
        return SemanticMemoryStore(db).disable(
            profile_id=LOCAL_PROFILE_ID,
            memory_id=memory_id,
        )
    except (KeyError, ValueError) as error:
        raise _memory_mutation_error(error)


@router.post("/{memory_id}/enable")
async def enable_semantic_memory(memory_id: int):
    try:
        return SemanticMemoryStore(db).enable(
            profile_id=LOCAL_PROFILE_ID,
            memory_id=memory_id,
        )
    except (KeyError, ValueError) as error:
        raise _memory_mutation_error(error)


@router.delete("/{memory_id}")
async def delete_semantic_memory(memory_id: int):
    try:
        return SemanticMemoryStore(db).forget(
            profile_id=LOCAL_PROFILE_ID,
            memory_id=memory_id,
        )
    except (KeyError, ValueError) as error:
        raise _memory_mutation_error(error)


@router.delete("")
async def clear_semantic_memories(
    memory_type: Optional[str] = None,
    workflow_scope: Optional[str] = None,
    contact_scope: Optional[str] = None,
):
    reset_kwargs = {
        "profile_id": LOCAL_PROFILE_ID,
        "memory_type": memory_type,
        "workflow_scope": workflow_scope,
    }
    if contact_scope is not None:
        reset_kwargs["contact_scope"] = contact_scope
    try:
        return SemanticMemoryStore(db).reset(**reset_kwargs)
    except ValueError as error:
        raise _memory_mutation_error(error)
