from datetime import datetime, timezone
from uuid import uuid4

from sqlalchemy import and_, case, literal, text
from sqlalchemy.orm import Session

from app.models.semantic_memory import (
    SemanticMemory,
    SemanticMemoryCandidate,
    SemanticMemoryCandidateEvidence,
)
from app.services.database import DatabaseService


CURRENT_STATUSES = ("active", "disabled")
_ALL_CONTACT_SCOPES = object()


class SemanticMemoryStore:
    def __init__(self, database: DatabaseService):
        self.database = database

    def remember(
        self,
        *,
        profile_id: str,
        memory_type: str,
        workflow_scope: str,
        contact_scope: str | None,
        key: str,
        value: str,
        source: str,
        source_ref: str | None = None,
    ) -> dict:
        fields = _normalize_fields(
            profile_id=profile_id,
            memory_type=memory_type,
            workflow_scope=workflow_scope,
            contact_scope=contact_scope,
            key=key,
            value=value,
            source=source,
        )
        session = self.database.Session()
        try:
            session.execute(text("BEGIN IMMEDIATE"))
            result = _remember(session, fields=fields, source_ref=source_ref)
            session.commit()
            return result
        except Exception:
            session.rollback()
            raise
        finally:
            session.close()

    def remember_in_session(
        self,
        session: Session,
        *,
        profile_id: str,
        memory_type: str,
        workflow_scope: str,
        contact_scope: str | None,
        key: str,
        value: str,
        source: str,
        source_ref: str | None = None,
    ) -> dict:
        fields = _normalize_fields(
            profile_id=profile_id,
            memory_type=memory_type,
            workflow_scope=workflow_scope,
            contact_scope=contact_scope,
            key=key,
            value=value,
            source=source,
        )
        return _remember(session, fields=fields, source_ref=source_ref)

    def update(
        self,
        *,
        profile_id: str,
        memory_id: int,
        value: str,
        source: str,
        source_ref: str | None = None,
    ) -> dict:
        normalized_value = _required(value, "value")
        normalized_source = _required(source, "source")
        session = self.database.Session()
        try:
            session.execute(text("BEGIN IMMEDIATE"))
            result = _update(
                session,
                profile_id=profile_id,
                memory_id=memory_id,
                value=normalized_value,
                source=normalized_source,
                source_ref=source_ref,
            )
            session.commit()
            return result
        except Exception:
            session.rollback()
            raise
        finally:
            session.close()

    def update_in_session(
        self,
        session: Session,
        *,
        profile_id: str,
        memory_id: int,
        value: str,
        source: str,
        source_ref: str | None = None,
    ) -> dict:
        return _update(
            session,
            profile_id=profile_id,
            memory_id=memory_id,
            value=_required(value, "value"),
            source=_required(source, "source"),
            source_ref=source_ref,
        )

    def disable(self, *, profile_id: str, memory_id: int) -> dict:
        session = self.database.Session()
        try:
            session.execute(text("BEGIN IMMEDIATE"))
            memory = _get_owned_memory(session, profile_id, memory_id)
            if memory.status != "active":
                raise ValueError("only an active memory can be disabled")
            memory.status = "disabled"
            memory.disabled_at = _utc_timestamp()
            session.commit()
            session.refresh(memory)
            return _memory_to_dict(memory)
        except Exception:
            session.rollback()
            raise
        finally:
            session.close()

    def enable(self, *, profile_id: str, memory_id: int) -> dict:
        session = self.database.Session()
        try:
            session.execute(text("BEGIN IMMEDIATE"))
            memory = _get_owned_memory(session, profile_id, memory_id)
            if memory.status != "disabled":
                raise ValueError("only a disabled memory can be enabled")
            memory.status = "active"
            memory.disabled_at = None
            session.commit()
            session.refresh(memory)
            return _memory_to_dict(memory)
        except Exception:
            session.rollback()
            raise
        finally:
            session.close()

    def forget(self, *, profile_id: str, memory_id: int) -> dict:
        session = self.database.Session()
        try:
            session.execute(text("BEGIN IMMEDIATE"))
            result = _forget(session, profile_id=profile_id, memory_id=memory_id)
            session.commit()
            return result
        except Exception:
            session.rollback()
            raise
        finally:
            session.close()

    def forget_in_session(
        self,
        session: Session,
        *,
        profile_id: str,
        memory_id: int,
    ) -> dict:
        return _forget(session, profile_id=profile_id, memory_id=memory_id)

    def reset(
        self,
        *,
        profile_id: str,
        memory_type: str | None = None,
        workflow_scope: str | None = None,
        contact_scope: str | None | object = _ALL_CONTACT_SCOPES,
    ) -> dict:
        normalized_profile = _required(profile_id, "profile_id")
        session = self.database.Session()
        try:
            session.execute(text("BEGIN IMMEDIATE"))
            result = _reset(
                session,
                profile_id=normalized_profile,
                memory_type=memory_type,
                workflow_scope=workflow_scope,
                contact_scope=contact_scope,
            )
            session.commit()
            return result
        except Exception:
            session.rollback()
            raise
        finally:
            session.close()

    def reset_in_session(
        self,
        session: Session,
        *,
        profile_id: str,
        memory_type: str | None = None,
        workflow_scope: str | None = None,
        contact_scope: str | None | object = _ALL_CONTACT_SCOPES,
    ) -> dict:
        return _reset(
            session,
            profile_id=_required(profile_id, "profile_id"),
            memory_type=memory_type,
            workflow_scope=workflow_scope,
            contact_scope=contact_scope,
        )

    def list_memories(
        self,
        *,
        profile_id: str,
        include_history: bool = False,
    ) -> list[dict]:
        normalized_profile = _required(profile_id, "profile_id")
        session = self.database.Session()
        try:
            query = session.query(SemanticMemory).filter(
                SemanticMemory.profile_id == normalized_profile
            )
            if include_history:
                query = query.order_by(SemanticMemory.created_at, SemanticMemory.id)
            else:
                query = query.filter(SemanticMemory.status.in_(CURRENT_STATUSES)).order_by(
                    SemanticMemory.updated_at.desc(), SemanticMemory.id.desc()
                )
            memories = query.all()
            return [_memory_to_dict(memory) for memory in memories]
        finally:
            session.close()

    def list_memories_page(
        self,
        *,
        profile_id: str,
        memory_type: str | None = None,
        workflow_scope: str | None = None,
        contact_scope: str | None = None,
        limit: int = 10,
        cursor: int | None = None,
    ) -> dict:
        if limit < 1 or limit > 50:
            raise ValueError("limit must be between 1 and 50")
        if cursor is not None and cursor < 1:
            raise ValueError("cursor must be a positive memory ID")

        normalized_profile = _required(profile_id, "profile_id")
        session = self.database.Session()
        try:
            query = session.query(SemanticMemory).filter(
                SemanticMemory.profile_id == normalized_profile,
                SemanticMemory.status.in_(CURRENT_STATUSES),
            )
            if memory_type is not None:
                query = query.filter(
                    SemanticMemory.memory_type
                    == _required(memory_type, "memory_type").casefold()
                )
            if workflow_scope is not None:
                query = query.filter(
                    SemanticMemory.workflow_scope
                    == _required(workflow_scope, "workflow_scope").casefold()
                )
            if contact_scope is not None:
                query = query.filter(
                    SemanticMemory.contact_scope == contact_scope.strip().casefold()
                )
            if cursor is not None:
                query = query.filter(SemanticMemory.id < cursor)

            rows = query.order_by(SemanticMemory.id.desc()).limit(limit + 1).all()
            page = rows[:limit]
            return {
                "memories": [_memory_to_dict(memory) for memory in page],
                "count": len(page),
                "next_cursor": page[-1].id if len(rows) > limit else None,
            }
        finally:
            session.close()

    def retrieve_active(
        self,
        *,
        profile_id: str,
        workflow_scope: str | None,
        contact_scope: str | None,
        limit: int = 5,
    ) -> list[dict]:
        if limit <= 0:
            return []
        normalized_profile = _required(profile_id, "profile_id")
        normalized_workflow = (
            workflow_scope.strip().casefold() if workflow_scope else None
        )
        normalized_contact = (contact_scope or "").strip().casefold()
        workflow_candidates = ["global"]
        if normalized_workflow and normalized_workflow != "global":
            workflow_candidates.append(normalized_workflow)
        contact_candidates = [""]
        if normalized_contact:
            contact_candidates.append(normalized_contact)

        rank_cases = []
        if normalized_workflow and normalized_workflow != "global":
            if normalized_contact:
                rank_cases.append(
                    (
                        and_(
                            SemanticMemory.workflow_scope == normalized_workflow,
                            SemanticMemory.contact_scope == normalized_contact,
                        ),
                        0,
                    )
                )
            rank_cases.append(
                (
                    and_(
                        SemanticMemory.workflow_scope == normalized_workflow,
                        SemanticMemory.contact_scope == "",
                    ),
                    1,
                )
            )
        if normalized_contact:
            rank_cases.append(
                (
                    and_(
                        SemanticMemory.workflow_scope == "global",
                        SemanticMemory.contact_scope == normalized_contact,
                    ),
                    2,
                )
            )
        relevance_rank = case(*rank_cases, else_=3) if rank_cases else literal(3)

        session = self.database.Session()
        try:
            memories = (
                session.query(SemanticMemory)
                .filter(
                    SemanticMemory.profile_id == normalized_profile,
                    SemanticMemory.status == "active",
                    SemanticMemory.workflow_scope.in_(workflow_candidates),
                    SemanticMemory.contact_scope.in_(contact_candidates),
                )
                .order_by(
                    relevance_rank,
                    SemanticMemory.updated_at.desc(),
                    SemanticMemory.id.desc(),
                )
                .limit(min(limit, 50))
                .all()
            )
            return [_memory_to_dict(memory) for memory in memories]
        finally:
            session.close()


