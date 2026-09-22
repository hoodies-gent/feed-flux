import asyncio
import unittest

from langchain_core.messages import AIMessage, ToolMessage

from app.agent.graph import build_agent
from app.agent.stream import new_turn_input


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


class TokenBudgetTest(unittest.TestCase):
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


if __name__ == "__main__":
    unittest.main()
