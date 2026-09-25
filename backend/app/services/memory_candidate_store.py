from datetime import datetime, timezone

from sqlalchemy import text
from sqlalchemy.orm import Session

from app.models.semantic_memory import (
    SemanticMemoryCandidate,
    SemanticMemoryCandidateEvidence,
)
from app.services.database import DatabaseService
from app.services.semantic_memory_store import SemanticMemoryStore


SUGGESTION_EVIDENCE_THRESHOLD = 2


class MemoryCandidateStore:
    def __init__(self, database: DatabaseService):
        self.database = database

    def record_candidate(
        self,
        *,
        profile_id: str,
        memory_type: str,
        workflow_scope: str,
        contact_scope: str | None,
        key: str,
        value: str,
        source: str,
        source_ref: str,
    ) -> dict:
        fields = _normalize_fields(
            profile_id=profile_id,
            memory_type=memory_type,
            workflow_scope=workflow_scope,
            contact_scope=contact_scope,
            key=key,
            value=value,
        )
        session = self.database.Session()
        try:
            session.execute(text("BEGIN IMMEDIATE"))
            result = _record_candidate(
                session,
                fields=fields,
                source=_required(source, "source"),
                source_ref=_required(source_ref, "source_ref"),
            )
            session.commit()
            return result
        except Exception:
            session.rollback()
            raise
        finally:
            session.close()

    def record_candidate_in_session(
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
        source_ref: str,
    ) -> dict:
        return _record_candidate(
            session,
            fields=_normalize_fields(
                profile_id=profile_id,
                memory_type=memory_type,
                workflow_scope=workflow_scope,
                contact_scope=contact_scope,
                key=key,
                value=value,
            ),
            source=_required(source, "source"),
            source_ref=_required(source_ref, "source_ref"),
        )

    def reject(self, *, profile_id: str, candidate_id: int) -> dict:
        session = self.database.Session()
        try:
            session.execute(text("BEGIN IMMEDIATE"))
            candidate = _get_owned_candidate(session, profile_id, candidate_id)
            if candidate.status != "suggested":
                raise ValueError("only a suggested candidate can be rejected")
            candidate.status = "rejected"
            candidate.rejected_at = _utc_timestamp()
            session.commit()
            session.refresh(candidate)
            return _candidate_to_dict(candidate)
        except Exception:
            session.rollback()
            raise
        finally:
            session.close()

    def expire(self, *, profile_id: str, candidate_id: int) -> dict:
        session = self.database.Session()
        try:
            session.execute(text("BEGIN IMMEDIATE"))
            candidate = _get_owned_candidate(session, profile_id, candidate_id)
            if candidate.status not in {"pending", "suggested"}:
                raise ValueError("only a pending or suggested candidate can expire")
            candidate.status = "expired"
            candidate.expired_at = _utc_timestamp()
            session.commit()
            session.refresh(candidate)
            return _candidate_to_dict(candidate)
        except Exception:
            session.rollback()
            raise
        finally:
            session.close()

    def confirm(self, *, profile_id: str, candidate_id: int) -> dict:
        session = self.database.Session()
        try:
            session.execute(text("BEGIN IMMEDIATE"))
            candidate = _get_owned_candidate(session, profile_id, candidate_id)
            if candidate.status == "confirmed":
                session.commit()
                return _candidate_to_dict(candidate)
            if candidate.status != "suggested":
                raise ValueError("only a suggested candidate can be confirmed")

            SemanticMemoryStore(self.database).remember_in_session(
                session,
                profile_id=candidate.profile_id,
                memory_type=candidate.memory_type,
                workflow_scope=candidate.workflow_scope,
                contact_scope=candidate.contact_scope,
                key=candidate.key,
                value=candidate.value,
                source="candidate_confirmation",
                source_ref=f"candidate:{candidate.id}",
            )
            candidate.status = "confirmed"
            candidate.confirmed_at = _utc_timestamp()
            session.commit()
            session.refresh(candidate)
            return _candidate_to_dict(candidate)
        except Exception:
            session.rollback()
            raise
        finally:
            session.close()

    def list_candidates(
        self,
        *,
        profile_id: str,
        status: str | None = None,
    ) -> list[dict]:
        normalized_profile = _required(profile_id, "profile_id")
        session = self.database.Session()
        try:
            query = session.query(SemanticMemoryCandidate).filter_by(
                profile_id=normalized_profile
            )
            if status is not None:
                query = query.filter_by(status=_required(status, "status").casefold())
            candidates = query.order_by(
                SemanticMemoryCandidate.updated_at.desc(),
                SemanticMemoryCandidate.id.desc(),
            ).all()
            return [_candidate_to_dict(candidate) for candidate in candidates]
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
) -> dict:
    display_key = " ".join(_required(key, "key").split())
    display_value = _required(value, "value")
    identity = {
        "profile_id": _required(profile_id, "profile_id"),
        "memory_type": _required(memory_type, "memory_type").casefold(),
        "workflow_scope": _required(workflow_scope, "workflow_scope").casefold(),
        "contact_scope": (contact_scope or "").strip().casefold(),
        "normalized_key": display_key.casefold(),
        "normalized_value": " ".join(display_value.split()).casefold(),
    }
    return {
        "identity": identity,
        "record": {
            **identity,
            "key": display_key,
            "value": display_value,
        },
    }


