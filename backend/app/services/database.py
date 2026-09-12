import logging
import os
from pathlib import Path
from sqlalchemy import create_engine, desc, or_, text
from sqlalchemy.orm import sessionmaker
from app.models.email import Base, DraftReply, Email, SentAction, LabelAction
from datetime import datetime

logger = logging.getLogger(__name__)

# Get project root directory (4 levels up from this file)
PROJECT_ROOT = Path(__file__).parent.parent.parent.parent
DATA_DIR = PROJECT_ROOT / "data"

class DatabaseService:
    """Service for database operations"""
    
    def __init__(self, db_path: str = None):
        """Initialize database connection and create tables"""
        if db_path is None:
            # Default to project root data directory
            DATA_DIR.mkdir(exist_ok=True)
            db_path = str(DATA_DIR / "emails.db")
        
        self.engine = create_engine(f'sqlite:///{db_path}', echo=False)
        Base.metadata.create_all(self.engine)
        self._migrate_add_is_deleted()
        self.Session = sessionmaker(bind=self.engine)
        logger.info(f"Database initialized at {db_path}")

    def _migrate_add_is_deleted(self):
        """Lightweight forward migration: add emails.is_deleted for pre-M2 DBs.
        SQLite doesn't support IF NOT EXISTS on ADD COLUMN; swallow the
        duplicate-column error on repeat runs.
        """
        with self.engine.connect() as conn:
            try:
                conn.execute(text("ALTER TABLE emails ADD COLUMN is_deleted BOOLEAN DEFAULT 0"))
                conn.commit()
            except Exception:
                pass  # column already exists
    
    def insert_email(self, email_data: dict):
        """Insert or update email (upsert)"""
        session = self.Session()
        try:
            # Check if email exists
            existing = session.query(Email).filter_by(id=email_data['id']).first()
            
            if existing:
                # Update existing email
                for key, value in email_data.items():
                    if hasattr(existing, key):
                        setattr(existing, key, value)
                existing.updated_at = datetime.utcnow()
                logger.debug(f"Updated email: {email_data['id']}")
            else:
                # Insert new email
                email = Email(**email_data)
                session.add(email)
                logger.debug(f"Inserted email: {email_data['id']}")
            
            session.commit()
        except Exception as e:
            session.rollback()
            logger.error(f"Failed to insert/update email: {e}")
            raise
        finally:
            session.close()
    
    def get_emails(self, limit: int = 20, offset: int = 0, unread_only: bool = False, search_query: str = None):
        """Get emails list with pagination and optional keyword search"""
        session = self.Session()
        try:
            query = session.query(Email).filter(Email.is_deleted == False)  # noqa: E712

            if unread_only:
                query = query.filter(Email.is_read == False)

            if search_query:
                search_term = f"%{search_query}%"
                query = query.filter(
                    or_(
                        Email.subject.ilike(search_term),
                        Email.sender_name.ilike(search_term),
                        Email.sender_email.ilike(search_term),
                        Email.body_preview.ilike(search_term)
                    )
                )

            emails = query.order_by(desc(Email.received_datetime))\
                         .limit(limit)\
                         .offset(offset)\
                         .all()

            return [self._email_to_dict(e) for e in emails]
        finally:
            session.close()
    
    def get_email_by_id(self, email_id: str):
        """Get single email by ID"""
        session = self.Session()
        try:
            email = session.query(Email).filter_by(id=email_id).first()
            return self._email_to_dict(email) if email else None
        finally:
            session.close()
    
    def update_email_status(self, email_id: str, **kwargs):
        """Update email status fields"""
        session = self.Session()
        try:
            email = session.query(Email).filter_by(id=email_id).first()
            if email:
                for key, value in kwargs.items():
                    if hasattr(email, key):
                        setattr(email, key, value)
                email.updated_at = datetime.utcnow()
                session.commit()
                logger.debug(f"Updated email status: {email_id}")
        except Exception as e:
            session.rollback()
            logger.error(f"Failed to update email status: {e}")
            raise
        finally:
            session.close()
    
    def insert_sent_action(self, action_data: dict) -> int:
        """Record a dry-run outbound reply. Returns the new row id."""
        session = self.Session()
        try:
            action = SentAction(**action_data)
            session.add(action)
            session.commit()
            session.refresh(action)
            logger.info(
                f"Recorded sent_action id={action.id} thread={action.thread_id} "
                f"to={action.recipient} (dry-run)"
            )
            return action.id
        except Exception as e:
            session.rollback()
            logger.error(f"Failed to record sent_action: {e}")
            raise
        finally:
            session.close()

    def create_draft(self, draft_data: dict) -> int:
        """Create a reply draft. Returns the new row id."""
        session = self.Session()
        try:
            draft = DraftReply(**draft_data)
            session.add(draft)
            session.commit()
            session.refresh(draft)
            return draft.id
        except Exception:
            session.rollback()
            raise
        finally:
            session.close()

    def get_drafts_for_email(self, email_id: str) -> list[dict]:
        """Return active drafts for an email, newest version first."""
        session = self.Session()
        try:
            drafts = (
                session.query(DraftReply)
                .filter_by(email_id=email_id, status="draft")
                .order_by(desc(DraftReply.created_at), desc(DraftReply.id))
                .all()
            )
            return [self._draft_to_dict(draft) for draft in drafts]
        finally:
            session.close()

    def get_draft_context(
        self,
        draft_id: int,
        selection_start: int | None = None,
        selection_end: int | None = None,
        scope: str = "around",
        context_chars: int = 600,
        email_id: str | None = None,
    ) -> dict:
        """Read a bounded or full active draft context without mutating it."""
        session = self.Session()
        try:
            draft = session.query(DraftReply).filter_by(id=draft_id).first()
            if not draft:
                raise ValueError(f"draft not found: {draft_id!r}")
            if draft.status != "draft":
                raise ValueError(f"draft is not active: {draft_id!r}")
            if email_id is not None and draft.email_id != email_id:
                raise ValueError(f"draft does not belong to email: {email_id!r}")
            if scope not in {"around", "full"}:
                raise ValueError("draft context scope is invalid")
            if scope == "full":
                start, end = 0, len(draft.body)
            else:
                if selection_start is None or selection_end is None:
                    raise ValueError("selection range is required for around context")
                if (
                    selection_start < 0
                    or selection_end < selection_start
                    or selection_end > len(draft.body)
                ):
                    raise ValueError("draft selection range is invalid")
                margin = max(100, min(context_chars, 2000))
                start = max(0, selection_start - margin)
                end = min(len(draft.body), selection_end + margin)
            return {
                "draft_id": draft.id,
                "scope": scope,
                "body": draft.body[start:end],
                "context_start": start,
                "context_end": end,
                "selection_start": selection_start,
                "selection_end": selection_end,
            }
        finally:
            session.close()

    def update_draft(self, draft_id: int, body: str) -> dict:
        """Persist edits to an active draft."""
        session = self.Session()
        try:
            draft = session.query(DraftReply).filter_by(id=draft_id).first()
            if not draft:
                raise ValueError(f"draft not found: {draft_id!r}")
            if draft.status != "draft":
                raise ValueError(f"draft is not active: {draft_id!r}")
            draft.body = body
            session.commit()
            session.refresh(draft)
            return self._draft_to_dict(draft)
        except Exception:
            session.rollback()
            raise
        finally:
            session.close()

    def apply_draft_patch(
        self,
        draft_id: int,
        selection_start: int,
        selection_end: int,
        replacement: str,
        email_id: str | None = None,
    ) -> dict:
        """Replace a selected body range while preserving the rest of a draft."""
        session = self.Session()
        try:
            draft = session.query(DraftReply).filter_by(id=draft_id).first()
            if not draft:
                raise ValueError(f"draft not found: {draft_id!r}")
            if draft.status != "draft":
                raise ValueError(f"draft is not active: {draft_id!r}")
            if email_id is not None and draft.email_id != email_id:
                raise ValueError(f"draft does not belong to email: {email_id!r}")
            if (
                selection_start < 0
                or selection_end < selection_start
                or selection_end > len(draft.body)
            ):
                raise ValueError("draft selection range is invalid")
            draft.body = draft.body[:selection_start] + replacement + draft.body[selection_end:]
            session.commit()
            session.refresh(draft)
            return self._draft_to_dict(draft)
        except Exception:
            session.rollback()
            raise
        finally:
            session.close()

    def discard_draft(self, draft_id: int) -> dict:
        """Mark an active draft as discarded."""
        session = self.Session()
        try:
            draft = session.query(DraftReply).filter_by(id=draft_id).first()
            if not draft:
                raise ValueError(f"draft not found: {draft_id!r}")
            if draft.status != "draft":
                raise ValueError(f"draft is not active: {draft_id!r}")
            draft.status = "discarded"
            session.commit()
            session.refresh(draft)
            return self._draft_to_dict(draft)
        except Exception:
            session.rollback()
            raise
        finally:
            session.close()

    def send_draft(self, draft_id: int) -> dict:
        """Record a dry-run send and close the draft in one transaction."""
        session = self.Session()
        try:
            draft = session.query(DraftReply).filter_by(id=draft_id).first()
            if not draft:
                raise ValueError(f"draft not found: {draft_id!r}")
            if draft.status != "draft":
                raise ValueError(f"draft is not active: {draft_id!r}")
            action = SentAction(
                thread_id=draft.thread_id,
                original_email_id=draft.email_id,
                recipient=draft.recipient,
                subject=draft.subject,
                body=draft.body,
            )
            session.add(action)
            draft.status = "sent"
            session.commit()
            session.refresh(action)
            return {"draft_id": draft.id, "sent_action_id": action.id}
        except Exception:
            session.rollback()
            raise
        finally:
            session.close()

    def insert_label_action(self, action_data: dict) -> int:
        """Record a dry-run inbox label (mark_read / archive). Returns the new row id."""
        session = self.Session()
        try:
            action = LabelAction(**action_data)
            session.add(action)
            session.commit()
            session.refresh(action)
            logger.info(
                f"Recorded label_action id={action.id} thread={action.thread_id} "
                f"email={action.email_id} kind={action.kind} (dry-run)"
            )
            return action.id
        except Exception as e:
            session.rollback()
            logger.error(f"Failed to record label_action: {e}")
            raise
        finally:
            session.close()

    def get_unread_emails(self, limit: int = 20):
        """Get unread emails, newest first. Used by the triage workflow.
        Excludes archived and deleted — those are already 'processed' from the
        user's perspective and should not resurface in the next triage pass.
        """
        session = self.Session()
        try:
            emails = (
                session.query(Email)
                .filter(Email.is_read == False)  # noqa: E712
                .filter(Email.is_archived == False)
                .filter(Email.is_deleted == False)
                .order_by(desc(Email.received_datetime))
                .limit(max(1, min(limit, 50)))
                .all()
            )
            return [self._email_to_dict(e) for e in emails]
        finally:
            session.close()

    def undo_email_action(self, row_id: int) -> dict:
        """Reverse a previous apply_email_action by row_id.

        Deletes the label_actions row and resets the corresponding Email flags
        to reflect "put it back where it was". Note: we don't remember the pre-
        action flag state, so undo returns the email to a neutral unread/unarchived
        state, matching the user's likely intent of 'get it back in the triage list'.
        Raises ValueError if the row doesn't exist.
        """
        session = self.Session()
        try:
            action = session.query(LabelAction).filter_by(id=row_id).first()
            if not action:
                raise ValueError(f"label_action row not found: {row_id!r}")
            email = session.query(Email).filter_by(id=action.email_id).first()
            if email:
                if action.kind == "mark_read":
                    email.is_read = False
                elif action.kind == "archive":
                    email.is_archived = False
                    email.is_read = False  # archive had implied read; undo returns to unread
                elif action.kind == "delete":
                    email.is_deleted = False
                email.updated_at = datetime.utcnow()
            summary = {"email_id": action.email_id, "kind": action.kind}
            session.delete(action)
            session.commit()
            logger.info(f"Undid email action row_id={row_id} email={summary['email_id']} kind={summary['kind']}")
            return summary
        except Exception:
            session.rollback()
            raise
        finally:
            session.close()

    def apply_email_action(self, email_id: str, kind: str, thread_id: str = "user-direct") -> int:
        """Apply a triage action to a single email — writes label_actions
        AND mutates local Email row state (is_read / is_archived / is_deleted).

        Dry-run in Phase 1: local state changes but nothing hits Microsoft Graph.
        Returns the new label_actions row id. Raises ValueError on unknown kind
        or missing email.
        """
        if kind not in ("mark_read", "archive", "delete"):
            raise ValueError(f"unknown kind: {kind!r}")
        session = self.Session()
        try:
            email = session.query(Email).filter_by(id=email_id).first()
            if not email:
                raise ValueError(f"email not found: {email_id!r}")
            if kind == "mark_read":
                email.is_read = True
            elif kind == "archive":
                email.is_archived = True
                email.is_read = True  # archive implies read
            elif kind == "delete":
                email.is_deleted = True
            email.updated_at = datetime.utcnow()
            action = LabelAction(thread_id=thread_id, email_id=email_id, kind=kind)
            session.add(action)
            session.commit()
            session.refresh(action)
            logger.info(
                f"Applied email action id={action.id} email={email_id} kind={kind} thread={thread_id}"
            )
            return action.id
        except Exception:
            session.rollback()
            raise
        finally:
            session.close()

    def get_email_count(self):
        """Get total email count"""
        session = self.Session()
        try:
            return session.query(Email).count()
        finally:
            session.close()
    
    def _email_to_dict(self, email):
        """Convert Email model to dictionary"""
        if not email:
            return None
        
        return {
            "id": email.id,
            "subject": email.subject,
            "sender": email.sender_name,
            "sender_email": email.sender_email,
            "received_datetime": email.received_datetime,  # Unix timestamp
            "body_preview": email.body_preview,
            "body_content": email.body_content,
            "body_html": email.body_html,
            "summary": email.summary,
            "summary_model": email.summary_model,
            "summary_generated_at": email.summary_generated_at,  # Unix timestamp
            "is_read": email.is_read,
            "is_starred": email.is_starred,
            "is_archived": email.is_archived,
            "is_deleted": email.is_deleted,
            "has_attachments": email.has_attachments,
            "attachments": email.attachments,
        }

    def _draft_to_dict(self, draft):
        return {
            "id": draft.id,
            "thread_id": draft.thread_id,
            "email_id": draft.email_id,
            "recipient": draft.recipient,
            "subject": draft.subject,
            "body": draft.body,
            "status": draft.status,
            "created_at": draft.created_at,
            "updated_at": draft.updated_at,
        }
