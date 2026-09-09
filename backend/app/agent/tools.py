import contextvars
from datetime import datetime, timedelta
from typing import Literal

from langchain_core.tools import tool
from pydantic import BaseModel, Field

from app.services.database import DatabaseService

# Set by the graph's tools_node before invoking any tool so that tools which
# need to attribute their side effects to a conversation can read it.
current_thread_id: contextvars.ContextVar[str] = contextvars.ContextVar(
    "current_thread_id", default="unknown"
)


class SendTestEmailInput(BaseModel):
    recipient: str = Field(description="Email address of the recipient.")
    subject: str = Field(description="Subject line of the email.")
    body: str = Field(description="Body text of the email.")


@tool("send_test_email", args_schema=SendTestEmailInput)
def send_test_email(recipient: str, subject: str, body: str) -> str:
    """Send an email on the user's behalf.
    Use when the user asks to send, forward, or reply to an email.
    This is a high-risk action requiring explicit user approval before it runs.
    """
    return f"[dry-run] Email queued to {recipient} — subject: {subject!r}, body length: {len(body)}."


class FindEmailInput(BaseModel):
    sender_contains: str | None = Field(
        default=None,
        description="Substring to match against the sender name or email address (case-insensitive).",
    )
    subject_contains: str | None = Field(
        default=None,
        description="Substring to match against the subject line (case-insensitive).",
    )
    limit: int = Field(
        default=5,
        description="Maximum number of emails to return. Prefer 3-5 for meeting-reply workflows.",
    )


@tool("find_email", args_schema=FindEmailInput)
def find_email(
    sender_contains: str | None = None,
    subject_contains: str | None = None,
    limit: int = 5,
) -> list[dict]:
    """Search the user's inbox for emails matching sender and/or subject substrings.

    Use when the user references an email by sender ("Sarah's meeting request")
    or topic ("the migration deep-dive email") and you need to identify which
    specific email they mean before drafting a reply.

    Returns compact summaries (id, subject, sender, received_datetime, body_preview)
    — enough to disambiguate. Fetch full body only when needed.
    """
    from sqlalchemy import or_

    from app.models.email import Email

    db = DatabaseService()
    session = db.Session()
    try:
        query = session.query(Email)
        if sender_contains:
            pattern = f"%{sender_contains}%"
            query = query.filter(
                or_(Email.sender_name.ilike(pattern), Email.sender_email.ilike(pattern))
            )
        if subject_contains:
            query = query.filter(Email.subject.ilike(f"%{subject_contains}%"))
        rows = (
            query.order_by(Email.received_datetime.desc())
            .limit(max(1, min(limit, 20)))
            .all()
        )
        return [
            {
                "id": r.id,
                "subject": r.subject,
                "sender": r.sender_name or r.sender_email,
                "sender_email": r.sender_email,
                "received": datetime.utcfromtimestamp(r.received_datetime).isoformat() + "Z",
                "body_preview": r.body_preview,
            }
            for r in rows
        ]
    finally:
        session.close()


class ListUnreadEmailsInput(BaseModel):
    limit: int = Field(
        default=20,
        description="Maximum number of unread emails to return. Default 20, capped at 50.",
    )


@tool("list_unread_emails", args_schema=ListUnreadEmailsInput)
def list_unread_emails(limit: int = 20) -> list[dict]:
    """Fetch the user's unread emails, newest first.

    Use at the start of a batch triage workflow when the user asks to
    process, clear, or review a batch of unread email ("处理今早的 20 封未读",
    "clean up my inbox", "triage today's unreads"). Read the returned
    summaries and decide a proposed action per email (mark_read / archive /
    reply), then submit them together via apply_triage_batch.

    Returns compact summaries (id, subject, sender, received, body_preview).
    """
    db = DatabaseService()
    rows = db.get_unread_emails(limit=limit)
    return [
        {
            "id": r["id"],
            "subject": r["subject"],
            "sender": r["sender"] or r["sender_email"],
            "sender_email": r["sender_email"],
            "received": datetime.utcfromtimestamp(r["received_datetime"]).isoformat() + "Z",
            "body_preview": r["body_preview"],
        }
        for r in rows
    ]


class ReadCalendarInput(BaseModel):
    days_ahead: int = Field(
        default=7,
        description="How many days into the future to inspect. Defaults to 7 (one week).",
    )