def _normalize_fields(
    *,
    profile_id: str,
    memory_type: str,
    workflow_scope: str,
    contact_scope: str | None,
    key: str,
    value: str,
    source: str,
) -> dict:
    display_key = " ".join(_required(key, "key").split())
    return {
        "profile_id": _required(profile_id, "profile_id"),
        "memory_type": _required(memory_type, "memory_type").casefold(),
        "workflow_scope": _required(workflow_scope, "workflow_scope").casefold(),
        "contact_scope": (contact_scope or "").strip().casefold(),
        "key": display_key,
        "normalized_key": display_key.casefold(),
        "value": _required(value, "value"),
        "source": _required(source, "source"),
    }


def _remember(
    session: Session,
    *,
    fields: dict,
    source_ref: str | None,
) -> dict:
    current = (
        session.query(SemanticMemory)
        .filter_by(
            profile_id=fields["profile_id"],
            memory_type=fields["memory_type"],
            workflow_scope=fields["workflow_scope"],
            contact_scope=fields["contact_scope"],
            normalized_key=fields["normalized_key"],
        )
        .filter(SemanticMemory.status.in_(CURRENT_STATUSES))
        .one_or_none()
    )
    if (
        current is not None
        and current.status == "active"
        and current.value == fields["value"]
    ):
        return _memory_to_dict(current)

    if current is None:
        lineage_id = str(uuid4())
        version = 1
        supersedes_id = None
    else:
        current.status = "superseded"
        session.flush()
        lineage_id = current.lineage_id
        version = current.version + 1
        supersedes_id = current.id

    memory = SemanticMemory(
        **fields,
        source_ref=source_ref,
        lineage_id=lineage_id,
        version=version,
        supersedes_id=supersedes_id,
        status="active",
    )
    session.add(memory)
    session.flush()
    return _memory_to_dict(memory)


