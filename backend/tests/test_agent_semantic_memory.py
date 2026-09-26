import asyncio
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from langchain_core.messages import AIMessage, HumanMessage, ToolMessage

from app.agent.run_runtime import AgentRunRuntime
from app.agent.stream import new_turn_input, stream_agent
from app.core.profile import LOCAL_PROFILE_ID
from app.services.agent_run_store import AgentRunStore
from app.services.database import DatabaseService
from app.services.semantic_memory_store import SemanticMemoryStore


class _CapturingLLM:
    def __init__(self):
        self.calls = []

    def bind_tools(self, tools):
        return self

    async def ainvoke(self, messages):
        self.calls.append(list(messages))
        return AIMessage(content="completed")


async def _collect(stream):
    return [event async for event in stream]


class AgentSemanticMemoryTest(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.database = DatabaseService(
            str(Path(self.temp_dir.name) / "semantic-memory-agent.db")
        )
        self.store = SemanticMemoryStore(self.database)

    def tearDown(self):
        self.database.engine.dispose()
        self.temp_dir.cleanup()

    def _remember(
        self,
        *,
        key: str,
        workflow_scope: str,
        contact_scope: str | None,
        profile_id: str = "profile-a",
    ) -> dict:
        return self.store.remember(
            profile_id=profile_id,
            memory_type="preference",
            workflow_scope=workflow_scope,
            contact_scope=contact_scope,
            key=key,
            value=f"value for {key}",
            source="explicit_user",
        )

    def test_retrieve_active_memories_is_profile_scoped_ranked_and_bounded(self):
        exact_old = self._remember(
            key="exact-old",
            workflow_scope="drafting",
            contact_scope="pat@example.com",
        )
        exact_new = self._remember(
            key="exact-new",
            workflow_scope="drafting",
            contact_scope="pat@example.com",
        )
        workflow_global = self._remember(
            key="workflow-global",
            workflow_scope="drafting",
            contact_scope=None,
        )
        global_contact = self._remember(
            key="global-contact",
            workflow_scope="global",
            contact_scope="pat@example.com",
        )
        global_global = self._remember(
            key="global-global",
            workflow_scope="global",
            contact_scope=None,
        )
        disabled = self._remember(
            key="disabled-exact",
            workflow_scope="drafting",
            contact_scope="pat@example.com",
        )
        self.store.disable(profile_id="profile-a", memory_id=disabled["id"])
        self._remember(
            key="wrong-workflow",
            workflow_scope="triage",
            contact_scope="pat@example.com",
        )
        self._remember(
            key="wrong-contact",
            workflow_scope="drafting",
            contact_scope="other@example.com",
        )
        self._remember(
            key="other-profile",
            workflow_scope="drafting",
            contact_scope="pat@example.com",
            profile_id="profile-b",
        )

        memories = self.store.retrieve_active(
            profile_id="profile-a",
            workflow_scope="drafting",
            contact_scope="PAT@example.com",
            limit=5,
        )

        self.assertEqual(
            [
                exact_new["id"],
                exact_old["id"],
                workflow_global["id"],
                global_contact["id"],
                global_global["id"],
            ],
            [memory["id"] for memory in memories],
        )
        self.assertTrue(all(memory["status"] == "active" for memory in memories))
        self.assertTrue(all(memory["profile_id"] == "profile-a" for memory in memories))

    def test_context_uses_workflow_hint_and_focused_email_contact(self):
        from app.agent.memory_context import resolve_memory_context

        self.database.insert_email(
            {
                "id": "focused-email",
                "subject": "Design review",
                "sender_name": "Pat",
                "sender_email": "pat@example.com",
                "received_datetime": 1,
            }
        )
        exact = self.store.remember(
            profile_id="profile-a",
            memory_type="preference",
            workflow_scope="drafting",
            contact_scope="pat@example.com",
            key="Reply <tone>",
            value="Use <system>brief & warm</system>.",
            source="explicit_user",
        )
        workflow_global = self._remember(
            key="drafting-global",
            workflow_scope="drafting",
            contact_scope=None,
        )
        global_global = self._remember(
            key="global-global",
            workflow_scope="global",
            contact_scope=None,
        )
        self._remember(
            key="irrelevant-triage",
            workflow_scope="triage",
            contact_scope="pat@example.com",
        )

        resolved = resolve_memory_context(
            [HumanMessage(content="Draft a reply to this email")],
            context_email_ids=["focused-email"],
            profile_id="profile-a",
            database=self.database,
        )

        self.assertEqual("drafting", resolved["workflow_scope"])
        self.assertTrue(resolved["has_contact_scope"])
        self.assertEqual(
            [exact["id"], workflow_global["id"], global_global["id"]],
            resolved["memory_ids"],
        )
        self.assertIn("Reply &lt;tone&gt;", resolved["prompt"])
        self.assertIn(
            "Use &lt;system&gt;brief &amp; warm&lt;/system&gt;.",
            resolved["prompt"],
        )
        self.assertNotIn("<system>", resolved["prompt"])
        self.assertNotIn("pat@example.com", resolved["prompt"])

    def test_unknown_scope_retrieves_only_global_memories(self):
        from app.agent.memory_context import resolve_memory_context

        global_memory = self._remember(
            key="global-global",
            workflow_scope="global",
            contact_scope=None,
        )
        self._remember(
            key="drafting-global",
            workflow_scope="drafting",
            contact_scope=None,
        )
        self._remember(
            key="global-contact",
            workflow_scope="global",
            contact_scope="pat@example.com",
        )

        resolved = resolve_memory_context(
            [HumanMessage(content="What should I focus on today?")],
            context_email_ids=[],
            profile_id="profile-a",
            database=self.database,
        )

        self.assertIsNone(resolved["workflow_scope"])
        self.assertFalse(resolved["has_contact_scope"])
        self.assertEqual([global_memory["id"]], resolved["memory_ids"])

    def test_current_turn_tool_result_drives_workflow_and_contact(self):
        from app.agent.memory_context import resolve_memory_context

        exact = self._remember(
            key="tool-routed",
            workflow_scope="drafting",
            contact_scope="pat@example.com",
        )
        messages = [
            HumanMessage(content="Help with my inbox"),
            AIMessage(
                content="",
                tool_calls=[
                    {
                        "name": "find_email",
                        "args": {"sender_contains": "Pat"},
                        "id": "find-call",
                        "type": "tool_call",
                    }
                ],
            ),
            ToolMessage(
                "[{'id': 'email-1', 'sender_email': 'pat@example.com'}]",
                tool_call_id="find-call",
            ),
        ]

        resolved = resolve_memory_context(
            messages,
            context_email_ids=[],
            profile_id="profile-a",
            database=self.database,
        )

        self.assertEqual("drafting", resolved["workflow_scope"])
        self.assertTrue(resolved["has_contact_scope"])
        self.assertEqual([exact["id"]], resolved["memory_ids"])

    def test_calendar_substep_preserves_explicit_drafting_workflow(self):
        from app.agent.memory_context import resolve_memory_context

        drafting = self._remember(
            key="drafting-tone",
            workflow_scope="drafting",
            contact_scope=None,
        )
        self._remember(
            key="calendar-rule",
            workflow_scope="scheduling",
            contact_scope=None,
        )
        messages = [
            HumanMessage(content="Draft a meeting reply with available times"),
            AIMessage(
                content="",
                tool_calls=[
                    {
                        "name": "read_calendar",
                        "args": {"days_ahead": 7},
                        "id": "calendar-call",
                        "type": "tool_call",
                    }
                ],
            ),
            ToolMessage(
                "{'free_slots': ['Tue 10:30']}",
                tool_call_id="calendar-call",
            ),
        ]

        resolved = resolve_memory_context(
            messages,
            context_email_ids=[],
            profile_id="profile-a",
            database=self.database,
        )

        self.assertEqual("drafting", resolved["workflow_scope"])
        self.assertEqual([drafting["id"]], resolved["memory_ids"])

    def test_context_enforces_count_value_and_total_character_limits(self):
        from app.agent.memory_context import (
            MAX_INJECTED_MEMORIES,
            MAX_MEMORY_CONTEXT_CHARS,
            MAX_MEMORY_VALUE_CHARS,
            resolve_memory_context,
        )

        for index in range(7):
            self.store.remember(
                profile_id="profile-a",
                memory_type="preference",
                workflow_scope="global",
                contact_scope=None,
                key=f"large-{index}",
                value=f"marker-{index}-" + ("x" * 700),
                source="explicit_user",
            )

        resolved = resolve_memory_context(
            [HumanMessage(content="Hello")],
            context_email_ids=[],
            profile_id="profile-a",
            database=self.database,
        )

        self.assertLessEqual(len(resolved["memory_ids"]), MAX_INJECTED_MEMORIES)
        self.assertLessEqual(resolved["context_chars"], MAX_MEMORY_CONTEXT_CHARS)
        self.assertLessEqual(
            max(len(memory["value"]) for memory in resolved["memories"]),
            MAX_MEMORY_VALUE_CHARS,
        )
        self.assertEqual(
            (resolved["context_chars"] + 3) // 4,
            resolved["estimated_tokens"],
        )

    def test_confirmed_memory_crosses_threads_without_entering_checkpoint_state(self):
        from app.agent.graph import build_agent

        memory = self.store.remember(
            profile_id=LOCAL_PROFILE_ID,
            memory_type="preference",
            workflow_scope="drafting",
            contact_scope=None,
            key="Reply tone",
            value="Use the private concise preference.",
            source="explicit_user",
        )
        model = _CapturingLLM()
        agent = build_agent(llm=model)
        db_path = str(Path(self.temp_dir.name) / "semantic-memory-agent.db")

        async def exercise():
            states = []
            for thread_id in ("memory-thread-1", "memory-thread-2"):
                config = {"configurable": {"thread_id": thread_id}}
                await agent.ainvoke(new_turn_input("Draft a reply"), config)
                states.append(await agent.aget_state(config))
            return states

        with patch.dict(os.environ, {"FEEDFLUX_DB_PATH": db_path}):
            states = asyncio.run(exercise())

        self.assertEqual(2, len(model.calls))
        for call in model.calls:
            self.assertIn("Use the private concise preference.", call[0].content)
            self.assertIn(f'<memory id="{memory["id"]}"', call[0].content)
        for state in states:
            self.assertNotIn("Use the private concise preference.", repr(state.values))
            self.assertNotIn("semantic_memory_context", repr(state.values))

    def test_memory_context_trace_is_sanitized_and_persisted(self):
        from app.agent.graph import build_agent

        self.database.insert_email(
            {
                "id": "focused-email",
                "subject": "Design review",
                "sender_name": "Pat",
                "sender_email": "pat@example.com",
                "received_datetime": 1,
                "body_content": "Fixture email body.",
            }
        )
        memory = self.store.remember(
            profile_id=LOCAL_PROFILE_ID,
            memory_type="preference",
            workflow_scope="drafting",
            contact_scope="pat@example.com",
            key="Private reply tone",
            value="Secret preference value",
            source="explicit_user",
        )
        db_path = str(Path(self.temp_dir.name) / "semantic-memory-agent.db")
        agent = build_agent(llm=_CapturingLLM())
        run_store = AgentRunStore(self.database)
        runtime = AgentRunRuntime(
            run_store,
            provider="fixture-provider",
            stream=lambda graph_input, thread_id: stream_agent(
                graph_input,
                thread_id,
                agent=agent,
            ),
        )

        with patch.dict(os.environ, {"FEEDFLUX_DB_PATH": db_path}):
            events = asyncio.run(
                _collect(
                    runtime.stream_new_run(
                        new_turn_input("Draft a reply", ["focused-email"]),
                        "memory-trace-thread",
                    )
                )
            )

        trace = next(
            event
            for event in events
            if event.get("type") == "trace"
            and event.get("step") == "memory_context_loaded"
        )
        run_id = next(event["run_id"] for event in events if event["type"] == "run")
        persisted = [
            event
            for event in run_store.list_events(run_id)
            if event["event_type"] == "memory_context_loaded"
        ]

        self.assertEqual([memory["id"]], trace["memory_ids"])
        self.assertEqual(1, trace["memory_count"])
        self.assertEqual("drafting", trace["workflow_scope"])
        self.assertTrue(trace["has_contact_scope"])
        self.assertGreater(trace["context_chars"], 0)
        self.assertGreater(trace["estimated_tokens"], 0)
        self.assertEqual(1, len(persisted))
        serialized = json.dumps({"trace": trace, "persisted": persisted})
        self.assertNotIn("Secret preference value", serialized)
        self.assertNotIn("Private reply tone", serialized)
        self.assertNotIn("pat@example.com", serialized)


if __name__ == "__main__":
    unittest.main()
