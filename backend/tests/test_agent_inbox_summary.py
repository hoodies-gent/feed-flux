import asyncio
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from langchain_core.messages import AIMessage
from pydantic import ValidationError

from app.agent import tools as agent_tools
from app.agent.graph import ToolExecutionFailure, build_agent
from app.agent.stream import new_turn_input, stream_agent
from app.services.database import DatabaseService


class _SummaryThenWriteModel:
    def __init__(self):
        self.attempt_count = 0

    def bind_tools(self, tools):
        return self

    async def ainvoke(self, messages):
        self.attempt_count += 1
        if self.attempt_count == 1:
            return AIMessage(
                content="",
                tool_calls=[{
                    "name": "list_inbox_emails",
                    "args": {
                        "scope": "unread",
                        "purpose": "overview",
                        "limit": 1,
                    },
                    "id": "summary-listing",
                    "type": "tool_call",
                }],
            )
        if self.attempt_count == 2:
            return AIMessage(
                content="",
                tool_calls=[
                    {
                        "name": "list_inbox_emails",
                        "args": {
                            "scope": "unread",
                            "purpose": "triage",
                            "limit": 1,
                        },
                        "id": "summary-triage-listing-attempt",
                        "type": "tool_call",
                    },
                    {
                        "name": "apply_triage_batch",
                        "args": {
                            "actions": [{
                                "email_id": "summary-email",
                                "action": "mark_read",
                                "reason": "low signal",
                            }],
                            "needs_reply": [],
                        },
                        "id": "summary-write-attempt",
                        "type": "tool_call",
                    },
                ],
            )
        return AIMessage(content="I covered the 1 most recent unread email.")


class _SummaryModel:
    def __init__(self):
        self.attempt_count = 0

    def bind_tools(self, tools):
        return self

    async def ainvoke(self, messages):
        self.attempt_count += 1
        if self.attempt_count == 1:
            return AIMessage(
                content="",
                tool_calls=[{
                    "name": "list_inbox_emails",
                    "args": {
                        "scope": "recent",
                        "purpose": "overview",
                        "limit": 2,
                    },
                    "id": "recent-summary-listing",
                    "type": "tool_call",
                }],
            )
        return AIMessage(content="I covered the 2 most recent inbox emails.")


class _RepeatedSummaryModel:
    def __init__(self):
        self.attempt_count = 0

    def bind_tools(self, tools):
        return self

    async def ainvoke(self, messages):
        self.attempt_count += 1
        if self.attempt_count == 1:
            return AIMessage(
                content="",
                tool_calls=[
                    {
                        "name": "list_inbox_emails",
                        "args": {
                            "scope": "unread",
                            "purpose": "overview",
                            "limit": 5,
                        },
                        "id": "summary-listing-first",
                        "type": "tool_call",
                    },
                    {
                        "name": "list_inbox_emails",
                        "args": {
                            "scope": "unread",
                            "purpose": "overview",
                            "limit": 5,
                        },
                        "id": "summary-listing-duplicate",
                        "type": "tool_call",
                    },
                ],
            )
        return AIMessage(content="No unread emails were found.")


class _TriageModel:
    def __init__(self):
        self.attempt_count = 0

    def bind_tools(self, tools):
        return self

    async def ainvoke(self, messages):
        self.attempt_count += 1
        if self.attempt_count == 1:
            return AIMessage(
                content="",
                tool_calls=[{
                    "name": "list_inbox_emails",
                    "args": {
                        "scope": "unread",
                        "purpose": "triage",
                        "limit": 1,
                    },
                    "id": "triage-listing",
                    "type": "tool_call",
                }],
            )
        if self.attempt_count == 2:
            return AIMessage(
                content="",
                tool_calls=[{
                    "name": "apply_triage_batch",
                    "args": {
                        "actions": [{
                            "email_id": "triage-email",
                            "action": "mark_read",
                            "reason": "status update",
                        }],
                        "needs_reply": [],
                    },
                    "id": "triage-plan",
                    "type": "tool_call",
                }],
            )
        return AIMessage(content="The triage plan is ready.")


