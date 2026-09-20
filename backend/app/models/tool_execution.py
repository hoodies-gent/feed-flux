from datetime import datetime, timezone

from sqlalchemy import JSON, Column, Integer, String

from app.models.email import Base


def _utc_timestamp() -> int:
    return int(datetime.now(timezone.utc).timestamp())


class ToolExecution(Base):
    __tablename__ = "tool_executions"

    idempotency_key = Column(String, primary_key=True)
    operation = Column(String, nullable=False)
    request_hash = Column(String, nullable=False)
    run_id = Column(String, index=True)
    tool_call_id = Column(String)
    result = Column(JSON, nullable=False)
    schema_version = Column(Integer, nullable=False, default=1)
    created_at = Column(Integer, nullable=False, default=_utc_timestamp)
