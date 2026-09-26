from datetime import datetime, timezone

from sqlalchemy import Column, ForeignKey, Index, Integer, String, Text, text

from app.models.email import Base


def _utc_timestamp() -> int:
    return int(datetime.now(timezone.utc).timestamp())


class SemanticMemory(Base):
    __tablename__ = "semantic_memories"
    __table_args__ = (
        Index(
            "uq_semantic_memories_current_key",
            "profile_id",
            "memory_type",
            "workflow_scope",
            "contact_scope",
            "normalized_key",
            unique=True,
            sqlite_where=text("status IN ('active', 'disabled')"),
        ),
        Index(
            "uq_semantic_memories_lineage_version",
            "lineage_id",
            "version",
            unique=True,
        ),
    )

    id = Column(Integer, primary_key=True, autoincrement=True)
    lineage_id = Column(String, nullable=False, index=True)
    version = Column(Integer, nullable=False)
    supersedes_id = Column(Integer, ForeignKey("semantic_memories.id"))
    profile_id = Column(String, nullable=False, index=True)
    memory_type = Column(String, nullable=False)
    workflow_scope = Column(String, nullable=False)
    contact_scope = Column(String, nullable=False, default="")
    key = Column(String)
    normalized_key = Column(String)
    value = Column(Text)
    source = Column(String, nullable=False)
    source_ref = Column(String)
    status = Column(String, nullable=False, index=True)
    created_at = Column(Integer, nullable=False, default=_utc_timestamp)
    updated_at = Column(
        Integer,
        nullable=False,
        default=_utc_timestamp,
        onupdate=_utc_timestamp,
    )
    confirmed_at = Column(Integer, nullable=False, default=_utc_timestamp)
    disabled_at = Column(Integer)
    forgotten_at = Column(Integer)


class SemanticMemoryCandidate(Base):
    __tablename__ = "semantic_memory_candidates"
    __table_args__ = (
        Index(
            "uq_semantic_memory_candidate_identity",
            "profile_id",
            "memory_type",
            "workflow_scope",
            "contact_scope",
            "normalized_key",
            "normalized_value",
            unique=True,
        ),
    )

    id = Column(Integer, primary_key=True, autoincrement=True)
    profile_id = Column(String, nullable=False, index=True)
    memory_type = Column(String, nullable=False)
    workflow_scope = Column(String, nullable=False)
    contact_scope = Column(String, nullable=False, default="")
    key = Column(String, nullable=False)
    normalized_key = Column(String, nullable=False)
    value = Column(Text, nullable=False)
    normalized_value = Column(Text, nullable=False)
    status = Column(String, nullable=False, index=True)
    evidence_count = Column(Integer, nullable=False, default=0)
    created_at = Column(Integer, nullable=False, default=_utc_timestamp)
    updated_at = Column(
        Integer,
        nullable=False,
        default=_utc_timestamp,
        onupdate=_utc_timestamp,
    )
    suggested_at = Column(Integer)
    confirmed_at = Column(Integer)
    rejected_at = Column(Integer)
    expired_at = Column(Integer)


class SemanticMemoryCandidateEvidence(Base):
    __tablename__ = "semantic_memory_candidate_evidence"
    __table_args__ = (
        Index(
            "uq_semantic_memory_candidate_evidence_source",
            "candidate_id",
            "source_ref",
            unique=True,
        ),
    )

    id = Column(Integer, primary_key=True, autoincrement=True)
    candidate_id = Column(
        Integer,
        ForeignKey("semantic_memory_candidates.id"),
        nullable=False,
        index=True,
    )
    source = Column(String, nullable=False)
    source_ref = Column(String, nullable=False)
    created_at = Column(Integer, nullable=False, default=_utc_timestamp)


class SemanticMemoryCandidateConfirmation(Base):
    __tablename__ = "semantic_memory_candidate_confirmations"

    candidate_id = Column(
        Integer,
        ForeignKey("semantic_memory_candidates.id"),
        primary_key=True,
    )
    profile_id = Column(String, nullable=False, index=True)
    lineage_id = Column(String, nullable=False, index=True)
    created_at = Column(Integer, nullable=False, default=_utc_timestamp)