def _update(
    session: Session,
    *,
    profile_id: str,
    memory_id: int,
    value: str,
    source: str,
    source_ref: str | None,
) -> dict:
    current = _get_owned_memory(session, profile_id, memory_id)
    if current.status not in CURRENT_STATUSES:
        raise ValueError("only a current memory can be updated")
    if current.value == value:
        return _memory_to_dict(current)

    successor_status = current.status
    current.status = "superseded"
    session.flush()
    successor = SemanticMemory(
        lineage_id=current.lineage_id,
        version=current.version + 1,
        supersedes_id=current.id,
        profile_id=current.profile_id,
        memory_type=current.memory_type,
        workflow_scope=current.workflow_scope,
        contact_scope=current.contact_scope,
        key=current.key,
        normalized_key=current.normalized_key,
        value=value,
        source=source,
        source_ref=source_ref,
        status=successor_status,
        disabled_at=_utc_timestamp() if successor_status == "disabled" else None,
    )
    session.add(successor)
    session.flush()
    return _memory_to_dict(successor)


def _forget(session: Session, *, profile_id: str, memory_id: int) -> dict:
    memory = _get_owned_memory(session, profile_id, memory_id)
    lineage_id = memory.lineage_id
    forgotten_count = _scrub_lineages(session, [lineage_id])
    return {"lineage_id": lineage_id, "forgotten_count": forgotten_count}


