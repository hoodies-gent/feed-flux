import asyncio
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import api

from app.core.config import Config
from app.services.agent_run_store import AgentRunStore
from app.services.database import DatabaseService


def _runtime_type():
    try:
        from app.agent.run_runtime import AgentRunRuntime
    except ModuleNotFoundError as exc:
        raise AssertionError("agent run runtime is not implemented") from exc
    return AgentRunRuntime


class _ScriptedStream:
    def __init__(self, *scripts):
        self.scripts = list(scripts)

    def __call__(self, graph_input, thread_id):
        script = self.scripts.pop(0)

        async def generate():
            for item in script:
                if isinstance(item, BaseException):
                    raise item
                yield item

        return generate()


async def _collect(stream):
    return [event async for event in stream]


class AgentRunRuntimeTest(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db = DatabaseService(str(Path(self.temp_dir.name) / "runtime.db"))
        self.store = AgentRunStore(self.db)

    def tearDown(self):
        self.db.engine.dispose()
        self.temp_dir.cleanup()

    def test_completed_stream_persists_lifecycle_and_tool_event(self):
        runtime = _runtime_type()(
            self.store,
            provider="deepseek",
            stream=_ScriptedStream(
                [
                    {"type": "trace", "step": "tool_start", "tool": "find_email"},
                    {"type": "token", "content": "done"},
                    {"type": "done"},
                ]
            ),
        )

        events = asyncio.run(_collect(runtime.stream_new_run({}, "completed-thread")))
        run_events = [event for event in events if event["type"] == "run"]
        run_id = run_events[0]["run_id"]
        persisted = self.store.get_run(run_id)
        ledger_events = self.store.list_events(run_id)

        self.assertEqual(["running", "completed"], [event["status"] for event in run_events])
        self.assertEqual("completed", persisted["status"])
        self.assertEqual("deepseek", persisted["provider"])
        self.assertEqual("done", events[-1]["type"])
        tool_event = next(event for event in ledger_events if event["event_type"] == "tool_call")
        self.assertEqual("find_email", tool_event["tool_name"])
        self.assertIsNone(tool_event["tool_call_id"])

    def test_resume_reuses_interrupted_run_id(self):
        runtime = _runtime_type()(
            self.store,
            provider="deepseek",
            stream=_ScriptedStream(
                [{"type": "interrupt", "tool": "send_test_email"}, {"type": "done"}],
                [{"type": "token", "content": "approved"}, {"type": "done"}],
            ),
        )

        interrupted_events = asyncio.run(
            _collect(runtime.stream_new_run({}, "resume-thread"))
        )
        run_id = next(event["run_id"] for event in interrupted_events if event["type"] == "run")
        resumed_events = asyncio.run(
            _collect(runtime.stream_resumed_run({}, "resume-thread"))
        )

        resumed_run_ids = {
            event["run_id"] for event in resumed_events if event["type"] == "run"
        }
        status_events = [
            event["status"]
            for event in self.store.list_events(run_id)
            if event["event_type"] == "status"
        ]
        self.assertEqual({run_id}, resumed_run_ids)
        self.assertEqual("completed", self.store.get_run(run_id)["status"])
        self.assertEqual(
            ["queued", "running", "interrupted", "running", "completed"],
            status_events,
        )

    def test_stream_failure_marks_run_failed_before_reraising(self):
        runtime = _runtime_type()(
            self.store,
            provider="deepseek",
            stream=_ScriptedStream([RuntimeError("provider unavailable")]),
        )

        async def exercise():
            events = []
            with self.assertRaisesRegex(RuntimeError, "provider unavailable"):
                async for event in runtime.stream_new_run({}, "failed-thread"):
                    events.append(event)
            return events

        events = asyncio.run(exercise())
        run_events = [event for event in events if event["type"] == "run"]
        run_id = run_events[0]["run_id"]

        self.assertEqual(["running", "failed"], [event["status"] for event in run_events])
        self.assertEqual("failed", self.store.get_run(run_id)["status"])
        self.assertIsNone(self.store.get_run(run_id)["error_category"])

    def test_stream_cancellation_marks_run_cancelled(self):
        runtime = _runtime_type()(
            self.store,
            provider="deepseek",
            stream=_ScriptedStream([asyncio.CancelledError()]),
        )

        async def exercise():
            events = []
            with self.assertRaises(asyncio.CancelledError):
                async for event in runtime.stream_new_run({}, "cancelled-thread"):
                    events.append(event)
            return events

        events = asyncio.run(exercise())
        run_id = next(event["run_id"] for event in events if event["type"] == "run")

        self.assertEqual("cancelled", self.store.get_run(run_id)["status"])

    def test_closing_stream_marks_run_cancelled(self):
        runtime = _runtime_type()(
            self.store,
            provider="deepseek",
            stream=_ScriptedStream(
                [{"type": "token", "content": "still running"}, {"type": "done"}]
            ),
        )

        async def exercise():
            stream = runtime.stream_new_run({}, "closed-thread")
            first_event = await anext(stream)
            await stream.aclose()
            return first_event

        first_event = asyncio.run(exercise())

        self.assertEqual("running", first_event["status"])
        self.assertEqual("cancelled", self.store.get_run(first_event["run_id"])["status"])

    def test_api_ndjson_uses_run_runtime(self):
        _runtime_type()

        async def fake_stream(graph_input, thread_id):
            yield {"type": "token", "content": "hello"}
            yield {"type": "done"}

        async def collect_lines():
            return [
                json.loads(line)
                async for line in api._agent_ndjson({}, "api-thread")
            ]

        with (
            patch.object(api, "db", self.db),
            patch("app.agent.run_runtime.stream_agent", fake_stream),
            patch.object(Config, "LLM_PROVIDER", "glm"),
        ):
            events = asyncio.run(collect_lines())

        run_events = [event for event in events if event["type"] == "run"]
        self.assertEqual(["running", "completed"], [event["status"] for event in run_events])
        persisted = self.store.get_run(run_events[0]["run_id"])
        self.assertEqual("completed", persisted["status"])
        self.assertEqual("glm", persisted["provider"])


if __name__ == "__main__":
    unittest.main()
