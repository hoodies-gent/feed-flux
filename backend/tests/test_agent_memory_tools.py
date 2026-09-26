import ast
import asyncio
import json
import tempfile
import unittest
from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from langchain_core.messages import AIMessage, HumanMessage, ToolMessage

from app.agent.execution_context import current_run_id, current_tool_call_id
from app.agent.run_runtime import AgentRunRuntime
from app.agent.stream import new_turn_input, resume_input, stream_agent
from app.core.profile import LOCAL_PROFILE_ID
from app.models.semantic_memory import SemanticMemory
from app.models.tool_execution import ToolExecution
from app.services.database import DatabaseService


async def _collect(stream):
    return [event async for event in stream]


def _target_config(data_dir: Path, memory_id: int) -> dict:
    from app.services.memory_precondition import memory_target_fingerprint
    from app.services.semantic_memory_store import SemanticMemoryStore

    database = DatabaseService(str(data_dir / "emails.db"))
    try:
        target = SemanticMemoryStore(database).get_memory(
            profile_id=LOCAL_PROFILE_ID,
            memory_id=memory_id,
        )
        return {
            "metadata": {
                "memory_target_fingerprint": memory_target_fingerprint(target),
            }
        }
    finally:
        database.engine.dispose()


class _InferenceCallingMemoryModel:
    def __init__(self):
        self.tool_output = None

    def bind_tools(self, tools):
        return self

    async def ainvoke(self, messages):
        if isinstance(messages[-1], ToolMessage):
            self.tool_output = messages[-1].content
            return AIMessage(content="Memory request handled.")
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


class _BatchMemoryCallingModel:
    def bind_tools(self, tools):
        return self

    async def ainvoke(self, messages):
        if isinstance(messages[-1], ToolMessage):
            return AIMessage(content="Memory requests handled.")
        return AIMessage(
            content="",
            tool_calls=[
                {
                    "name": "remember_memory",
                    "args": {
                        "memory_type": "preference",
                        "workflow_scope": "drafting",
                        "key": "Reply tone",
                        "value": "Be concise.",
                    },
                    "id": "batch-memory-call-1",
                    "type": "tool_call",
                },
                {
                    "name": "remember_memory",
                    "args": {
                        "memory_type": "constraint",
                        "workflow_scope": "scheduling",
                        "key": "Meeting hours",
                        "value": "Use afternoons.",
                    },
                    "id": "batch-memory-call-2",
                    "type": "tool_call",
                },
            ],
        )


class _FakeConsentReviewer:
    def __init__(self, decision, reason_code):
        self.result = {"decision": decision, "reason_code": reason_code}
        self.messages = []

    def with_structured_output(self, schema):
        return self

    async def ainvoke(self, messages):
        self.messages.append(messages)
        return self.result


class _HangingConsentReviewer:
    def with_structured_output(self, schema):
        return self

    async def ainvoke(self, messages):
        await asyncio.sleep(60)


class _BarrierConsentReviewer:
    def __init__(self, required_calls):
        self.required_calls = required_calls
        self.started_calls = 0
        self.ready = asyncio.Event()

    def with_structured_output(self, schema):
        return self

    async def ainvoke(self, messages):
        self.started_calls += 1
        if self.started_calls == self.required_calls:
            self.ready.set()
        await self.ready.wait()
        return {
            "decision": "allow",
            "reason_code": "explicit_user_request",
        }


class _ChangingConsentReviewer:
    def __init__(self, *results):
        self.results = list(results)
        self.messages = []

    def with_structured_output(self, schema):
        return self

    async def ainvoke(self, messages):
        self.messages.append(messages)
        return self.results.pop(0)


class _TargetAwareConsentReviewer:
    def __init__(self, expected_key):
        self.expected_key = expected_key
        self.payloads = []

    def with_structured_output(self, schema):
        return self

    async def ainvoke(self, messages):
        payload = json.loads(messages[-1].content)
        self.payloads.append(payload)
        target = payload.get("current_target") or {}
        if target.get("key") == self.expected_key:
            return {
                "decision": "allow",
                "reason_code": "explicit_user_request",
            }
        return {
            "decision": "deny",
            "reason_code": "argument_mismatch",
        }


