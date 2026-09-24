import asyncio
import json
import tempfile
import unittest
from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from langchain_core.messages import AIMessage, ToolMessage

from app.agent.execution_context import current_run_id, current_tool_call_id
from app.agent.run_runtime import AgentRunRuntime
from app.agent.stream import new_turn_input, resume_input, stream_agent
from app.core.profile import LOCAL_PROFILE_ID
from app.models.semantic_memory import SemanticMemory
from app.models.tool_execution import ToolExecution
from app.services.database import DatabaseService


async def _collect(stream):
    return [event async for event in stream]


class _InferenceCallingMemoryModel:
    def bind_tools(self, tools):
        return self

    async def ainvoke(self, messages):
        if isinstance(messages[-1], ToolMessage):
            return AIMessage(content="Memory updated after approval.")
        return AIMessage(
            content="",
            tool_calls=[
                {
                    "name": "remember_memory",
                    "args": {
                        "memory_type": "preference",
                        "workflow_scope": "drafting",
                        "key": "Reply tone",
                        "value": "Use the style from that one edit.",
                    },
                    "id": "inferred-memory-call",
                    "type": "tool_call",
                }
            ],
        )


class _MemoryTraceAgent:
    async def astream_events(self, graph_input, config, version):
        yield {
            "event": "on_tool_start",
            "name": "remember_memory",
            "metadata": {"tool_call_id": "trace-memory-call"},
            "data": {
                "input": {
                    "memory_type": "preference",
                    "workflow_scope": "drafting",
                    "contact_scope": "private-contact@example.com",
                    "key": "Private reply tone",
                    "value": "Secret preference value",
                }
            },
        }
        yield {
            "event": "on_tool_end",
            "name": "remember_memory",
            "metadata": {"tool_call_id": "trace-memory-call"},
            "data": {
                "output": {
                    "memory": {
                        "id": 7,
                        "memory_type": "preference",
                        "workflow_scope": "drafting",
                        "contact_scope": "private-contact@example.com",
                        "key": "Private reply tone",
                        "value": "Secret preference value",
                        "status": "active",
                        "version": 1,
                    }
                }
            },
        }

    async def aget_state(self, config):
        return SimpleNamespace(tasks=[])


@contextmanager
def _tool_context(run_id: str, tool_call_id: str):
    run_token = current_run_id.set(run_id)
    call_token = current_tool_call_id.set(tool_call_id)
    try:
        yield
    finally:
        current_tool_call_id.reset(call_token)
        current_run_id.reset(run_token)


