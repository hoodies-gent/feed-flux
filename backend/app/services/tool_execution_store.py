import hashlib
import json
from collections.abc import Callable, Mapping
from typing import Any

from sqlalchemy.orm import Session

from app.models.tool_execution import ToolExecution
from app.services.database import DatabaseService


class IdempotencyConflictError(ValueError):
    pass


class ToolExecutionStore:
    def __init__(self, database: DatabaseService):
        self.database = database

    def execute_once(
        self,
        *,
        idempotency_key: str,
        operation: str,
        request_payload: Mapping[str, Any],
        execute: Callable[[Session], dict[str, Any]],
        run_id: str | None = None,
        tool_call_id: str | None = None,
        schema_version: int = 1,
    ) -> tuple[dict[str, Any], bool]:
        request_hash = _request_hash(request_payload)
        session = self.database.Session()
        try:
            existing = session.get(ToolExecution, idempotency_key)
            if existing is not None:
                _validate_replay(existing, operation, request_hash)
                return existing.result, False

            result = execute(session)
            session.add(
                ToolExecution(
                    idempotency_key=idempotency_key,
                    operation=operation,
                    request_hash=request_hash,
                    run_id=run_id,
                    tool_call_id=tool_call_id,
                    result=result,
                    schema_version=schema_version,
                )
            )
            session.commit()
            return result, True
        except Exception:
            session.rollback()
            raise
        finally:
            session.close()


def _request_hash(payload: Mapping[str, Any]) -> str:
    canonical = json.dumps(
        payload,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _validate_replay(
    existing: ToolExecution,
    operation: str,
    request_hash: str,
) -> None:
    if existing.operation != operation or existing.request_hash != request_hash:
        raise IdempotencyConflictError(
            f"idempotency key {existing.idempotency_key!r} was reused for a different request"
        )
