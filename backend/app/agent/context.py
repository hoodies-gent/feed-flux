from typing import Any

from app.services.database import DatabaseService


MAX_CONTEXT_EMAILS = 3
MAX_CONTEXT_CHARS = 6_000


class EmailContextError(ValueError):
    error_category = "user_repairable"

    def __init__(self, user_message: str):
        self.user_message = user_message
        super().__init__(user_message)


def _one_line(value: Any, limit: int) -> str:
    return " ".join(str(value or "").split())[:limit]


def _format_email_context(email: dict, char_limit: int, citation_key: str) -> str:
    email_id = _one_line(email["id"], 200).replace('"', "'")
    subject = _one_line(email.get("subject"), 300)
    sender = _one_line(email.get("sender") or email.get("sender_email"), 200)
    content = str(
        email.get("summary")
        or email.get("body_content")
        or email.get("body_preview")
        or ""
    )
    header = (
        f'<email_context citation_key="{citation_key}" id="{email_id}">\n'
        f"Subject: {subject}\n"
        f"From: {sender}\n"
        "Content:\n"
    )
    footer = "\n</email_context>"
    content_limit = max(0, char_limit - len(header) - len(footer))
    return f"{header}{content[:content_limit]}{footer}"


def resolve_email_context(
    email_ids: list[str],
    *,
    database: DatabaseService | None = None,
) -> dict[str, Any]:
    if len(email_ids) > MAX_CONTEXT_EMAILS:
        raise EmailContextError("You can pin at most 3 emails.")

    unique_ids = list(dict.fromkeys(email_ids))
    if not unique_ids:
        return {
            "email_ids": [],
            "prompt": "",
            "references": [],
            "context_chars": 0,
        }

    active_database = database or DatabaseService()
    emails = []
    for email_id in unique_ids:
        email = active_database.get_email_by_id(email_id)
        if email is None:
            raise EmailContextError(
                "A pinned email is unavailable or not accessible in this mailbox."
            )
        emails.append(email)

    separator = "\n\n"
    per_email_limit = (
        MAX_CONTEXT_CHARS - len(separator) * (len(emails) - 1)
    ) // len(emails)
    citation_keys = [f"context-{index}" for index in range(1, len(emails) + 1)]
    blocks = [
        _format_email_context(email, per_email_limit, citation_key)
        for email, citation_key in zip(emails, citation_keys)
    ]
    prompt = separator.join(blocks)
    references = [
        {
            "citation_key": citation_key,
            "email_id": email["id"],
            "subject": email.get("subject") or "",
            "sender": email.get("sender") or email.get("sender_email") or "",
        }
        for email, citation_key in zip(emails, citation_keys)
    ]
    return {
        "email_ids": unique_ids,
        "prompt": prompt,
        "references": references,
        "context_chars": len(prompt),
    }
