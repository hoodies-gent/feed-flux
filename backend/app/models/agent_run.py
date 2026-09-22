from datetime import datetime, timezone

from sqlalchemy import JSON, Column, ForeignKey, Integer, String

from app.models.email import Base


def _utc_timestamp() -> int:
    return int(datetime.now(timezone.utc).timestamp())


class AgentRun(Base):
    __tablename__ = "agent_runs"

    run_id = Column(String, primary_key=True)
    thread_id = Column(String, nullable=False, index=True)
    status = Column(String, nullable=False)
    provider = Column(String)
    outcome = Column(JSON)
    error_category = Column(String)
    created_at = Column(Integer, nullable=False, default=_utc_timestamp)
    updated_at = Column(
        Integer,
        nullable=False,
        default=_utc_timestamp,
        onupdate=_utc_timestamp,
    )


class AgentRunEvent(Base):
    __tablename__ = "agent_run_events"

    id = Column(Integer, primary_key=True, autoincrement=True)
    run_id = Column(
        String,
        ForeignKey("agent_runs.run_id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    event_type = Column(String, nullable=False)
    status = Column(String)
    provider = Column(String)
    tool_name = Column(String)
    tool_call_id = Column(String)
    outcome = Column(JSON)
    error_category = Column(String)
    created_at = Column(Integer, nullable=False, default=_utc_timestamp)