class AgentMemoryToolsTest(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.data_dir = Path(self.temp_dir.name)
        self.data_dir_patch = patch("app.services.database.DATA_DIR", self.data_dir)
        self.data_dir_patch.start()

    def tearDown(self):
        self.data_dir_patch.stop()
        self.temp_dir.cleanup()

    def test_remember_tool_is_server_scoped_and_idempotent(self):
        try:
            from app.agent.memory_tools import remember_memory
        except ModuleNotFoundError:
            self.fail("agent memory tools are not implemented")

        self.assertNotIn("profile_id", remember_memory.args_schema.model_fields)
        self.assertNotIn("source", remember_memory.args_schema.model_fields)
        args = {
            "memory_type": "preference",
            "workflow_scope": "drafting",
            "contact_scope": "pat@example.com",
            "key": "Reply tone",
            "value": "Be concise.",
        }
        with _tool_context("run-memory-1", "call-memory-1"):
            first = remember_memory.invoke(args)
            replay = remember_memory.invoke(args)

        database = DatabaseService(str(self.data_dir / "emails.db"))
        session = database.Session()
        try:
            memories = session.query(SemanticMemory).all()
            executions = session.query(ToolExecution).all()
        finally:
            session.close()
            database.engine.dispose()

        self.assertEqual(first, replay)
        self.assertEqual("active", first["memory"]["status"])
        self.assertEqual(LOCAL_PROFILE_ID, first["memory"]["profile_id"])
        self.assertEqual(1, len(memories))
        self.assertEqual(1, len(executions))
        self.assertEqual("remember_memory", executions[0].operation)
        self.assertEqual("run-memory-1", executions[0].run_id)
        self.assertEqual("call-memory-1", executions[0].tool_call_id)

    def test_list_and_update_tools_preserve_profile_and_replay_safely(self):
        from app.agent.memory_tools import (
            list_memories,
            remember_memory,
            update_memory,
        )

        with _tool_context("run-memory-2", "call-remember"):
            created = remember_memory.invoke(
                {
                    "memory_type": "preference",
                    "workflow_scope": "drafting",
                    "key": "Reply tone",
                    "value": "Be concise.",
                }
            )
        update_args = {
            "memory_id": created["memory"]["id"],
            "value": "Be warm and concise.",
        }
        with _tool_context("run-memory-2", "call-update"):
            first = update_memory.invoke(update_args)
            replay = update_memory.invoke(update_args)

        listed = list_memories.invoke(
            {"memory_type": "preference", "workflow_scope": "drafting"}
        )

        self.assertEqual(first, replay)
        self.assertEqual(2, first["memory"]["version"])
        self.assertEqual(1, listed["count"])
        self.assertEqual("Be warm and concise.", listed["memories"][0]["value"])
        for tool in (list_memories, update_memory):
            self.assertNotIn("profile_id", tool.args_schema.model_fields)
            self.assertNotIn("source", tool.args_schema.model_fields)

        database = DatabaseService(str(self.data_dir / "emails.db"))
        session = database.Session()
        try:
            memories = session.query(SemanticMemory).all()
            executions = session.query(ToolExecution).all()
        finally:
            session.close()
            database.engine.dispose()
        self.assertEqual(2, len(memories))
        self.assertEqual(
            "Be warm and concise.",
            next(memory for memory in memories if memory.status == "active").value,
        )
        self.assertEqual(2, len(executions))

    def test_forget_and_reset_tools_are_idempotent_and_scoped(self):
        from app.agent.memory_tools import (
            forget_memory,
            list_memories,
            remember_memory,
            reset_memories,
        )

        with _tool_context("run-memory-3", "call-pref"):
            preference = remember_memory.invoke(
                {
                    "memory_type": "preference",
                    "workflow_scope": "drafting",
                    "key": "Reply tone",
                    "value": "Be concise.",
                }
            )
        with _tool_context("run-memory-3", "call-fact"):
            remember_memory.invoke(
                {
                    "memory_type": "fact",
                    "workflow_scope": "drafting",
                    "key": "Manager",
                    "value": "Pat is my manager.",
                }
            )

        with _tool_context("run-memory-3", "call-forget"):
            first_forget = forget_memory.invoke(
                {"memory_id": preference["memory"]["id"]}
            )
            replayed_forget = forget_memory.invoke(
                {"memory_id": preference["memory"]["id"]}
            )
        self.assertEqual(first_forget, replayed_forget)
        self.assertEqual(1, first_forget["forgotten_count"])

        with _tool_context("run-memory-3", "call-reset"):
            first_reset = reset_memories.invoke({"workflow_scope": "drafting"})
            replayed_reset = reset_memories.invoke({"workflow_scope": "drafting"})
        self.assertEqual(first_reset, replayed_reset)
        self.assertEqual(1, first_reset["lineage_count"])
        self.assertEqual(1, first_reset["forgotten_count"])
        self.assertEqual(0, list_memories.invoke({})["count"])

        database = DatabaseService(str(self.data_dir / "emails.db"))
        session = database.Session()
        try:
            execution_results = [
                execution.result for execution in session.query(ToolExecution).all()
            ]
        finally:
            session.close()
            database.engine.dispose()
        serialized_results = json.dumps(execution_results)
        self.assertNotIn("Be concise.", serialized_results)
        self.assertNotIn("Pat is my manager.", serialized_results)
        self.assertNotIn("Reply tone", serialized_results)
        self.assertNotIn("Manager", serialized_results)

        for tool in (forget_memory, reset_memories):
            self.assertNotIn("profile_id", tool.args_schema.model_fields)
            self.assertNotIn("source", tool.args_schema.model_fields)

    def test_memory_writes_interrupt_before_execution_and_resume_after_approval(self):
        from app.agent.graph import build_agent
        from app.agent.tools import HIGH_RISK_TOOLS, TOOLS_BY_NAME
        from app.services.agent_run_store import AgentRunStore

        write_tools = {
            "remember_memory",
            "update_memory",
            "forget_memory",
            "reset_memories",
        }
        self.assertTrue(write_tools.issubset(HIGH_RISK_TOOLS))
        self.assertNotIn("list_memories", HIGH_RISK_TOOLS)
        self.assertTrue({*write_tools, "list_memories"}.issubset(TOOLS_BY_NAME))

        database = DatabaseService(str(self.data_dir / "emails.db"))
        agent = build_agent(llm=_InferenceCallingMemoryModel())
        runtime = AgentRunRuntime(
            AgentRunStore(database),
            provider="fixture-provider",
            stream=lambda graph_input, thread_id: stream_agent(
                graph_input,
                thread_id,
                agent=agent,
            ),
        )

        interrupted_events = asyncio.run(
            _collect(
                runtime.stream_new_run(
                    new_turn_input("I edited this one draft to be shorter."),
                    "memory-approval-thread",
                )
            )
        )
        interrupt = next(
            event for event in interrupted_events if event["type"] == "interrupt"
        )
        session = database.Session()
        try:
            before_approval = session.query(SemanticMemory).count()
        finally:
            session.close()

        self.assertEqual("remember_memory", interrupt["tool"])
        self.assertEqual(
            "Use the style from that one edit.",
            interrupt["args"]["value"],
        )
        self.assertEqual(0, before_approval)

        resumed_events = asyncio.run(
            _collect(
                runtime.stream_resumed_run(
                    resume_input(approve=True),
                    "memory-approval-thread",
                )
            )
        )
        session = database.Session()
        try:
            memories = session.query(SemanticMemory).all()
        finally:
            session.close()
            database.engine.dispose()

        self.assertFalse(
            any(event["type"] == "interrupt" for event in resumed_events)
        )
        self.assertEqual(1, len(memories))
        self.assertEqual("explicit_user", memories[0].source)
        self.assertEqual(LOCAL_PROFILE_ID, memories[0].profile_id)

    def test_memory_tool_trace_exposes_metadata_without_sensitive_fields(self):
        events = asyncio.run(
            _collect(
                stream_agent(
                    {},
                    "memory-trace-thread",
                    agent=_MemoryTraceAgent(),
                )
            )
        )
        serialized = json.dumps(events)
        self.assertNotIn("Secret preference value", serialized)
        self.assertNotIn("Private reply tone", serialized)
        self.assertNotIn("private-contact@example.com", serialized)

        trace = [event for event in events if event.get("type") == "trace"]
        self.assertEqual("remember_memory", trace[0]["tool"])
        self.assertEqual(
            {
                "memory_type": "preference",
                "workflow_scope": "drafting",
                "has_contact_scope": True,
                "key_present": True,
                "value_chars": 23,
            },
            trace[0]["args"],
        )
        self.assertEqual(7, trace[1]["outcome"]["memory_id"])
        self.assertEqual("active", trace[1]["outcome"]["status"])


if __name__ == "__main__":
    unittest.main()
