import asyncio
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from langchain_core.messages import AIMessage, ToolMessage

from app.agent import graph as agent_graph
from app.agent.graph import build_agent
from app.agent.run_runtime import AgentRunRuntime
from app.agent.stream import new_turn_input, stream_agent
from app.services.agent_run_store import AgentRunStore
from app.services.database import DatabaseService


def _fault_injecting_tool_type():
    try:
        from app.agent.fault_injection import FaultInjectingTool
    except ImportError as exc:
        raise AssertionError("tool fault injection adapter is not implemented") from exc
    return FaultInjectingTool


class _ToolCallingModel:
    def __init__(self):
        self.attempt_count = 0

    def bind_tools(self, tools):
        return self

    async def ainvoke(self, messages):
        self.attempt_count += 1
        return AIMessage(
            content="",
            tool_calls=[
                {
                    "name": "read_calendar",
                    "args": {"days_ahead": 1},
                    "id": "permanent-tool-call",
                    "type": "tool_call",
                }
            ],
        )


class _ToolFailureRecoveryModel:
    def __init__(self):
        self.attempt_count = 0

    def bind_tools(self, tools):
        return self

    async def ainvoke(self, messages):
        self.attempt_count += 1
        for index, message in enumerate(messages):
            if not isinstance(message, AIMessage) or not message.tool_calls:
                continue
            responses = messages[index + 1:index + 1 + len(message.tool_calls)]
            if any(
                not isinstance(response, ToolMessage)
                or response.tool_call_id != tool_call["id"]
                for response, tool_call in zip(responses, message.tool_calls)
            ):
                raise ValueError("unpaired tool call in checkpoint")
        if any(isinstance(message, ToolMessage) for message in messages):
            return AIMessage(content="recovered after failed tool")
        return AIMessage(
            content="",
            tool_calls=[
                {
                    "name": "read_calendar",
                    "args": {"days_ahead": 1},
                    "id": "recovery-tool-call",
                    "type": "tool_call",
                }
            ],
        )


class ToolFailureTest(unittest.TestCase):
    def test_permanent_tool_failure_is_not_retried_and_fails_run(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            db = DatabaseService(str(Path(temp_dir) / "tool-failure.db"))
            store = AgentRunStore(db)
            model = _ToolCallingModel()
            fault_tool = _fault_injecting_tool_type()(
                ValueError("injected permanent tool validation failure")
            )
            agent = build_agent(llm=model)

            def graph_stream(graph_input, thread_id):
                return stream_agent(graph_input, thread_id, agent=agent)

            runtime = AgentRunRuntime(
                store,
                provider="fixture-provider",
                stream=graph_stream,
            )

            async def exercise():
                events = []
                with self.assertRaisesRegex(RuntimeError, "Tool read_calendar failed"):
                    async for event in runtime.stream_new_run(
                        new_turn_input("check my calendar"),
                        "permanent-tool-thread",
                    ):
                        events.append(event)
                return events

            with patch.dict(
                agent_graph.TOOLS_BY_NAME,
                {"read_calendar": fault_tool},
            ):
                events = asyncio.run(exercise())

            run_events = [event for event in events if event["type"] == "run"]
            run_id = run_events[0]["run_id"]

            self.assertEqual(1, fault_tool.attempt_count)
            self.assertEqual(1, model.attempt_count)
            self.assertEqual(
                ["running", "failed"],
                [event["status"] for event in run_events],
            )
            self.assertEqual("failed", store.get_run(run_id)["status"])
            self.assertEqual("terminal", store.get_run(run_id)["error_category"])
            self.assertEqual(
                "terminal",
                store.list_events(run_id)[-1]["error_category"],
            )
            tool_results = [
                event
                for event in store.list_events(run_id)
                if event["event_type"] == "tool_result"
            ]
            self.assertEqual(1, len(tool_results))
            self.assertEqual("permanent-tool-call", tool_results[0]["tool_call_id"])
            self.assertEqual("read_calendar", tool_results[0]["tool_name"])
            self.assertEqual("terminal", tool_results[0]["error_category"])
            self.assertEqual(
                {
                    "schema_version": 1,
                    "kind": "error",
                    "result": "failed",
                },
                tool_results[0]["outcome"],
            )
            db.engine.dispose()

    def test_failed_tool_keeps_same_thread_checkpoint_recoverable(self):
        from app.agent.runtime import open_agent_checkpointer

        async def exercise(checkpoint_path: Path, runtime_path: Path):
            db = DatabaseService(str(runtime_path))
            store = AgentRunStore(db)
            model = _ToolFailureRecoveryModel()

            async with open_agent_checkpointer(checkpoint_path) as checkpointer:
                agent = build_agent(checkpointer=checkpointer, llm=model)

                def graph_stream(graph_input, thread_id):
                    return stream_agent(graph_input, thread_id, agent=agent)

                runtime = AgentRunRuntime(
                    store,
                    provider="fixture-provider",
                    stream=graph_stream,
                )

                with self.assertRaisesRegex(RuntimeError, "Tool read_calendar failed"):
                    async for _ in runtime.stream_new_run(
                        new_turn_input("check my calendar"),
                        "recoverable-failure-thread",
                    ):
                        pass

                resumed_events = [
                    event
                    async for event in runtime.stream_new_run(
                        new_turn_input("try again"),
                        "recoverable-failure-thread",
                    )
                ]
                state = await agent.aget_state(
                    {"configurable": {"thread_id": "recoverable-failure-thread"}}
                )

            db.engine.dispose()
            return resumed_events, state

        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            with patch.dict(
                agent_graph.TOOLS_BY_NAME,
                {
                    "read_calendar": _fault_injecting_tool_type()(
                        ValueError("injected permanent tool validation failure")
                    )
                },
            ):
                resumed_events, state = asyncio.run(
                    exercise(
                        temp_path / "agent-checkpoints.sqlite",
                        temp_path / "runtime.db",
                    )
                )

        self.assertEqual("completed", resumed_events[-2]["status"])
        self.assertEqual("recovered after failed tool", state.values["messages"][-1].content)


if __name__ == "__main__":
    unittest.main()
