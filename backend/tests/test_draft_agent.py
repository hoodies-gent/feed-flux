import asyncio
import tempfile
import unittest
from contextlib import contextmanager
from pathlib import Path
from unittest.mock import patch

from langchain_core.messages import AIMessage, ToolMessage
from langgraph.graph import END

from app.agent import graph as agent_graph
from app.agent import stream as agent_stream
from app.agent.execution_context import (
    current_run_id,
    current_thread_id,
    current_tool_call_id,
)
from app.agent.run_runtime import AgentRunRuntime
from app.agent.tools import (
    HIGH_RISK_TOOLS,
    TOOLS_BY_NAME,
    apply_draft_patch,
    read_draft_context,
    read_original_email_context,
    save_reply_draft,
    send_test_email,
)
from app.models.email import DraftReply, SentAction
from app.services.agent_run_store import AgentRunStore
from app.services.database import DatabaseService


class _SaveReplyDraftLLM:
    def bind_tools(self, tools):
        return self

    async def ainvoke(self, messages):
        return AIMessage(
            content="",
            tool_calls=[
                {
                    "name": "save_reply_draft",
                    "args": {
                        "original_email_id": "email-runtime-context",
                        "recipient": "sarah@example.com",
                        "subject": "Re: Runtime context",
                        "body": "Create this draft with the runtime context.",
                    },
                    "id": "runtime-call-9",
                    "type": "tool_call",
                }
            ],
        )


class _PromptAwareSendTestEmailLLM:
    def bind_tools(self, tools):
        return self

    async def ainvoke(self, messages):
        system_prompt = messages[0].content
        if "send_test_email" not in system_prompt:
            return AIMessage(content="I can't send that directly.")
        return AIMessage(
            content="",
            tool_calls=[
                {
                    "name": "send_test_email",
                    "args": {
                        "recipient": "test@example.com",
                        "subject": "reliability-check",
                        "body": "checkpoint test",
                    },
                    "id": "send-test-call-1",
                    "type": "tool_call",
                }
            ],
        )


@contextmanager
def _tool_execution_context(thread_id: str, run_id: str, tool_call_id: str):
    thread_token = current_thread_id.set(thread_id)
    run_token = current_run_id.set(run_id)
    call_token = current_tool_call_id.set(tool_call_id)
    try:
        yield
    finally:
        current_tool_call_id.reset(call_token)
        current_run_id.reset(run_token)
        current_thread_id.reset(thread_token)


