import asyncio
import tempfile
import unittest
from pathlib import Path

from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import AIMessage, ToolMessage
from langchain_core.outputs import ChatGeneration, ChatResult

from app.agent.graph import build_agent
from app.agent.run_runtime import AgentRunRuntime
from app.agent.stream import new_turn_input, resume_input, stream_agent
from app.services.agent_run_store import AgentRunStore
from app.services.database import DatabaseService


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

        async def exercise(checkpoint_path: Path, runtime_path: Path):
            thread_id = "checkpoint-restart-thread"
            config = {"configurable": {"thread_id": thread_id}}
            first_db = DatabaseService(str(runtime_path))
            first_store = AgentRunStore(first_db)

            async with open_agent_checkpointer(checkpoint_path) as first_checkpointer:
                first_agent = build_agent(checkpointer=first_checkpointer, llm=_ApprovalLLM())
                first_runtime = AgentRunRuntime(
                    first_store,
                    provider="fixture-provider",
                    stream=lambda graph_input, active_thread_id: stream_agent(
                        graph_input,
                        active_thread_id,
                        agent=first_agent,
                    ),
                )
                first_events = [
                    event
                    async for event in first_runtime.stream_new_run(
                        new_turn_input("send a fixture email"),
                        thread_id,
                    )
                ]
            first_db.engine.dispose()

            self.assertTrue(any(event["type"] == "interrupt" for event in first_events))
            run_id = next(
                event["run_id"]
                for event in first_events
                if event.get("type") == "run"
            )

            resumed_db = DatabaseService(str(runtime_path))
            resumed_store = AgentRunStore(resumed_db)

            async with open_agent_checkpointer(checkpoint_path) as resumed_checkpointer:
                resumed_agent = build_agent(checkpointer=resumed_checkpointer, llm=_ApprovalLLM())
                resumed_runtime = AgentRunRuntime(
                    resumed_store,
                    provider="fixture-provider",
                    stream=lambda graph_input, active_thread_id: stream_agent(
                        graph_input,
                        active_thread_id,
                        agent=resumed_agent,
                    ),
                )
                resumed_events = [
                    event
                    async for event in resumed_runtime.stream_resumed_run(
                        resume_input(approve=True),
                        thread_id,
                    )
                ]
                state = await resumed_agent.aget_state(config)

            persisted = resumed_store.get_run(run_id)
            status_events = [
                event["status"]
                for event in resumed_store.list_events(run_id)
                if event["event_type"] == "status"
            ]
            resumed_db.engine.dispose()

            self.assertFalse(any(event["type"] == "interrupt" for event in resumed_events))
            self.assertEqual(
                {run_id},
                {
                    event["run_id"]
                    for event in [*first_events, *resumed_events]
                    if event.get("type") == "run"
                },
            )
            self.assertEqual("completed", persisted["status"])
            self.assertEqual(
                ["queued", "running", "interrupted", "running", "completed"],
                status_events,
            )
            self.assertEqual((), state.next)
            self.assertTrue(any(isinstance(message, ToolMessage) for message in state.values["messages"]))
            self.assertEqual("completed after restart", state.values["messages"][-1].content)

        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            asyncio.run(
                exercise(
                    temp_path / "agent-checkpoints.sqlite",
                    temp_path / "runtime.db",
                )
            )


if __name__ == "__main__":
    unittest.main()
