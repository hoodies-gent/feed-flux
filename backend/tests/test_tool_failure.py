import asyncio
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from langchain_core.messages import AIMessage

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
                with self.assertRaisesRegex(
                    ValueError,
                    "injected permanent tool validation failure",
                ):
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
            db.engine.dispose()


if __name__ == "__main__":
    unittest.main()
