import asyncio
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from langchain_core.messages import AIMessage, ToolMessage

from app.agent.graph import build_agent
from app.agent.run_runtime import AgentRunRuntime
from app.agent.stream import new_turn_input, stream_agent
from app.models.semantic_memory import SemanticMemory
from app.services.agent_run_store import AgentRunStore
from app.services.database import DatabaseService


async def _collect(stream):
    return [event async for event in stream]


class _TokenBudgetLLM:
    def __init__(self):
        self.completed_tool_calls = 0
        self.model_calls = 0

    def bind_tools(self, tools):
        return self

    async def ainvoke(self, messages):
        if isinstance(messages[-1], ToolMessage):
            self.completed_tool_calls += 1
        self.model_calls += 1
        total_tokens = 100 if self.model_calls == 1 else 80
        return AIMessage(
            content="",
            tool_calls=[
                {
                    "name": "read_calendar",
                    "args": {"days_ahead": 1},
                    "id": f"token-call-{self.model_calls}",
                    "type": "tool_call",
                }
            ],
            response_metadata={"model_name": "fixture-model"},
            usage_metadata={
                "input_tokens": total_tokens - 20,
                "output_tokens": 20,
                "total_tokens": total_tokens,
            },
        )


class _FinalUsageLLM:
    def __init__(self):
        self.model_calls = 0

    def bind_tools(self, tools):
        return self

    async def ainvoke(self, messages):
        self.model_calls += 1
        return AIMessage(
            content="completed",
            usage_metadata={
                "input_tokens": 80,
                "output_tokens": 20,
                "total_tokens": 100,
            },
        )


class _MemoryBudgetLLM:
    def __init__(self):
        self.completed_tool_calls = 0

    def bind_tools(self, tools):
        return self

    async def ainvoke(self, messages):
        if isinstance(messages[-1], ToolMessage):
            self.completed_tool_calls += 1
            return AIMessage(
                content="done",
                usage_metadata={
                    "input_tokens": 1,
                    "output_tokens": 0,
                    "total_tokens": 1,
                },
            )
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
                    "id": "budgeted-memory-call",
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
                    "id": "budgeted-memory-call-2",
                    "type": "tool_call",
                },
            ],
            usage_metadata={
                "input_tokens": 80,
                "output_tokens": 20,
                "total_tokens": 100,
            },
        )


class _UsageReportingConsentReviewer:
    def with_structured_output(self, schema, *, include_raw=False):
        return self

    async def ainvoke(self, messages):
        return {
            "raw": AIMessage(
                content="",
                usage_metadata={
                    "input_tokens": 25,
                    "output_tokens": 5,
                    "total_tokens": 30,
                },
            ),
            "parsed": {
                "decision": "allow",
                "reason_code": "explicit_user_request",
            },
            "parsing_error": None,
        }


class TokenBudgetTest(unittest.TestCase):
    def _run_memory_budget_turn(self, max_total_tokens):
        model = _MemoryBudgetLLM()

        with tempfile.TemporaryDirectory() as temp_dir, patch(
            "app.services.database.DATA_DIR",
            Path(temp_dir),
        ):
            database = DatabaseService(str(Path(temp_dir) / "emails.db"))
            agent = build_agent(
                llm=model,
                memory_consent_reviewer=_UsageReportingConsentReviewer(),
                max_total_tokens=max_total_tokens,
            )
            config = {"configurable": {"thread_id": "reviewer-budget-thread"}}
            runtime = AgentRunRuntime(
                AgentRunStore(database),
                provider="fixture-provider",
                stream=lambda graph_input, thread_id: stream_agent(
                    graph_input,
                    thread_id,
                    agent=agent,
                ),
            )

            async def exercise():
                try:
                    await _collect(
                        runtime.stream_new_run(
                            new_turn_input(
                                "Remember that I prefer concise replies and afternoon "
                                "meetings."
                            ),
                            "reviewer-budget-thread",
                        )
                    )
                    error = None
                except RuntimeError as caught:
                    error = caught
                return error, await agent.aget_state(config)

            try:
                error, state = asyncio.run(exercise())
                session = database.Session()
                try:
                    memory_count = session.query(SemanticMemory).count()
                finally:
                    session.close()
            finally:
                database.engine.dispose()

        return model, error, state, memory_count

    def test_token_budget_stops_before_tool_after_overage_model_step(self):
        model = _TokenBudgetLLM()
        agent = build_agent(llm=model, max_total_tokens=150)
        config = {"configurable": {"thread_id": "token-budget-thread"}}

        async def exercise():
            with self.assertRaisesRegex(RuntimeError, "150 tokens"):
                await agent.ainvoke(new_turn_input("keep checking"), config)
            return await agent.aget_state(config)

        state = asyncio.run(exercise())

        self.assertEqual(2, model.model_calls)
        self.assertEqual(1, model.completed_tool_calls)
        self.assertEqual(100, state.values["total_tokens_used"])
        self.assertEqual(1, state.values["tool_calls_used"])

    def test_new_turn_resets_token_budget_on_same_thread(self):
        model = _FinalUsageLLM()
        agent = build_agent(llm=model, max_total_tokens=150)
        config = {"configurable": {"thread_id": "token-budget-reset-thread"}}

        async def exercise():
            await agent.ainvoke(new_turn_input("first turn"), config)
            result = await agent.ainvoke(new_turn_input("second turn"), config)
            state = await agent.aget_state(config)
            return result, state

        result, state = asyncio.run(exercise())

        self.assertEqual("completed", result["messages"][-1].content)
        self.assertEqual(2, model.model_calls)
        self.assertEqual(100, state.values["total_tokens_used"])

    def test_concurrent_reviewer_usage_stops_memory_tools_before_budget_overage(self):
        model, error, state, memory_count = self._run_memory_budget_turn(150)

        self.assertIsInstance(error, RuntimeError)
        self.assertIn("150 tokens", str(error))
        self.assertEqual(0, model.completed_tool_calls)
        self.assertEqual(0, memory_count)
        self.assertEqual(100, state.values["total_tokens_used"])

    def test_reviewer_usage_is_persisted_in_run_token_total(self):
        model, error, state, memory_count = self._run_memory_budget_turn(200)

        self.assertIsNone(error)
        self.assertEqual(1, model.completed_tool_calls)
        self.assertEqual(2, memory_count)
        self.assertEqual(161, state.values["total_tokens_used"])


if __name__ == "__main__":
    unittest.main()