def _record_candidate(
    session: Session,
    *,
    fields: dict,
    source: str,
    source_ref: str,
) -> dict:
    candidate = (
        session.query(SemanticMemoryCandidate)
        .filter_by(**fields["identity"])
        .one_or_none()
    )
    if candidate is None:
        candidate = SemanticMemoryCandidate(
            **fields["record"],
            status="pending",
            evidence_count=0,
        )
        session.add(candidate)
        session.flush()

    if candidate.status not in {"pending", "suggested"}:
        return _candidate_to_dict(candidate)

    evidence = (
        session.query(SemanticMemoryCandidateEvidence)
        .filter_by(candidate_id=candidate.id, source_ref=source_ref)
        .one_or_none()
    )
    if evidence is None:
        session.add(
            SemanticMemoryCandidateEvidence(
                candidate_id=candidate.id,
                source=source,
                source_ref=source_ref,
            )
        )
        candidate.evidence_count += 1
        if candidate.evidence_count >= SUGGESTION_EVIDENCE_THRESHOLD:
            candidate.status = "suggested"
            candidate.suggested_at = _utc_timestamp()
        session.flush()

    return _candidate_to_dict(candidate)


def _required(value: str, field_name: str) -> str:
    normalized = value.strip()
    if not normalized:
        raise ValueError(f"{field_name} is required")
    return normalized


def _get_owned_candidate(
    session,
    profile_id: str,
    candidate_id: int,
) -> SemanticMemoryCandidate:
    candidate = session.get(SemanticMemoryCandidate, candidate_id)
    normalized_profile = _required(profile_id, "profile_id")
    if candidate is None or candidate.profile_id != normalized_profile:
        raise KeyError(f"memory candidate not found: {candidate_id}")
    return candidate


def _utc_timestamp() -> int:
    return int(datetime.now(timezone.utc).timestamp())


def _candidate_to_dict(candidate: SemanticMemoryCandidate) -> dict:
    return {
        "id": candidate.id,
        "profile_id": candidate.profile_id,
        "memory_type": candidate.memory_type,
        "workflow_scope": candidate.workflow_scope,
        "contact_scope": candidate.contact_scope or None,
        "key": candidate.key,
        "normalized_key": candidate.normalized_key,
        "value": candidate.value,
        "status": candidate.status,
        "evidence_count": candidate.evidence_count,
        "created_at": candidate.created_at,
        "updated_at": candidate.updated_at,
        "suggested_at": candidate.suggested_at,
        "confirmed_at": candidate.confirmed_at,
        "rejected_at": candidate.rejected_at,
        "expired_at": candidate.expired_at,
    }
