from typing import Any

from sqlalchemy.orm import Session

from app.models.email import DraftReply
from app.services.database import DatabaseService
from app.services.tool_execution_store import ToolExecutionStore


class ReplyDraftService:
    def __init__(self, database: DatabaseService):
        self.database = database
        self.executions = ToolExecutionStore(database)

    def save_once(
        self,
        *,
        run_id: str,
        tool_call_id: str,
        thread_id: str,
        recipient: str,
        subject: str,
        body: str,
        original_email_id: str,
        draft_id: int | None,
    ) -> dict[str, Any]:
        result, _ = self.executions.execute_once(
            idempotency_key=f"{run_id}:{tool_call_id}",
            operation="send_reply",
            request_payload={
                "thread_id": thread_id,
                "recipient": recipient,
                "subject": subject,
                "body": body,
                "original_email_id": original_email_id,
                "draft_id": draft_id,
            },
            execute=lambda session: self._save(
                session,
                thread_id=thread_id,
                recipient=recipient,
                subject=subject,
                body=body,
                original_email_id=original_email_id,
                draft_id=draft_id,
            ),
            run_id=run_id,
            tool_call_id=tool_call_id,
        )
        return result

    def apply_patch_once(
        self,
        *,
        run_id: str,
        tool_call_id: str,
        draft_id: int,
        original_email_id: str,
        selection_start: int,
        selection_end: int,
        replacement: str,
    ) -> dict[str, Any]:
        result, _ = self.executions.execute_once(
            idempotency_key=f"{run_id}:{tool_call_id}",
            operation="apply_draft_patch",
            request_payload={
                "draft_id": draft_id,
                "original_email_id": original_email_id,
                "selection_start": selection_start,
                "selection_end": selection_end,
                "replacement": replacement,
            },
            execute=lambda session: self._apply_patch(
                session,
                draft_id=draft_id,
                original_email_id=original_email_id,
                selection_start=selection_start,
                selection_end=selection_end,
                replacement=replacement,
            ),
            run_id=run_id,
            tool_call_id=tool_call_id,
        )
        return result

    @staticmethod
    def _save(
        session: Session,
        *,
        thread_id: str,
        recipient: str,
        subject: str,
        body: str,
        original_email_id: str,
        draft_id: int | None,
    ) -> dict[str, Any]:
        draft = None
        if draft_id is not None:
            draft = session.get(DraftReply, draft_id)
            if draft is None:
                raise ValueError(f"draft not found: {draft_id!r}")
            if draft.status != "draft":
                raise ValueError(f"draft is not active: {draft_id!r}")
        else:
            draft = (
                session.query(DraftReply)
                .filter_by(email_id=original_email_id, status="draft")
                .order_by(DraftReply.created_at.desc(), DraftReply.id.desc())
                .first()
            )

        if draft is not None:
            draft.body = body
            marker = "DRAFT UPDATED"
        else:
            draft = DraftReply(
                thread_id=thread_id,
                email_id=original_email_id,
                recipient=recipient,
                subject=subject,
                body=body,
            )
            session.add(draft)
            marker = "DRAFT READY"

        session.flush()
        return {"draft_id": draft.id, "marker": marker}

    @staticmethod
    def _apply_patch(
        session: Session,
        *,
        draft_id: int,
        original_email_id: str,
        selection_start: int,
        selection_end: int,
        replacement: str,
    ) -> dict[str, Any]:
        draft = session.get(DraftReply, draft_id)
        if draft is None:
            raise ValueError(f"draft not found: {draft_id!r}")
        if draft.status != "draft":
            raise ValueError(f"draft is not active: {draft_id!r}")
        if draft.email_id != original_email_id:
            raise ValueError(f"draft does not belong to email: {original_email_id!r}")
        if (
            selection_start < 0
            or selection_end < selection_start
            or selection_end > len(draft.body)
        ):
            raise ValueError("draft selection range is invalid")

        draft.body = (
            draft.body[:selection_start]
            + replacement
            + draft.body[selection_end:]
        )
        session.flush()
        return {"draft_id": draft.id}