class DraftAgentTest(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.data_dir = Path(self.temp_dir.name)
        self.data_dir_patch = patch("app.services.database.DATA_DIR", self.data_dir)
        self.data_dir_patch.start()

    def tearDown(self):
        self.data_dir_patch.stop()
        self.temp_dir.cleanup()

    def test_reply_draft_tool_uses_truthful_canonical_name(self):
        self.assertIn("save_reply_draft", TOOLS_BY_NAME)
        self.assertNotIn("send_reply", TOOLS_BY_NAME)

    def test_save_reply_draft_creates_draft_without_recording_send(self):
        with _tool_execution_context("agent-thread", "run-create", "call-create"):
            output = save_reply_draft.invoke({
                "original_email_id": "email-a",
                "recipient": "sarah@example.com",
                "subject": "Re: Weekly sync",
                "body": "Tuesday works for me.",
            })

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
        self.assertNotIn("save_reply_draft", HIGH_RISK_TOOLS)

    def test_save_reply_draft_replays_same_tool_call_without_duplicate_draft(self):
        from app.models.tool_execution import ToolExecution

        args = {
            "original_email_id": "email-idempotent",
            "recipient": "sarah@example.com",
            "subject": "Re: Reliable draft",
            "body": "Create this draft once.",
        }
        with _tool_execution_context("idempotent-thread", "run-123", "call-7"):
            first_output = save_reply_draft.invoke(args)
            first_replay = save_reply_draft.invoke(args)
            second_replay = save_reply_draft.invoke(args)

        db = DatabaseService(str(self.data_dir / "emails.db"))
        drafts = db.get_drafts_for_email("email-idempotent")
        session = db.Session()
        try:
            executions = session.query(ToolExecution).all()
        finally:
            session.close()
            db.engine.dispose()

        self.assertEqual(first_output, first_replay)
        self.assertEqual(first_output, second_replay)
        self.assertIn("DRAFT READY", second_replay)
        self.assertEqual(1, len(drafts))
        self.assertEqual(1, len(executions))
        self.assertEqual("save_reply_draft", executions[0].operation)
        self.assertEqual("run-123", executions[0].run_id)
        self.assertEqual("call-7", executions[0].tool_call_id)

    def test_runtime_links_saved_draft_outcome_to_tool_call(self):
        from app.models.tool_execution import ToolExecution

        db = DatabaseService(str(self.data_dir / "emails.db"))
        agent = agent_graph.build_agent(llm=_SaveReplyDraftLLM())

        def stream(graph_input, thread_id):
            return agent_stream.stream_agent(
                graph_input,
                thread_id,
                agent=agent,
            )

        run_store = AgentRunStore(db)
        runtime = AgentRunRuntime(
            run_store,
            provider="fixture-provider",
            stream=stream,
        )

        async def exercise():
            return [
                event
                async for event in runtime.stream_new_run(
                    agent_stream.new_turn_input("Create a reply draft"),
                    "runtime-context-thread",
                )
            ]

        events = asyncio.run(exercise())
        run_id = next(event["run_id"] for event in events if event["type"] == "run")
        drafts = db.get_drafts_for_email("email-runtime-context")
        session = db.Session()
        try:
            execution = session.query(ToolExecution).one()
        finally:
            session.close()
            db.engine.dispose()

        run_events = run_store.list_events(run_id)
        tool_call = next(
            event
            for event in run_events
            if event["event_type"] == "tool_call"
        )
        tool_result = next(
            event
            for event in run_events
            if event["event_type"] == "tool_result"
        )

        self.assertEqual(1, len(drafts))
        self.assertEqual(run_id, execution.run_id)
        self.assertEqual("runtime-call-9", execution.tool_call_id)
        self.assertEqual("runtime-call-9", tool_call["tool_call_id"])
        self.assertEqual("save_reply_draft", tool_call["tool_name"])
        self.assertEqual("fixture-provider", tool_call["provider"])
        self.assertEqual("runtime-call-9", tool_result["tool_call_id"])
        self.assertEqual("save_reply_draft", tool_result["tool_name"])
        self.assertEqual("fixture-provider", tool_result["provider"])
        self.assertEqual(
            {
                "schema_version": 1,
                "kind": "draft",
                "draft_id": drafts[0]["id"],
                "email_id": "email-runtime-context",
                "result": "created",
            },
            tool_result["outcome"],
        )
        self.assertNotIn("recipient", tool_result["outcome"])
        self.assertNotIn("body", tool_result["outcome"])

    def test_save_reply_draft_updates_existing_draft_when_draft_id_is_given(self):
        db = DatabaseService(str(self.data_dir / "emails.db"))
        draft_id = db.create_draft({
            "thread_id": "original-thread",
            "email_id": "email-a",
            "recipient": "sarah@example.com",
            "subject": "Re: Weekly sync",
            "body": "Original draft",
        })

        with _tool_execution_context("revision-thread", "run-update", "call-update"):
            output = save_reply_draft.invoke({
                "draft_id": draft_id,
                "original_email_id": "email-a",
                "recipient": "sarah@example.com",
                "subject": "Re: Weekly sync",
                "body": "Revised draft",
            })

        drafts = db.get_drafts_for_email("email-a")
        self.assertEqual([draft_id], [draft["id"] for draft in drafts])
        self.assertEqual("Revised draft", drafts[0]["body"])
        self.assertIn(f"DRAFT UPDATED (id={draft_id})", output)

    def test_save_reply_draft_creates_new_draft_when_requested_draft_was_discarded(self):
        db = DatabaseService(str(self.data_dir / "emails.db"))
        discarded_id = db.create_draft({
            "thread_id": "original-thread",
            "email_id": "email-a",
            "recipient": "sarah@example.com",
            "subject": "Re: Weekly sync",
            "body": "Discarded draft",
        })
        db.discard_draft(discarded_id)

        with _tool_execution_context("revision-thread", "run-recreate", "call-recreate"):
            output = save_reply_draft.invoke({
                "draft_id": discarded_id,
                "original_email_id": "email-a",
                "recipient": "sarah@example.com",
                "subject": "Re: Weekly sync",
                "body": "Fresh draft after discard",
            })

        drafts = db.get_drafts_for_email("email-a")
        self.assertEqual(1, len(drafts))
        self.assertNotEqual(discarded_id, drafts[0]["id"])
        self.assertEqual("Fresh draft after discard", drafts[0]["body"])
        self.assertIn(f"DRAFT READY (id={drafts[0]['id']})", output)

        session = db.Session()
        try:
            discarded = session.get(DraftReply, discarded_id)
        finally:
            session.close()
        self.assertEqual("discarded", discarded.status)

    def test_save_reply_draft_reuses_latest_active_draft_by_default(self):
        db = DatabaseService(str(self.data_dir / "emails.db"))
        draft_id = db.create_draft({
            "thread_id": "original-thread",
            "email_id": "email-a",
            "recipient": "sarah@example.com",
            "subject": "Re: Weekly sync",
            "body": "Original draft",
        })

        with _tool_execution_context("reuse-thread", "run-reuse", "call-reuse"):
            output = save_reply_draft.invoke({
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

        with _tool_execution_context("patch-thread", "run-patch-once", "call-patch-once"):
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

    def test_apply_draft_patch_replays_same_tool_call_without_reapplying_patch(self):
        from app.models.tool_execution import ToolExecution

        db = DatabaseService(str(self.data_dir / "emails.db"))
        draft_id = db.create_draft({
            "thread_id": "original-thread",
            "email_id": "email-idempotent-patch",
            "recipient": "sarah@example.com",
            "subject": "Re: Weekly sync",
            "body": "Hello Sarah,\nTuesday works for me.\nBest,\nAlex",
        })
        args = {
            "draft_id": draft_id,
            "original_email_id": "email-idempotent-patch",
            "selection_start": 13,
            "selection_end": 35,
            "replacement": "Wednesday at 10:00 works.\n",
        }

        with _tool_execution_context("patch-thread", "run-patch", "call-patch"):
            first_output = apply_draft_patch.invoke(args)
            first_replay = apply_draft_patch.invoke(args)
            second_replay = apply_draft_patch.invoke(args)

        patched_body = db.get_drafts_for_email("email-idempotent-patch")[0]["body"]
        session = db.Session()
        try:
            executions = session.query(ToolExecution).all()
        finally:
            session.close()
            db.engine.dispose()

        self.assertEqual(first_output, first_replay)
        self.assertEqual(first_output, second_replay)
        self.assertEqual(
            "Hello Sarah,\nWednesday at 10:00 works.\nBest,\nAlex",
            patched_body,
        )
        self.assertEqual(1, len(executions))
        self.assertEqual("apply_draft_patch", executions[0].operation)

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

    def test_agent_prompt_routes_explicit_test_email_to_approval_tool(self):
        agent = agent_graph.build_agent(llm=_PromptAwareSendTestEmailLLM())

        async def collect_events():
            return [
                event
                async for event in agent_stream.stream_agent(
                    agent_stream.new_turn_input(
                        'Send a test email to test@example.com with subject "reliability-check" '
                        'and body "checkpoint test".'
                    ),
                    "test-email-prompt-thread",
                    agent=agent,
                )
            ]

        events = asyncio.run(collect_events())
        interrupts = [event for event in events if event["type"] == "interrupt"]
        self.assertEqual(1, len(interrupts))
        self.assertEqual("send_test_email", interrupts[0]["tool"])

    def test_send_test_email_reports_completed_local_dry_run(self):
        output = send_test_email.invoke({
            "recipient": "test@example.com",
            "subject": "reliability-check",
            "body": "checkpoint test",
        })

        self.assertIn("TEST EMAIL RECORDED (dry-run)", output)
        self.assertIn("no external email was sent", output)
        self.assertNotIn("queued for approval", output)

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
