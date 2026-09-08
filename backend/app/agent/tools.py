from langchain_core.tools import tool
from pydantic import BaseModel, Field


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


TOOLS = [send_test_email]
TOOLS_BY_NAME = {t.name: t for t in TOOLS}
HIGH_RISK_TOOLS = {"send_test_email"}
