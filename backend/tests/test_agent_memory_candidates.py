import asyncio
import json
import tempfile
import unittest
from contextlib import contextmanager
from pathlib import Path
from unittest.mock import patch

from langchain_core.messages import AIMessage, ToolMessage

from app.agent import graph as agent_graph
from app.agent import stream as agent_stream
from app.agent.execution_context import (
    current_run_id,
    current_thread_id,
    current_tool_call_id,
)
from app.agent.run_runtime import AgentRunRuntime
from app.agent.tools import apply_draft_patch, save_reply_draft
from app.models.email import DraftReply
from app.models.semantic_memory import (
    SemanticMemoryCandidate,
    SemanticMemoryCandidateEvidence,
)
from app.models.tool_execution import ToolExecution
from app.services.agent_run_store import AgentRunStore
from app.services.database import DatabaseService


async def _collect(stream):
    return [event async for event in stream]


@contextmanager
def _tool_context(thread_id: str, run_id: str, tool_call_id: str):
    thread_token = current_thread_id.set(thread_id)
    run_token = current_run_id.set(run_id)
    call_token = current_tool_call_id.set(tool_call_id)
    try:
        yield
    finally:
        current_tool_call_id.reset(call_token)
        current_run_id.reset(run_token)
        current_thread_id.reset(thread_token)


class _CandidateCallingModel:
    def __init__(self, draft_id):
        self.draft_id = draft_id

    def bind_tools(self, tools):
        self.tool_names = {item.name for item in tools}
        return self

    async def ainvoke(self, messages):
        if isinstance(messages[-1], ToolMessage):
            return AIMessage(content="Draft correction recorded.")
        if "record_memory_candidate" not in self.tool_names:
            return AIMessage(content="Candidate tool unavailable.")
        return AIMessage(
            content="",
            tool_calls=[
                {
                    "name": "save_reply_draft",
                    "args": {
                        "draft_id": self.draft_id,
                        "original_email_id": "dev-email-1",
                        "recipient": "pat@example.com",
                        "subject": "Re: Project update",
                        "body": "Here is the shorter revised draft.",
                    },
                    "id": "revision-call",
                    "type": "tool_call",
                },
                {
                    "name": "record_memory_candidate",
                    "args": {
                        "draft_id": self.draft_id,
                        "memory_type": "preference",
                        "contact_scope": "private-contact@example.com",
                        "key": "Private reply length",
                        "value": "Secret preference value",
                    },
                    "id": "candidate-call",
                    "type": "tool_call",
                }
            ],
        )


