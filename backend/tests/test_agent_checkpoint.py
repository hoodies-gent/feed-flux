import asyncio
import tempfile
import unittest
from pathlib import Path

from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage
from langchain_core.outputs import ChatGeneration, ChatResult
from langgraph.types import Command

from app.agent.graph import build_agent
from app.agent.stream import stream_agent


class _ApprovalLLM(BaseChatModel):
    @property
    def _llm_type(self) -> str:
        return "approval-test"

    def bind_tools(self, tools, **kwargs):
        return self

    def _generate(self, messages, stop=None, run_manager=None, **kwargs) -> ChatResult:
        if isinstance(messages[-1], ToolMessage):
            message = AIMessage(content="completed after restart")
        else:
            message = AIMessage(
                content="",
                tool_calls=[
                    {
                        "name": "send_test_email",
                        "args": {
                            "recipient": "fixture@example.com",
                            "subject": "checkpoint test",
                            "body": "fixture body",
                        },
                        "id": "checkpoint-tool-call",
                        "type": "tool_call",
                    }
                ],
            )
        return ChatResult(generations=[ChatGeneration(message=message)])


class AgentCheckpointTest(unittest.TestCase):
    def test_interrupt_resumes_after_runtime_reopens_same_sqlite_database(self):
        try:
            from app.agent.runtime import open_agent_checkpointer
        except ModuleNotFoundError:
            self.fail("persistent agent runtime is not implemented")

        async def exercise(checkpoint_path: Path):
            thread_id = "checkpoint-restart-thread"
            config = {"configurable": {"thread_id": thread_id}}

            async with open_agent_checkpointer(checkpoint_path) as first_checkpointer:
                first_agent = build_agent(checkpointer=first_checkpointer, llm=_ApprovalLLM())
                first_events = [
                    event
                    async for event in stream_agent(
                        {"messages": [HumanMessage(content="send a fixture email")]},
                        thread_id,
                        agent=first_agent,
                    )
                ]

            self.assertTrue(any(event["type"] == "interrupt" for event in first_events))

            async with open_agent_checkpointer(checkpoint_path) as resumed_checkpointer:
                resumed_agent = build_agent(checkpointer=resumed_checkpointer, llm=_ApprovalLLM())
                resumed_events = [
                    event
                    async for event in stream_agent(
                        Command(resume={"approve": True}),
                        thread_id,
                        agent=resumed_agent,
                    )
                ]
                state = await resumed_agent.aget_state(config)

            self.assertFalse(any(event["type"] == "interrupt" for event in resumed_events))
            self.assertEqual((), state.next)
            self.assertTrue(any(isinstance(message, ToolMessage) for message in state.values["messages"]))
            self.assertEqual("completed after restart", state.values["messages"][-1].content)

        with tempfile.TemporaryDirectory() as temp_dir:
            asyncio.run(exercise(Path(temp_dir) / "agent-checkpoints.sqlite"))


if __name__ == "__main__":
    unittest.main()