@tool("read_calendar", args_schema=ReadCalendarInput)
def read_calendar(days_ahead: int = 7) -> dict:
    """Return free 30-minute slots in the user's calendar for the coming week.

    Use when drafting a meeting reply that needs to propose specific times.
    The result lists candidate free slots (weekdays only, morning/afternoon).
    Pick 2-3 slots that make sense given the meeting purpose.
    """
    today = datetime.now()
    slots = []
    days = max(1, min(days_ahead, 14))
    for i in range(1, days + 1):
        d = today + timedelta(days=i)
        if d.weekday() >= 5:  # skip Sat/Sun
            continue
        for hh, mm in [(10, 30), (14, 0), (16, 0)]:
            slot = d.replace(hour=hh, minute=mm, second=0, microsecond=0)
            slots.append(slot.strftime("%a %b %d %H:%M"))
    return {
        "timezone": "local",
        "free_slots": slots,
        "note": "Approximate 30-minute openings. Busy blocks omitted from this view.",
    }


class SendReplyInput(BaseModel):
    original_email_id: str | None = Field(
        default=None,
        description="ID of the email being replied to (from find_email results). Optional for cold sends.",
    )
    recipient: str = Field(description="Email address of the recipient.")
    subject: str = Field(description="Subject line of the reply, typically prefixed with 'Re: '.")
    body: str = Field(description="Full body text of the reply.")


@tool("send_reply", args_schema=SendReplyInput)
def send_reply(
    recipient: str,
    subject: str,
    body: str,
    original_email_id: str | None = None,
) -> str:
    """Send a reply on the user's behalf.

    Use after you've drafted a reply the user should review. This is a
    high-risk action: the user will see the draft in a confirmation card
    and either approve, decline, or edit it before it runs.

    Dry-run in Phase 1 — the message is recorded locally, never sent to
    Microsoft Graph. Never retry silently on decline: read the user's note
    and redraft accordingly.
    """
    db = DatabaseService()
    row_id = db.insert_sent_action({
        "thread_id": current_thread_id.get(),
        "original_email_id": original_email_id,
        "recipient": recipient,
        "subject": subject,
        "body": body,
    })
    return (
        f"SEND COMPLETE (dry-run mode, id={row_id}). "
        f"The reply to {recipient} has been sent from the user's workflow perspective. "
        f"Do NOT offer further edits or ask for feedback on this reply — the action is finished."
    )


class TriageActionItem(BaseModel):
    email_id: str = Field(description="ID of the email this action applies to (from list_unread_emails).")
    action: Literal["mark_read", "archive"] = Field(
        description="Bulk-safe action for low-signal email. Never use this for anything requiring a human reply."
    )


class ApplyTriageBatchInput(BaseModel):
    actions: list[TriageActionItem] = Field(
        default_factory=list,
        description=(
            "Bulk-safe proposals only (mark_read / archive). One item per email. "
            "These become preselected checkboxes in the batch review card so the user "
            "can dismiss the low-signal bucket in a single click."
        ),
        max_length=50,
    )
    needs_reply_ids: list[str] = Field(
        default_factory=list,
        description=(
            "IDs of emails that genuinely need a human-authored reply — do NOT draft "
            "them here. They surface as a follow-up list; the user picks one at a time "
            "and drafts through the standard send_reply flow. Keep this short (0-5); "
            "if you find yourself putting most of the batch here, your classification "
            "is too conservative."
        ),
        max_length=20,
    )


@tool("apply_triage_batch", args_schema=ApplyTriageBatchInput)
def apply_triage_batch(actions: list[dict], needs_reply_ids: list[str]) -> str:
    """Submit a triage plan for a single human review pass.

    Use after list_unread_emails once you've classified each email into either
    (a) bulk-safe: mark_read or archive — goes into `actions`, or
    (b) needs a human reply — goes into `needs_reply_ids` (id only, no draft).

    HIGH-RISK — the user reviews the whole plan in one card, unchecks anything
    they disagree with, and applies bulk actions in one click. For emails in
    `needs_reply_ids`, the user picks them one at a time in a separate turn and
    drafts through the standard reply flow — DO NOT draft replies here.

    Dry-run in Phase 1: approved bulk items are recorded to local `label_actions`;
    nothing hits Microsoft Graph.

    Do NOT retry declined items in the same turn.
    """
    return (
        "apply_triage_batch called without a review decision — this should not happen; "
        "the tools_node must intercept and route through interrupt."
    )


TOOLS = [send_test_email, find_email, list_unread_emails, read_calendar, send_reply, apply_triage_batch]
TOOLS_BY_NAME = {t.name: t for t in TOOLS}
HIGH_RISK_TOOLS = {"send_test_email", "send_reply", "apply_triage_batch"}
