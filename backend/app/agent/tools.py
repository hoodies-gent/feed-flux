import contextvars
from datetime import datetime, timedelta

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


TOOLS = [send_test_email, find_email, read_calendar, send_reply]
TOOLS_BY_NAME = {t.name: t for t in TOOLS}
HIGH_RISK_TOOLS = {"send_test_email", "send_reply"}