class AgentMemoryCandidateTest(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.data_dir = Path(self.temp_dir.name)
        self.data_dir_patch = patch("app.services.database.DATA_DIR", self.data_dir)
        self.data_dir_patch.start()

    def tearDown(self):
        self.data_dir_patch.stop()
        self.temp_dir.cleanup()

    def _seed_draft(self):
        database = DatabaseService(str(self.data_dir / "emails.db"))
        session = database.Session()
        try:
            draft = DraftReply(
                thread_id="seed-thread",
                email_id="dev-email-1",
                recipient="pat@example.com",
                subject="Re: Project update",
                body="Here is the original draft.",
            )
            session.add(draft)
            session.commit()
            return draft.id
        finally:
            session.close()
            database.engine.dispose()

    def _revise_draft(self, *, draft_id, thread_id, run_id):
        with _tool_context(thread_id, run_id, f"{run_id}-revision"):
            return save_reply_draft.invoke(
                {
                    "draft_id": draft_id,
                    "original_email_id": "dev-email-1",
                    "recipient": "pat@example.com",
                    "subject": "Re: Project update",
                    "body": f"Revised draft for {run_id}.",
                }
            )

    def test_candidate_tool_aggregates_threads_and_replays_safely(self):
        try:
            from app.agent.memory_candidate_tools import record_memory_candidate
        except ModuleNotFoundError:
            self.fail("agent memory candidate tool is not implemented")

        draft_id = self._seed_draft()
        args = {
            "draft_id": draft_id,
            "memory_type": "preference",
            "contact_scope": "pat@example.com",
            "key": "Reply length",
            "value": "Keep replies concise.",
        }
        self._revise_draft(draft_id=draft_id, thread_id="thread-1", run_id="run-1")
        with _tool_context("thread-1", "run-1", "call-1"):
            first = record_memory_candidate.invoke(args)
            replay = record_memory_candidate.invoke(args)
        self._revise_draft(draft_id=draft_id, thread_id="thread-2", run_id="run-2")
        with _tool_context("thread-2", "run-2", "call-2"):
            suggested = record_memory_candidate.invoke(args)

        database = DatabaseService(str(self.data_dir / "emails.db"))
        session = database.Session()
        try:
            candidates = session.query(SemanticMemoryCandidate).all()
            evidence = session.query(SemanticMemoryCandidateEvidence).all()
            executions = session.query(ToolExecution).all()
        finally:
            session.close()
            database.engine.dispose()

        self.assertEqual(first, replay)
        self.assertEqual("pending", first["candidate"]["status"])
        self.assertEqual("suggested", suggested["candidate"]["status"])
        self.assertEqual(2, suggested["candidate"]["evidence_count"])
        self.assertEqual(1, len(candidates))
        self.assertEqual(2, len(evidence))
        candidate_executions = [
            item for item in executions
            if item.operation == "record_memory_candidate"
        ]
        self.assertEqual(2, len(candidate_executions))
        self.assertEqual(
            {"draft_id", "memory_type", "contact_scope", "key", "value"},
            set(record_memory_candidate.args_schema.model_fields),
        )
        serialized_results = json.dumps([item.result for item in candidate_executions])
        self.assertNotIn("Keep replies concise.", serialized_results)
        self.assertNotIn("Reply length", serialized_results)
        self.assertNotIn("pat@example.com", serialized_results)

    def test_candidate_tool_is_registered_without_hitl(self):
        from app.agent.tools import HIGH_RISK_TOOLS, TOOLS_BY_NAME

        self.assertIn("record_memory_candidate", TOOLS_BY_NAME)
        self.assertNotIn("record_memory_candidate", HIGH_RISK_TOOLS)

    def test_candidate_tool_rejects_without_same_run_draft_revision(self):
        from app.agent.memory_candidate_tools import record_memory_candidate

        with _tool_context("thread-1", "run-without-revision", "candidate-call"):
            with self.assertRaisesRegex(RuntimeError, "successful draft revision"):
                record_memory_candidate.invoke(
                    {
                        "draft_id": 42,
                        "memory_type": "preference",
                        "contact_scope": "pat@example.com",
                        "key": "Reply length",
                        "value": "Keep replies concise.",
                    }
                )

        database = DatabaseService(str(self.data_dir / "emails.db"))
        session = database.Session()
        try:
            self.assertEqual(0, session.query(SemanticMemoryCandidate).count())
            self.assertEqual(0, session.query(SemanticMemoryCandidateEvidence).count())
        finally:
            session.close()
            database.engine.dispose()

    def test_candidate_tool_rejects_new_draft_as_revision_evidence(self):
        from app.agent.memory_candidate_tools import record_memory_candidate

        with _tool_context("thread-1", "run-new-draft", "create-draft-call"):
            save_reply_draft.invoke(
                {
                    "original_email_id": "dev-email-1",
                    "recipient": "pat@example.com",
                    "subject": "Re: Project update",
                    "body": "Initial draft.",
                }
            )

        database = DatabaseService(str(self.data_dir / "emails.db"))
        session = database.Session()
        try:
            draft_id = session.query(DraftReply.id).one()[0]
        finally:
            session.close()
            database.engine.dispose()

        with _tool_context("thread-1", "run-new-draft", "candidate-call"):
            with self.assertRaisesRegex(RuntimeError, "successful draft revision"):
                record_memory_candidate.invoke(
                    {
                        "draft_id": draft_id,
                        "memory_type": "preference",
                        "contact_scope": None,
                        "key": "Reply tone",
                        "value": "Use a warmer tone.",
                    }
                )

    def test_candidate_tool_requires_matching_run_and_draft(self):
        from app.agent.memory_candidate_tools import record_memory_candidate

        revised_draft_id = self._seed_draft()
        other_draft_id = self._seed_draft()
        self._revise_draft(
            draft_id=revised_draft_id,
            thread_id="thread-1",
            run_id="revision-run",
        )
        candidate_args = {
            "memory_type": "preference",
            "contact_scope": None,
            "key": "Reply tone",
            "value": "Use a warmer tone.",
        }

        with _tool_context("thread-1", "different-run", "candidate-other-run"):
            with self.assertRaisesRegex(RuntimeError, "successful draft revision"):
                record_memory_candidate.invoke(
                    {**candidate_args, "draft_id": revised_draft_id}
                )
        with _tool_context("thread-1", "revision-run", "candidate-other-draft"):
            with self.assertRaisesRegex(RuntimeError, "successful draft revision"):
                record_memory_candidate.invoke(
                    {**candidate_args, "draft_id": other_draft_id}
                )

    def test_candidate_tool_accepts_same_run_patch_evidence(self):
        from app.agent.memory_candidate_tools import record_memory_candidate

        draft_id = self._seed_draft()
        with _tool_context("thread-1", "patch-run", "patch-call"):
            apply_draft_patch.invoke(
                {
                    "draft_id": draft_id,
                    "original_email_id": "dev-email-1",
                    "selection_start": 0,
                    "selection_end": 4,
                    "replacement": "This",
                }
            )
        with _tool_context("thread-1", "patch-run", "candidate-call"):
            result = record_memory_candidate.invoke(
                {
                    "draft_id": draft_id,
                    "memory_type": "preference",
                    "contact_scope": None,
                    "key": "Reply opening",
                    "value": "Start replies directly.",
                }
            )

        self.assertEqual("pending", result["candidate"]["status"])
        self.assertEqual(1, result["candidate"]["evidence_count"])

    def test_agent_candidate_trace_is_sanitized_and_never_interrupts(self):
        database = DatabaseService(str(self.data_dir / "emails.db"))
        draft_id = self._seed_draft()
        agent = agent_graph.build_agent(llm=_CandidateCallingModel(draft_id))
        runtime = AgentRunRuntime(
            AgentRunStore(database),
            provider="fixture-provider",
            stream=lambda graph_input, thread_id: agent_stream.stream_agent(
                graph_input,
                thread_id,
                agent=agent,
            ),
        )

        async def exercise(thread_id):
            return await _collect(
                runtime.stream_new_run(
                    agent_stream.new_turn_input("Make this draft shorter."),
                    thread_id,
                )
            )

        first_events = asyncio.run(exercise("candidate-thread-1"))
        second_events = asyncio.run(exercise("candidate-thread-2"))
        events = [*first_events, *second_events]

        session = database.Session()
        try:
            candidate = session.query(SemanticMemoryCandidate).one()
        finally:
            session.close()
            database.engine.dispose()

        self.assertEqual("suggested", candidate.status)
        self.assertFalse(any(event["type"] == "interrupt" for event in events))
        serialized = json.dumps(events)
        self.assertNotIn("Secret preference value", serialized)
        self.assertNotIn("Private reply length", serialized)
        self.assertNotIn("private-contact@example.com", serialized)
        candidate_outcomes = [
            event["outcome"]
            for event in events
            if event.get("tool") == "record_memory_candidate"
            and event.get("step") == "tool_end"
        ]
        self.assertEqual(2, len(candidate_outcomes))
        self.assertEqual(
            "semantic_memory_candidate",
            candidate_outcomes[-1]["kind"],
        )
        self.assertEqual("suggested", candidate_outcomes[-1]["status"])


if __name__ == "__main__":
    unittest.main()