class _MemoryMutationCallingModel:
    def __init__(self, tool_name, args):
        self.tool_name = tool_name
        self.args = args
        self.tool_output = None

    def bind_tools(self, tools):
        return self

    async def ainvoke(self, messages):
        if isinstance(messages[-1], ToolMessage):
            self.tool_output = messages[-1].content
            return AIMessage(content="Memory request handled.")
        return AIMessage(
            content="",
            tool_calls=[
                {
                    "name": self.tool_name,
                    "args": self.args,
                    "id": f"{self.tool_name}-call",
                    "type": "tool_call",
                }
            ],
        )


class _ListCallingMemoryModel:
    def __init__(self):
        self.tool_output = None

    def bind_tools(self, tools):
        return self

    async def ainvoke(self, messages):
        if isinstance(messages[-1], ToolMessage):
            self.tool_output = ast.literal_eval(messages[-1].content)
            return AIMessage(content="Memory list ready.")
        return AIMessage(
            content="",
            tool_calls=[
                {
                    "name": "list_memories",
                    "args": {"workflow_scope": "drafting"},
                    "id": "list-memory-call",
                    "type": "tool_call",
                }
            ],
        )


class _PagingListCallingMemoryModel:
    def __init__(self):
        self.next_cursor = None
        self.pages = []
        self.denials = []
        self.request_count = 0

    def bind_tools(self, tools):
        return self

    def _list_call(self, cursor=None):
        self.request_count += 1
        args = {"workflow_scope": "drafting"}
        if cursor is not None:
            args["cursor"] = cursor
        return AIMessage(
            content="",
            tool_calls=[
                {
                    "name": "list_memories",
                    "args": args,
                    "id": f"list-page-call-{self.request_count}",
                    "type": "tool_call",
                }
            ],
        )

    async def ainvoke(self, messages):
        if isinstance(messages[-1], HumanMessage):
            return self._list_call(self.next_cursor)
        if isinstance(messages[-1], ToolMessage):
            try:
                page = ast.literal_eval(messages[-1].content)
            except (SyntaxError, ValueError):
                self.denials.append(messages[-1].content)
                return AIMessage(content="Another page requires a new user request.")
            self.pages.append(page)
            self.next_cursor = page["next_cursor"]
            if len(self.pages) == 1:
                return self._list_call(self.next_cursor)
            return AIMessage(content="Memory page ready.")
        return AIMessage(content="Memory page ready.")


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
        update_config = _target_config(self.data_dir, created["memory"]["id"])
        with _tool_context("run-memory-2", "call-update"):
            first = update_memory.invoke(update_args, config=update_config)
            replay = update_memory.invoke(update_args, config=update_config)

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

        forget_config = _target_config(
            self.data_dir,
            preference["memory"]["id"],
        )
        with _tool_context("run-memory-3", "call-forget"):
            first_forget = forget_memory.invoke(
                {"memory_id": preference["memory"]["id"]},
                config=forget_config,
            )
            replayed_forget = forget_memory.invoke(
                {"memory_id": preference["memory"]["id"]},
                config=forget_config,
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

    def test_list_tool_has_a_hard_page_limit_and_cursor(self):
        from app.agent.memory_tools import list_memories
        from app.services.semantic_memory_store import SemanticMemoryStore

        database = DatabaseService(str(self.data_dir / "emails.db"))
        store = SemanticMemoryStore(database)
        for index in range(12):
            store.remember(
                profile_id=LOCAL_PROFILE_ID,
                memory_type="preference",
                workflow_scope="drafting",
                contact_scope=None,
                key=f"Preference {index}",
                value=f"Value {index}",
                source="explicit_user",
            )

        first = list_memories.invoke(
            {"memory_type": "preference", "workflow_scope": "drafting"}
        )
        second = list_memories.invoke(
            {
                "memory_type": "preference",
                "workflow_scope": "drafting",
                "cursor": first["next_cursor"],
            }
        )
        database.engine.dispose()

        self.assertEqual(10, first["count"])
        self.assertIsNotNone(first["next_cursor"])
        self.assertEqual(2, second["count"])
        self.assertIsNone(second["next_cursor"])
        self.assertEqual(
            {"memory_type", "workflow_scope", "contact_scope", "limit", "cursor"},
            set(list_memories.args_schema.model_fields),
        )

    def test_explicit_memory_write_executes_without_interrupt(self):
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
        reviewer = _FakeConsentReviewer("allow", "explicit_user_request")
        agent = build_agent(
            llm=_InferenceCallingMemoryModel(),
            memory_consent_reviewer=reviewer,
        )
        runtime = AgentRunRuntime(
            AgentRunStore(database),
            provider="fixture-provider",
            stream=lambda graph_input, thread_id: stream_agent(
                graph_input,
                thread_id,
                agent=agent,
            ),
        )

        events = asyncio.run(
            _collect(
                runtime.stream_new_run(
                    new_turn_input("Remember that I prefer concise replies."),
                    "memory-explicit-thread",
                )
            )
        )
        session = database.Session()
        try:
            memories = session.query(SemanticMemory).all()
        finally:
            session.close()
            database.engine.dispose()

        self.assertFalse(any(event["type"] == "interrupt" for event in events))
        self.assertEqual(1, len(memories))
        self.assertEqual("explicit_user", memories[0].source)
        self.assertEqual(LOCAL_PROFILE_ID, memories[0].profile_id)
        self.assertEqual(1, len(reviewer.messages))

    def test_inferred_memory_write_is_denied_without_interrupt_or_storage(self):
        from app.agent.graph import build_agent
        from app.services.agent_run_store import AgentRunStore

        database = DatabaseService(str(self.data_dir / "emails.db"))
        model = _InferenceCallingMemoryModel()
        agent = build_agent(
            llm=model,
            memory_consent_reviewer=_FakeConsentReviewer(
                "deny",
                "inferred_behavior",
            ),
        )
        runtime = AgentRunRuntime(
            AgentRunStore(database),
            provider="fixture-provider",
            stream=lambda graph_input, thread_id: stream_agent(
                graph_input,
                thread_id,
                agent=agent,
            ),
        )

        events = asyncio.run(
            _collect(
                runtime.stream_new_run(
                    new_turn_input("I edited this one draft to be shorter."),
                    "memory-denied-thread",
                )
            )
        )
        session = database.Session()
        try:
            memory_count = session.query(SemanticMemory).count()
        finally:
            session.close()
            database.engine.dispose()

        self.assertFalse(any(event["type"] == "interrupt" for event in events))
        self.assertEqual(0, memory_count)
        self.assertEqual(
            "[memory consent denied] No confirmed memory was changed.",
            model.tool_output,
        )
        self.assertNotIn("Use the style from that one edit.", json.dumps(events))

    def test_reviewer_timeout_interrupts_without_storing_memory(self):
        from app.agent.graph import build_agent
        from app.services.agent_run_store import AgentRunStore

        database = DatabaseService(str(self.data_dir / "emails.db"))
        agent = build_agent(
            llm=_InferenceCallingMemoryModel(),
            memory_consent_reviewer=_HangingConsentReviewer(),
            memory_consent_timeout_seconds=0.01,
        )
        runtime = AgentRunRuntime(
            AgentRunStore(database),
            provider="fixture-provider",
            stream=lambda graph_input, thread_id: stream_agent(
                graph_input,
                thread_id,
                agent=agent,
            ),
        )

        events = asyncio.run(
            _collect(
                runtime.stream_new_run(
                    new_turn_input("Remember that I prefer concise replies."),
                    "memory-reviewer-timeout-thread",
                )
            )
        )
        session = database.Session()
        try:
            memory_count = session.query(SemanticMemory).count()
        finally:
            session.close()
            database.engine.dispose()

        interrupt = next(event for event in events if event["type"] == "interrupt")
        self.assertEqual("remember_memory", interrupt["tool"])
        self.assertEqual(0, memory_count)

    def test_memory_consent_reviews_batch_concurrently(self):
        from app.agent.graph import build_agent
        from app.services.agent_run_store import AgentRunStore

        database = DatabaseService(str(self.data_dir / "emails.db"))
        agent = build_agent(
            llm=_BatchMemoryCallingModel(),
            memory_consent_reviewer=_BarrierConsentReviewer(required_calls=2),
            memory_consent_timeout_seconds=0.05,
        )
        runtime = AgentRunRuntime(
            AgentRunStore(database),
            provider="fixture-provider",
            stream=lambda graph_input, thread_id: stream_agent(
                graph_input,
                thread_id,
                agent=agent,
            ),
        )

        events = asyncio.run(
            _collect(
                runtime.stream_new_run(
                    new_turn_input(
                        "Remember that replies should be concise and meetings should "
                        "be scheduled in the afternoon."
                    ),
                    "memory-reviewer-batch-thread",
                )
            )
        )
        session = database.Session()
        try:
            memory_count = session.query(SemanticMemory).count()
        finally:
            session.close()
            database.engine.dispose()

        self.assertFalse(any(event["type"] == "interrupt" for event in events))
        self.assertEqual(2, memory_count)

    def test_ambiguous_memory_write_interrupts_then_executes_after_approval(self):
        from app.agent.graph import build_agent
        from app.services.agent_run_store import AgentRunStore

        database = DatabaseService(str(self.data_dir / "emails.db"))
        agent = build_agent(
            llm=_InferenceCallingMemoryModel(),
            memory_consent_reviewer=_FakeConsentReviewer(
                "ask",
                "ambiguous_user_intent",
            ),
        )
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
                    new_turn_input("Maybe keep this style in mind."),
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

    def test_memory_consent_decision_is_stable_when_declined_after_resume(self):
        from app.agent.graph import build_agent
        from app.services.agent_run_store import AgentRunStore

        database = DatabaseService(str(self.data_dir / "emails.db"))
        reviewer = _ChangingConsentReviewer(
            {
                "decision": "ask",
                "reason_code": "ambiguous_user_intent",
            },
            {
                "decision": "allow",
                "reason_code": "explicit_user_request",
            },
        )
        model = _InferenceCallingMemoryModel()
        agent = build_agent(llm=model, memory_consent_reviewer=reviewer)
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
                    new_turn_input("Maybe keep this style in mind."),
                    "memory-decline-replay-thread",
                )
            )
        )
        self.assertTrue(
            any(event["type"] == "interrupt" for event in interrupted_events)
        )

        resumed_events = asyncio.run(
            _collect(
                runtime.stream_resumed_run(
                    resume_input(approve=False, note="Do not remember this."),
                    "memory-decline-replay-thread",
                )
            )
        )
        session = database.Session()
        try:
            memory_count = session.query(SemanticMemory).count()
        finally:
            session.close()
            database.engine.dispose()

        self.assertFalse(
            any(event["type"] == "interrupt" for event in resumed_events)
        )
        self.assertEqual(0, memory_count)
        self.assertEqual(1, len(reviewer.messages))
        self.assertIn("[rejected by user]", model.tool_output)

    def test_update_consent_rejects_a_different_model_selected_target(self):
        from app.agent.graph import build_agent
        from app.services.agent_run_store import AgentRunStore
        from app.services.semantic_memory_store import SemanticMemoryStore

        database = DatabaseService(str(self.data_dir / "emails.db"))
        store = SemanticMemoryStore(database)
        intended = store.remember(
            profile_id=LOCAL_PROFILE_ID,
            memory_type="preference",
            workflow_scope="drafting",
            contact_scope=None,
            key="Reply tone",
            value="Be concise.",
            source="explicit_user",
        )
        other = store.remember(
            profile_id=LOCAL_PROFILE_ID,
            memory_type="preference",
            workflow_scope="drafting",
            contact_scope=None,
            key="Signature style",
            value="Use initials.",
            source="explicit_user",
        )
        reviewer = _TargetAwareConsentReviewer(expected_key=intended["key"])
        model = _MemoryMutationCallingModel(
            "update_memory",
            {"memory_id": other["id"], "value": "Be warm."},
        )
        agent = build_agent(llm=model, memory_consent_reviewer=reviewer)
        runtime = AgentRunRuntime(
            AgentRunStore(database),
            provider="fixture-provider",
            stream=lambda graph_input, thread_id: stream_agent(
                graph_input,
                thread_id,
                agent=agent,
            ),
        )

        events = asyncio.run(
            _collect(
                runtime.stream_new_run(
                    new_turn_input("Update my Reply tone memory to be warm."),
                    "memory-target-mismatch-thread",
                )
            )
        )
        memories = store.list_memories(profile_id=LOCAL_PROFILE_ID)
        database.engine.dispose()

        self.assertFalse(any(event["type"] == "interrupt" for event in events))
        self.assertEqual("Signature style", reviewer.payloads[0]["current_target"]["key"])
        self.assertEqual(
            "[memory consent denied] No confirmed memory was changed.",
            model.tool_output,
        )
        self.assertEqual(
            {"Reply tone": "Be concise.", "Signature style": "Use initials."},
            {memory["key"]: memory["value"] for memory in memories},
        )

    def test_forget_approval_fails_if_target_changes_while_interrupted(self):
        from app.agent.graph import ToolExecutionFailure, build_agent
        from app.services.agent_run_store import AgentRunStore
        from app.services.semantic_memory_store import SemanticMemoryStore

        database = DatabaseService(str(self.data_dir / "emails.db"))
        store = SemanticMemoryStore(database)
        target = store.remember(
            profile_id=LOCAL_PROFILE_ID,
            memory_type="preference",
            workflow_scope="drafting",
            contact_scope=None,
            key="Reply tone",
            value="Be concise.",
            source="explicit_user",
        )
        agent = build_agent(
            llm=_MemoryMutationCallingModel(
                "forget_memory",
                {"memory_id": target["id"]},
            ),
            memory_consent_reviewer=_FakeConsentReviewer(
                "ask",
                "ambiguous_user_intent",
            ),
        )
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
                    new_turn_input("Maybe forget my Reply tone memory."),
                    "memory-target-changed-thread",
                )
            )
        )
        self.assertTrue(
            any(event["type"] == "interrupt" for event in interrupted_events)
        )
        store.disable(profile_id=LOCAL_PROFILE_ID, memory_id=target["id"])

        with self.assertRaises(ToolExecutionFailure):
            asyncio.run(
                _collect(
                    runtime.stream_resumed_run(
                        resume_input(approve=True),
                        "memory-target-changed-thread",
                    )
                )
            )
        current = store.list_memories(profile_id=LOCAL_PROFILE_ID)
        database.engine.dispose()

        self.assertEqual(1, len(current))
        self.assertEqual("disabled", current[0]["status"])
        self.assertEqual("Reply tone", current[0]["key"])

    def test_forget_consent_rejects_a_superseded_target_id(self):
        from app.agent.graph import build_agent
        from app.services.agent_run_store import AgentRunStore
        from app.services.semantic_memory_store import SemanticMemoryStore

        database = DatabaseService(str(self.data_dir / "emails.db"))
        store = SemanticMemoryStore(database)
        original = store.remember(
            profile_id=LOCAL_PROFILE_ID,
            memory_type="preference",
            workflow_scope="drafting",
            contact_scope=None,
            key="Reply tone",
            value="Be concise.",
            source="explicit_user",
        )
        successor = store.update(
            profile_id=LOCAL_PROFILE_ID,
            memory_id=original["id"],
            value="Be warm.",
            source="management_ui",
        )
        model = _MemoryMutationCallingModel(
            "forget_memory",
            {"memory_id": original["id"]},
        )
        reviewer = _FakeConsentReviewer("allow", "explicit_user_request")
        agent = build_agent(llm=model, memory_consent_reviewer=reviewer)
        runtime = AgentRunRuntime(
            AgentRunStore(database),
            provider="fixture-provider",
            stream=lambda graph_input, thread_id: stream_agent(
                graph_input,
                thread_id,
                agent=agent,
            ),
        )

        events = asyncio.run(
            _collect(
                runtime.stream_new_run(
                    new_turn_input("Forget my current Reply tone memory."),
                    "memory-superseded-target-thread",
                )
            )
        )
        current = store.list_memories(profile_id=LOCAL_PROFILE_ID)
        database.engine.dispose()

        self.assertFalse(any(event["type"] == "interrupt" for event in events))
        self.assertEqual(0, len(reviewer.messages))
        self.assertEqual(
            "[memory consent denied] No confirmed memory was changed.",
            model.tool_output,
        )
        self.assertEqual(1, len(current))
        self.assertEqual(successor["id"], current[0]["id"])
        self.assertEqual("Be warm.", current[0]["value"])

    def test_memory_list_executes_without_interrupt_and_stays_bounded(self):
        from app.agent.graph import build_agent
        from app.services.agent_run_store import AgentRunStore
        from app.services.semantic_memory_store import SemanticMemoryStore

        database = DatabaseService(str(self.data_dir / "emails.db"))
        store = SemanticMemoryStore(database)
        for index in range(12):
            store.remember(
                profile_id=LOCAL_PROFILE_ID,
                memory_type="preference",
                workflow_scope="drafting",
                contact_scope=None,
                key=f"Preference {index}",
                value=f"Value {index}",
                source="explicit_user",
            )

        model = _ListCallingMemoryModel()
        agent = build_agent(llm=model)
        runtime = AgentRunRuntime(
            AgentRunStore(database),
            provider="fixture-provider",
            stream=lambda graph_input, thread_id: stream_agent(
                graph_input,
                thread_id,
                agent=agent,
            ),
        )

        events = asyncio.run(
            _collect(
                runtime.stream_new_run(
                    new_turn_input("List my drafting memories."),
                    "memory-list-thread",
                )
            )
        )
        database.engine.dispose()

        self.assertFalse(any(event["type"] == "interrupt" for event in events))
        self.assertEqual(10, model.tool_output["count"])
        self.assertEqual(10, len(model.tool_output["memories"]))
        self.assertIsNotNone(model.tool_output["next_cursor"])

    def test_memory_list_allows_only_one_page_per_user_turn(self):
        from app.agent.graph import build_agent
        from app.services.agent_run_store import AgentRunStore
        from app.services.semantic_memory_store import SemanticMemoryStore

        database = DatabaseService(str(self.data_dir / "emails.db"))
        store = SemanticMemoryStore(database)
        for index in range(12):
            store.remember(
                profile_id=LOCAL_PROFILE_ID,
                memory_type="preference",
                workflow_scope="drafting",
                contact_scope=None,
                key=f"Preference {index}",
                value=f"Value {index}",
                source="explicit_user",
            )

        model = _PagingListCallingMemoryModel()
        agent = build_agent(llm=model)
        runtime = AgentRunRuntime(
            AgentRunStore(database),
            provider="fixture-provider",
            stream=lambda graph_input, thread_id: stream_agent(
                graph_input,
                thread_id,
                agent=agent,
            ),
        )

        first_turn_events = asyncio.run(
            _collect(
                runtime.stream_new_run(
                    new_turn_input("List my drafting memories."),
                    "memory-paging-thread",
                )
            )
        )
        self.assertEqual(1, len(model.pages))
        self.assertEqual(10, model.pages[0]["count"])
        self.assertEqual(1, len(model.denials))
        self.assertEqual(
            1,
            sum(
                event.get("type") == "trace"
                and event.get("step") == "tool_start"
                and event.get("tool") == "list_memories"
                for event in first_turn_events
            ),
        )

        second_turn_events = asyncio.run(
            _collect(
                runtime.stream_new_run(
                    new_turn_input("Show me the next memory page."),
                    "memory-paging-thread",
                )
            )
        )
        database.engine.dispose()

        self.assertEqual(2, len(model.pages))
        self.assertEqual(2, model.pages[1]["count"])
        self.assertEqual(
            1,
            sum(
                event.get("type") == "trace"
                and event.get("step") == "tool_start"
                and event.get("tool") == "list_memories"
                for event in second_turn_events
            ),
        )

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