class InboxListingToolTest(unittest.TestCase):
    def test_unread_scope_returns_newest_bounded_metadata(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            db_path = str(Path(temp_dir) / "emails.db")
            database = DatabaseService(db_path)
            database.insert_email({
                "id": "older-unread",
                "subject": "Older unread",
                "sender_name": "Older Sender",
                "sender_email": "older@example.com",
                "received_datetime": 100,
                "body_preview": "Older preview",
                "is_read": False,
            })
            database.insert_email({
                "id": "newer-unread",
                "subject": "Newer unread",
                "sender_name": None,
                "sender_email": "newer@example.com",
                "received_datetime": 200,
                "body_preview": "Newer preview",
                "is_read": False,
            })
            database.insert_email({
                "id": "newest-read",
                "subject": "Newest read",
                "sender_name": "Read Sender",
                "sender_email": "read@example.com",
                "received_datetime": 300,
                "body_preview": "Read preview",
                "is_read": True,
            })

            with patch.dict(os.environ, {"FEEDFLUX_DB_PATH": db_path}):
                result = agent_tools.TOOLS_BY_NAME["list_inbox_emails"].invoke({
                    "scope": "unread",
                    "purpose": "overview",
                    "limit": 1,
                })
            database.engine.dispose()

        self.assertEqual(
            {
                "scope": "unread",
                "purpose": "overview",
                "limit": 1,
                "returned_count": 1,
                "emails": [{
                    "citation_key": "inbox-1",
                    "email_id": "newer-unread",
                    "subject": "Newer unread",
                    "sender": "newer@example.com",
                    "received_time": "1970-01-01T00:03:20Z",
                    "preview": "Newer preview",
                }],
            },
            result,
        )

    def test_recent_scope_includes_read_and_unread_but_not_archived_or_deleted(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            db_path = str(Path(temp_dir) / "emails.db")
            database = DatabaseService(db_path)
            for email_id, received, is_read, is_archived, is_deleted in (
                ("recent-read", 400, True, False, False),
                ("recent-unread", 300, False, False, False),
                ("archived", 500, False, True, False),
                ("deleted", 600, False, False, True),
            ):
                database.insert_email({
                    "id": email_id,
                    "subject": email_id,
                    "sender_name": "Fixture Sender",
                    "sender_email": "fixture@example.com",
                    "received_datetime": received,
                    "body_preview": f"{email_id} preview",
                    "is_read": is_read,
                    "is_archived": is_archived,
                    "is_deleted": is_deleted,
                })

            with patch.dict(os.environ, {"FEEDFLUX_DB_PATH": db_path}):
                result = agent_tools.TOOLS_BY_NAME["list_inbox_emails"].invoke({
                    "scope": "recent",
                    "purpose": "overview",
                    "limit": 10,
                })
            database.engine.dispose()

        self.assertEqual("recent", result["scope"])
        self.assertEqual(2, result["returned_count"])
        self.assertEqual(
            ["recent-read", "recent-unread"],
            [email["email_id"] for email in result["emails"]],
        )

    def test_attention_purpose_returns_bounded_unread_metadata(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            db_path = str(Path(temp_dir) / "emails.db")
            database = DatabaseService(db_path)
            database.insert_email({
                "id": "attention-email",
                "subject": "Decision needed",
                "sender_name": "Fixture Sender",
                "sender_email": "fixture@example.com",
                "received_datetime": 100,
                "body_preview": "Please confirm the launch date.",
                "is_read": False,
            })

            with patch.dict(os.environ, {"FEEDFLUX_DB_PATH": db_path}):
                result = agent_tools.TOOLS_BY_NAME["list_inbox_emails"].invoke({
                    "scope": "unread",
                    "purpose": "attention",
                    "limit": 1,
                })
            database.engine.dispose()

        self.assertEqual("unread", result["scope"])
        self.assertEqual("attention", result["purpose"])
        self.assertEqual(1, result["limit"])
        self.assertEqual(1, result["returned_count"])
        self.assertEqual("attention-email", result["emails"][0]["email_id"])

    def test_empty_scope_returns_explicit_zero_coverage(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            db_path = str(Path(temp_dir) / "emails.db")
            database = DatabaseService(db_path)

            with patch.dict(os.environ, {"FEEDFLUX_DB_PATH": db_path}):
                result = agent_tools.TOOLS_BY_NAME["list_inbox_emails"].invoke({
                    "scope": "unread",
                    "purpose": "overview",
                    "limit": 20,
                })
            database.engine.dispose()

        self.assertEqual(
            {
                "scope": "unread",
                "purpose": "overview",
                "limit": 20,
                "returned_count": 0,
                "emails": [],
            },
            result,
        )

    def test_limit_above_hard_cap_is_rejected(self):
        with self.assertRaises(ValidationError):
            agent_tools.TOOLS_BY_NAME["list_inbox_emails"].invoke({
                "scope": "unread",
                "purpose": "overview",
                "limit": 51,
            })

    def test_legacy_summary_purpose_is_rejected(self):
        with self.assertRaises(ValidationError):
            agent_tools.TOOLS_BY_NAME["list_inbox_emails"].invoke({
                "scope": "recent",
                "purpose": "summary",
                "limit": 20,
            })


class InboxSummaryAgentTest(unittest.TestCase):
    def test_triage_uses_generic_unread_listing_before_plan(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            db_path = str(Path(temp_dir) / "emails.db")
            database = DatabaseService(db_path)
            database.insert_email({
                "id": "triage-email",
                "subject": "Triage source",
                "sender_name": "Fixture Sender",
                "sender_email": "fixture@example.com",
                "received_datetime": 100,
                "body_preview": "Triage preview",
                "is_read": False,
            })
            model = _TriageModel()
            agent = build_agent(llm=model)

            async def exercise():
                return [
                    event
                    async for event in stream_agent(
                        new_turn_input("Triage my unread emails"),
                        "generic-listing-triage-thread",
                        agent=agent,
                    )
                ]

            with patch.dict(os.environ, {"FEEDFLUX_DB_PATH": db_path}):
                events = asyncio.run(exercise())
            database.engine.dispose()

        started_tools = [
            event["tool"]
            for event in events
            if event.get("type") == "trace" and event.get("step") == "tool_start"
        ]
        self.assertEqual(
            ["list_inbox_emails", "apply_triage_batch"],
            started_tools,
        )
        self.assertEqual(1, len([
            event for event in events if event.get("type") == "plan"
        ]))

    def test_summary_executes_only_one_listing_per_user_turn(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            db_path = str(Path(temp_dir) / "emails.db")
            database = DatabaseService(db_path)
            model = _RepeatedSummaryModel()
            agent = build_agent(llm=model)

            async def exercise():
                return [
                    event
                    async for event in stream_agent(
                        new_turn_input("Summarize my unread emails"),
                        "summary-single-listing-thread",
                        agent=agent,
                    )
                ]

            with patch.dict(os.environ, {"FEEDFLUX_DB_PATH": db_path}):
                events = asyncio.run(exercise())
            database.engine.dispose()

        started_tools = [
            event["tool"]
            for event in events
            if event.get("type") == "trace" and event.get("step") == "tool_start"
        ]
        self.assertEqual(["list_inbox_emails"], started_tools)
        self.assertEqual(2, model.attempt_count)

    def test_listing_failure_stops_summary_without_fallback_tools(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            db_path = str(Path(temp_dir) / "emails.db")
            database = DatabaseService(db_path)
            model = _SummaryModel()
            agent = build_agent(llm=model)
            events = []

            async def exercise():
                async for event in stream_agent(
                    new_turn_input("Summarize my recent emails"),
                    "summary-failure-thread",
                    agent=agent,
                ):
                    events.append(event)

            with (
                patch.dict(os.environ, {"FEEDFLUX_DB_PATH": db_path}),
                patch(
                    "app.agent.tools.DatabaseService.get_inbox_emails",
                    side_effect=RuntimeError("fixture listing failure"),
                ),
                self.assertRaisesRegex(
                    ToolExecutionFailure,
                    "Tool list_inbox_emails failed",
                ),
            ):
                asyncio.run(exercise())
            database.engine.dispose()

        started_tools = [
            event["tool"]
            for event in events
            if event.get("type") == "trace" and event.get("step") == "tool_start"
        ]
        self.assertEqual(["list_inbox_emails"], started_tools)
        self.assertEqual(1, model.attempt_count)
        self.assertNotIn("plan", [event.get("type") for event in events])

    def test_summary_listing_emits_source_references(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            db_path = str(Path(temp_dir) / "emails.db")
            database = DatabaseService(db_path)
            for email_id, subject, sender, received in (
                ("source-2", "Second source", "Second Sender", 200),
                ("source-1", "First source", "First Sender", 100),
            ):
                database.insert_email({
                    "id": email_id,
                    "subject": subject,
                    "sender_name": sender,
                    "sender_email": f"{email_id}@example.com",
                    "received_datetime": received,
                    "body_preview": f"{subject} preview",
                    "is_read": False,
                })
            model = _SummaryModel()
            agent = build_agent(llm=model)

            async def exercise():
                return [
                    event
                    async for event in stream_agent(
                        new_turn_input("Summarize my recent emails"),
                        "summary-reference-thread",
                        agent=agent,
                    )
                ]

            with patch.dict(os.environ, {"FEEDFLUX_DB_PATH": db_path}):
                events = asyncio.run(exercise())
            database.engine.dispose()

        references = [
            event for event in events if event.get("type") == "references"
        ]
        self.assertEqual(
            [{
                "type": "references",
                "references": [
                    {
                        "citation_key": "inbox-1",
                        "email_id": "source-2",
                        "subject": "Second source",
                        "sender": "Second Sender",
                    },
                    {
                        "citation_key": "inbox-2",
                        "email_id": "source-1",
                        "subject": "First source",
                        "sender": "First Sender",
                    },
                ],
            }],
            references,
        )

    def test_summary_listing_blocks_followup_write_tool(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            db_path = str(Path(temp_dir) / "emails.db")
            database = DatabaseService(db_path)
            database.insert_email({
                "id": "summary-email",
                "subject": "Summary source",
                "sender_name": "Fixture Sender",
                "sender_email": "fixture@example.com",
                "received_datetime": 100,
                "body_preview": "Summary preview",
                "is_read": False,
            })
            model = _SummaryThenWriteModel()
            agent = build_agent(llm=model)

            async def exercise():
                return [
                    event
                    async for event in stream_agent(
                        new_turn_input("Summarize my unread emails"),
                        "summary-read-only-thread",
                        agent=agent,
                    )
                ]

            with patch.dict(os.environ, {"FEEDFLUX_DB_PATH": db_path}):
                events = asyncio.run(exercise())
            database.engine.dispose()

        started_tools = [
            event["tool"]
            for event in events
            if event.get("type") == "trace" and event.get("step") == "tool_start"
        ]
        self.assertEqual(["list_inbox_emails"], started_tools)
        self.assertNotIn("plan", [event.get("type") for event in events])
        self.assertEqual(3, model.attempt_count)


if __name__ == "__main__":
    unittest.main()