def _reset(
    session: Session,
    *,
    profile_id: str,
    memory_type: str | None,
    workflow_scope: str | None,
    contact_scope: str | None | object,
) -> dict:
    query = session.query(SemanticMemory.lineage_id).filter(
        SemanticMemory.profile_id == profile_id,
        SemanticMemory.status.in_(CURRENT_STATUSES),
    )
    if memory_type is not None:
        query = query.filter(
            SemanticMemory.memory_type == _required(memory_type, "memory_type").casefold()
        )
    if workflow_scope is not None:
        query = query.filter(
            SemanticMemory.workflow_scope
            == _required(workflow_scope, "workflow_scope").casefold()
        )
    if contact_scope is not _ALL_CONTACT_SCOPES:
        query = query.filter(
            SemanticMemory.contact_scope == (contact_scope or "").strip().casefold()
        )

    lineage_ids = [row[0] for row in query.distinct().all()]
    forgotten_count = _scrub_lineages(session, lineage_ids)
    return {
        "lineage_count": len(lineage_ids),
        "forgotten_count": forgotten_count,
    }


def _required(value: str, field_name: str) -> str:
    normalized = value.strip()
    if not normalized:
        raise ValueError(f"{field_name} is required")
    return normalized


def _get_owned_memory(session, profile_id: str, memory_id: int) -> SemanticMemory:
    memory = session.get(SemanticMemory, memory_id)
    normalized_profile = _required(profile_id, "profile_id")
    if memory is None or memory.profile_id != normalized_profile:
        raise KeyError(f"semantic memory not found: {memory_id}")
    return memory


def _utc_timestamp() -> int:
    return int(datetime.now(timezone.utc).timestamp())


def _scrub_lineages(session, lineage_ids: list[str]) -> int:
    if not lineage_ids:
        return 0
    memories = (
        session.query(SemanticMemory)
        .filter(
            SemanticMemory.lineage_id.in_(lineage_ids),
            SemanticMemory.status != "forgotten",
        )
        .all()
    )
    candidate_refs = {
        (memory.profile_id, candidate_id)
        for memory in memories
        if memory.source == "candidate_confirmation"
        and (candidate_id := _candidate_id(memory.source_ref)) is not None
    }
    _scrub_candidates(session, candidate_refs)
    forgotten_at = _utc_timestamp()
    for memory in memories:
        memory.status = "forgotten"
        memory.workflow_scope = ""
        memory.contact_scope = ""
        memory.key = None
        memory.normalized_key = None
        memory.value = None
        memory.source_ref = None
        memory.disabled_at = None
        memory.forgotten_at = forgotten_at
    return len(memories)


def _candidate_id(source_ref: str | None) -> int | None:
    if not source_ref or not source_ref.startswith("candidate:"):
        return None
    candidate_id = source_ref.removeprefix("candidate:")
    return int(candidate_id) if candidate_id.isdigit() else None


def _scrub_candidates(session, candidate_refs: set[tuple[str, int]]) -> None:
    for profile_id, candidate_id in candidate_refs:
        candidate = session.get(SemanticMemoryCandidate, candidate_id)
        if candidate is None or candidate.profile_id != profile_id:
            continue
        (
            session.query(SemanticMemoryCandidateEvidence)
            .filter_by(candidate_id=candidate_id)
            .delete(synchronize_session=False)
        )
        candidate.status = "forgotten"
        candidate.workflow_scope = ""
        candidate.contact_scope = ""
        candidate.key = ""
        candidate.normalized_key = f"forgotten:{candidate.id}"
        candidate.value = ""
        candidate.normalized_value = ""
        candidate.evidence_count = 0


def _memory_to_dict(memory: SemanticMemory) -> dict:
    return {
        "id": memory.id,
        "lineage_id": memory.lineage_id,
        "version": memory.version,
        "supersedes_id": memory.supersedes_id,
        "profile_id": memory.profile_id,
        "memory_type": memory.memory_type,
        "workflow_scope": memory.workflow_scope or None,
        "contact_scope": memory.contact_scope or None,
        "key": memory.key,
        "normalized_key": memory.normalized_key,
        "value": memory.value,
        "source": memory.source,
        "source_ref": memory.source_ref,
        "status": memory.status,
        "created_at": memory.created_at,
        "updated_at": memory.updated_at,
        "confirmed_at": memory.confirmed_at,
        "disabled_at": memory.disabled_at,
        "forgotten_at": memory.forgotten_at,
    }
