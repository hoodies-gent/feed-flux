import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from langchain_core.messages import ToolMessage
from langgraph.graph import END

from app.agent import graph as agent_graph
from app.agent import stream as agent_stream
from app.agent.tools import (
    HIGH_RISK_TOOLS,
    apply_draft_patch,
    current_thread_id,
    read_draft_context,
    read_original_email_context,
    send_reply,
)
from app.models.email import SentAction
from app.services.database import DatabaseService


class DraftAgentTest(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.data_dir = Path(self.temp_dir.name)
        self.data_dir_patch = patch("app.services.database.DATA_DIR", self.data_dir)
        self.data_dir_patch.start()

    def tearDown(self):
        self.data_dir_patch.stop()
        self.temp_dir.cleanup()

    def test_send_reply_creates_draft_without_recording_send(self):
        token = current_thread_id.set("agent-thread")
        try:
            output = send_reply.invoke({
                "original_email_id": "email-a",
                "recipient": "sarah@example.com",
                "subject": "Re: Weekly sync",
                "body": "Tuesday works for me.",
            })
        finally:
            current_thread_id.reset(token)

        db = DatabaseService(str(self.data_dir / "emails.db"))
        drafts = db.get_drafts_for_email("email-a")
        session = db.Session()
        try:
            sent_count = session.query(SentAction).count()
        finally:
            session.close()
            db.engine.dispose()

        self.assertEqual(1, len(drafts))
        self.assertEqual("agent-thread", drafts[0]["thread_id"])
        self.assertEqual("Tuesday works for me.", drafts[0]["body"])
        self.assertEqual(0, sent_count)
        self.assertIn(f"DRAFT READY (id={drafts[0]['id']})", output)
        self.assertNotIn("send_reply", HIGH_RISK_TOOLS)

    def test_send_reply_updates_existing_draft_when_draft_id_is_given(self):
        db = DatabaseService(str(self.data_dir / "emails.db"))
        draft_id = db.create_draft({
            "thread_id": "original-thread",
            "email_id": "email-a",
            "recipient": "sarah@example.com",
            "subject": "Re: Weekly sync",
            "body": "Original draft",
        })

        token = current_thread_id.set("revision-thread")
        try:
            output = send_reply.invoke({
                "draft_id": draft_id,
                "original_email_id": "email-a",
                "recipient": "sarah@example.com",
                "subject": "Re: Weekly sync",
                "body": "Revised draft",
            })
        finally:
            current_thread_id.reset(token)

        drafts = db.get_drafts_for_email("email-a")
        self.assertEqual([draft_id], [draft["id"] for draft in drafts])
        self.assertEqual("Revised draft", drafts[0]["body"])
        self.assertIn(f"DRAFT UPDATED (id={draft_id})", output)

    def test_send_reply_reuses_latest_active_draft_by_default(self):
        db = DatabaseService(str(self.data_dir / "emails.db"))
        draft_id = db.create_draft({
            "thread_id": "original-thread",
            "email_id": "email-a",
            "recipient": "sarah@example.com",
            "subject": "Re: Weekly sync",
            "body": "Original draft",
        })

        output = send_reply.invoke({
            "original_email_id": "email-a",
            "recipient": "sarah@example.com",
            "subject": "Re: Weekly sync",
            "body": "Revised canonical draft",
        })

        drafts = db.get_drafts_for_email("email-a")
        self.assertEqual([draft_id], [draft["id"] for draft in drafts])
        self.assertEqual("Revised canonical draft", drafts[0]["body"])
        self.assertIn(f"DRAFT UPDATED (id={draft_id})", output)

    def test_apply_draft_patch_updates_only_selected_text(self):
        db = DatabaseService(str(self.data_dir / "emails.db"))
        draft_id = db.create_draft({
            "thread_id": "original-thread",
            "email_id": "email-a",
            "recipient": "sarah@example.com",
            "subject": "Re: Weekly sync",
            "body": "Hello Sarah,\nTuesday works for me.\nBest,\nAlex",
        })

        output = apply_draft_patch.invoke({
            "draft_id": draft_id,
            "original_email_id": "email-a",
            "selection_start": 13,
            "selection_end": 35,
            "replacement": "Wednesday at 10:00 works.\n",
        })

        self.assertIn(f"DRAFT UPDATED (id={draft_id})", output)
        self.assertEqual(
            "Hello Sarah,\nWednesday at 10:00 works.\nBest,\nAlex",
            db.get_drafts_for_email("email-a")[0]["body"],
        )

    def test_read_draft_context_expands_only_when_requested(self):
        db = DatabaseService(str(self.data_dir / "emails.db"))
        body = "Intro context.\n\n" + ("Earlier context. " * 20) + "Selected paragraph." + (" Later context. " * 20)
        draft_id = db.create_draft({
            "thread_id": "original-thread",
            "email_id": "email-a",
            "recipient": "sarah@example.com",
            "subject": "Re: Weekly sync",
            "body": body,
        })

        around = read_draft_context.invoke({
            "draft_id": draft_id,
            "original_email_id": "email-a",
            "scope": "around",
            "selection_start": body.index("Selected paragraph."),
            "selection_end": body.index("Selected paragraph.") + len("Selected paragraph."),
            "context_chars": 10,
        })
        full = read_draft_context.invoke({
            "draft_id": draft_id,
            "original_email_id": "email-a",
            "scope": "full",
        })

        self.assertEqual("around", around["scope"])
        self.assertNotEqual(body, around["body"])
        self.assertIn("Selected paragraph.", around["body"])
        self.assertEqual(body, full["body"])

    def test_read_original_email_context_returns_requested_scope(self):
        db = DatabaseService(str(self.data_dir / "emails.db"))
        body = "Opening. " + ("Earlier details. " * 20) + "The selected request." + (" Later details. " * 20)
        db.insert_email({
            "id": "email-a",
            "subject": "Project proposal",
            "sender_name": "Sarah",
            "sender_email": "sarah@example.com",
            "received_datetime": 1,
            "body_preview": body[:80],
            "body_content": body,
        })

        selected_start = body.index("The selected request.")
        around = read_original_email_context.invoke({
            "original_email_id": "email-a",
            "scope": "around",
            "selection_start": selected_start,
            "selection_end": selected_start + len("The selected request."),
            "context_chars": 10,
        })
        full = read_original_email_context.invoke({
            "original_email_id": "email-a",
            "scope": "full",
        })

        self.assertEqual("email-a", around["email_id"])
        self.assertEqual("Project proposal", around["subject"])
        self.assertEqual("Sarah", around["sender"])
        self.assertEqual("around", around["scope"])
        self.assertIn("The selected request.", around["body"])
        self.assertNotEqual(body, around["body"])
        self.assertEqual(body, full["body"])

    def test_agent_prompt_exposes_original_email_context_for_grounded_rewrites(self):
        self.assertIn("read_original_email_context", agent_graph.SYSTEM_PROMPT)
        self.assertIn("according to the original email", agent_graph.SYSTEM_PROMPT)

    def test_draft_tool_result_builds_draft_stream_event(self):
        build_event = getattr(agent_stream, "_build_draft_event", None)
        self.assertIsNotNone(build_event, "stream._build_draft_event is required")

        event = build_event(
            {"original_email_id": "email-a"},
            "DRAFT UPDATED (id=42). The reply draft is saved.",
        )

        self.assertEqual({
            "type": "draft",
            "draft_id": 42,
            "email_id": "email-a",
        }, event)

    def test_draft_tool_result_ends_agent_turn(self):
        route_after_tools = getattr(agent_graph, "_route_after_tools", None)
        self.assertIsNotNone(route_after_tools, "graph._route_after_tools is required")
        state = {
            "messages": [
                ToolMessage("DRAFT UPDATED (id=42).", tool_call_id="call-1"),
            ],
        }

        self.assertEqual(END, route_after_tools(state))


if __name__ == "__main__":
    unittest.main()
