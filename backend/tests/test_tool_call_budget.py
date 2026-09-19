import asyncio
import unittest

from langchain_core.messages import AIMessage, HumanMessage, ToolMessage

from app.agent.graph import build_agent
from app.agent.stream import new_turn_input


class _LoopingReadOnlyLLM:
    def __init__(self):
        self.completed_tool_calls = 0

    def bind_tools(self, tools):
        return self

    async def ainvoke(self, messages):
        if isinstance(messages[-1], ToolMessage):
            self.completed_tool_calls += 1
        return AIMessage(
            content="",
            tool_calls=[
                {
                    "name": "read_calendar",
                    "args": {"days_ahead": 1},
                    "id": f"budget-call-{self.completed_tool_calls}",
                    "type": "tool_call",
                }
            ],
        )


class _OneToolPerTurnLLM:
    def __init__(self):
        self.tool_call_count = 0

    def bind_tools(self, tools):
        return self

    async def ainvoke(self, messages):
        if isinstance(messages[-1], HumanMessage):
            return AIMessage(
                content="",
                tool_calls=[
                    {
                        "name": "read_calendar",
                        "args": {"days_ahead": 1},
                        "id": f"turn-call-{self.tool_call_count}",
                        "type": "tool_call",
                    }
                ],
            )
        self.tool_call_count += 1
        return AIMessage(content="completed")


class ToolCallBudgetTest(unittest.TestCase):
    def test_tool_call_budget_stops_before_next_tool_execution(self):
        model = _LoopingReadOnlyLLM()
        agent = build_agent(llm=model, max_tool_calls=2)
        config = {"configurable": {"thread_id": "tool-budget-thread"}}

        async def exercise():
            with self.assertRaisesRegex(RuntimeError, "2 tool calls"):
                await agent.ainvoke(
                    {"messages": [HumanMessage(content="keep checking")]},
                    config,
                )
            return await agent.aget_state(config)

        state = asyncio.run(exercise())

        self.assertEqual(2, model.completed_tool_calls)
        self.assertEqual(2, state.values["tool_calls_used"])

    def test_new_turn_resets_tool_call_budget_on_same_thread(self):
        model = _OneToolPerTurnLLM()
        agent = build_agent(llm=model, max_tool_calls=1)
        config = {"configurable": {"thread_id": "tool-budget-reset-thread"}}

        async def exercise():
            await agent.ainvoke(new_turn_input("first turn"), config)
            result = await agent.ainvoke(new_turn_input("second turn"), config)
            state = await agent.aget_state(config)
            return result, state

        result, state = asyncio.run(exercise())

        self.assertEqual("completed", result["messages"][-1].content)
        self.assertEqual(2, model.tool_call_count)
        self.assertEqual(1, state.values["tool_calls_used"])


if __name__ == "__main__":
    unittest.main()
