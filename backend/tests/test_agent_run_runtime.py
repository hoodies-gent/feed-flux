import asyncio
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import api
from langchain_core.messages import AIMessage, ToolMessage
from langgraph.errors import GraphRecursionError
from pydantic import BaseModel, ValidationError

from app.agent.graph import RunTokenBudgetExceeded, build_agent
from app.agent.stream import stream_agent
from app.agent.usage import TokenPricing
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


class _BlockingStream:
    def __call__(self, graph_input, thread_id):
        async def generate():
            yield {"type": "token", "content": "started"}
            await asyncio.Event().wait()

        return generate()


class _EventuallyCompletingToolLoop:
    def __init__(self, tool_calls_before_completion: int):
        self.tool_calls_before_completion = tool_calls_before_completion
        self.tool_call_count = 0

    def bind_tools(self, tools):
        return self

    async def ainvoke(self, messages):
        if isinstance(messages[-1], ToolMessage):
            self.tool_call_count += 1
        if self.tool_call_count < self.tool_calls_before_completion:
            return AIMessage(
                content="",
                tool_calls=[
                    {
                        "name": "read_calendar",
                        "args": {"days_ahead": 1},
                        "id": f"loop-{self.tool_call_count}",
                        "type": "tool_call",
                    }
                ],
            )
        return AIMessage(content="eventually completed")


class _InvalidRuntimePayload(BaseModel):
    count: int


def _validation_error() -> ValidationError:
    try:
        _InvalidRuntimePayload.model_validate({"count": "invalid"})
    except ValidationError as error:
        return error
    raise AssertionError("fixture must produce a validation error")


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
                    {
                        "type": "trace",
                        "step": "tool_start",
                        "tool": "find_email",
                        "tool_call_id": "find-email-call-1",
                    },
                    {
                        "type": "trace",
                        "step": "tool_end",
                        "tool": "find_email",
                        "tool_call_id": "find-email-call-1",
                    },
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
        tool_call = next(
            event for event in ledger_events if event["event_type"] == "tool_call"
        )
        tool_result = next(
            event for event in ledger_events if event["event_type"] == "tool_result"
        )
        self.assertEqual("find_email", tool_call["tool_name"])
        self.assertEqual("find-email-call-1", tool_call["tool_call_id"])
        self.assertEqual("deepseek", tool_call["provider"])
        self.assertEqual("find-email-call-1", tool_result["tool_call_id"])
        self.assertEqual("deepseek", tool_result["provider"])

    def test_usage_events_are_persisted_with_cost_and_not_forwarded(self):
        runtime = _runtime_type()(
            self.store,
            provider="fixture-provider",
            stream=_ScriptedStream(
                [
                    {
                        "type": "usage",
                        "model": "fixture-model",
                        "usage": {
                            "input_tokens": 100,
                            "cached_input_tokens": 40,
                            "output_tokens": 20,
                            "total_tokens": 120,
                        },
                    },
                    {
                        "type": "usage",
                        "model": "fixture-model",
                        "usage": {
                            "input_tokens": 50,
                            "cached_input_tokens": 0,
                            "output_tokens": 10,
                            "total_tokens": 60,
                        },
                    },
                    {"type": "token", "content": "done"},
                    {"type": "done"},
                ]
            ),
            pricing=TokenPricing(
                input_usd_per_million=1.0,
                output_usd_per_million=2.0,
                cached_input_usd_per_million=0.25,
            ),
        )

        client_events = asyncio.run(
            _collect(runtime.stream_new_run({}, "usage-ledger-thread"))
        )
        run_id = next(
            event["run_id"] for event in client_events if event["type"] == "run"
        )

        self.assertNotIn("usage", [event["type"] for event in client_events])

        self.db.engine.dispose()
        reopened_db = DatabaseService(str(Path(self.temp_dir.name) / "runtime.db"))
        reopened_store = AgentRunStore(reopened_db)
        usage_events = [
            event
            for event in reopened_store.list_events(run_id)
            if event["event_type"] == "provider_usage"
        ]
        reopened_db.engine.dispose()

        self.assertEqual(2, len(usage_events))
        self.assertEqual(
            {
                "schema_version": 1,
                "model": "fixture-model",
                "usage": {
                    "input_tokens": 100,
                    "cached_input_tokens": 40,
                    "output_tokens": 20,
                    "total_tokens": 120,
                },
                "pricing": {
                    "input_usd_per_million": 1.0,
                    "output_usd_per_million": 2.0,
                    "cached_input_usd_per_million": 0.25,
                },
                "estimated_cost_usd": 0.00011,
            },
            usage_events[0]["outcome"],
        )
        self.assertEqual("fixture-provider", usage_events[0]["provider"])
        self.assertEqual(0.00007, usage_events[1]["outcome"]["estimated_cost_usd"])

    def test_completed_run_persists_sanitized_artifact_outcome(self):
        runtime = _runtime_type()(
            self.store,
            provider="fixture-provider",
            stream=_ScriptedStream(
                [
                    {
                        "type": "draft",
                        "draft_id": 7,
                        "email_id": "draft-email",
                        "body": "private draft body",
                    },
                    {
                        "type": "plan",
                        "bulk": [
                            {
                                "email_id": "bulk-email",
                                "reason": "private bulk reason",
                            }
                        ],
                        "needs_reply": [
                            {
                                "email_id": "reply-email",
                                "body_preview": "private email preview",
                            }
                        ],
                    },
                    {"type": "done"},
                ]
            ),
        )

        events = asyncio.run(
            _collect(runtime.stream_new_run({}, "artifact-outcome-thread"))
        )
        run_id = next(event["run_id"] for event in events if event["type"] == "run")

        self.db.engine.dispose()
        reopened_db = DatabaseService(str(Path(self.temp_dir.name) / "runtime.db"))
        reopened_store = AgentRunStore(reopened_db)
        persisted = reopened_store.get_run(run_id)
        completed_event = reopened_store.list_events(run_id)[-1]
        reopened_db.engine.dispose()

        expected = {
            "schema_version": 1,
            "kind": "completed",
            "artifacts": [
                {
                    "type": "draft",
                    "draft_id": 7,
                    "email_id": "draft-email",
                },
                {
                    "type": "triage_plan",
                    "bulk_email_ids": ["bulk-email"],
                    "needs_reply_email_ids": ["reply-email"],
                },
            ],
        }
        self.assertEqual(expected, persisted["outcome"])
        self.assertEqual(expected, completed_event["outcome"])
        self.assertNotIn("private", json.dumps(persisted["outcome"]))

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

    def test_interrupted_run_persists_sanitized_approval_outcome(self):
        runtime = _runtime_type()(
            self.store,
            provider="fixture-provider",
            stream=_ScriptedStream(
                [
                    {
                        "type": "interrupt",
                        "tool": "send_reply",
                        "tool_call_id": "approval-call-1",
                        "args": {"body": "private draft body"},
                        "draft_preview": {"body": "private preview"},
                        "references": [{"output": "private reference"}],
                    },
                    {"type": "done"},
                ]
            ),
        )

        events = asyncio.run(
            _collect(runtime.stream_new_run({}, "approval-outcome-thread"))
        )
        run_id = next(event["run_id"] for event in events if event["type"] == "run")

        self.db.engine.dispose()
        reopened_db = DatabaseService(str(Path(self.temp_dir.name) / "runtime.db"))
        reopened_store = AgentRunStore(reopened_db)
        persisted = reopened_store.get_run(run_id)
        interrupted_event = reopened_store.list_events(run_id)[-1]
        reopened_db.engine.dispose()

        expected = {
            "schema_version": 1,
            "kind": "awaiting_approval",
            "tool": "send_reply",
            "tool_call_id": "approval-call-1",
        }
        self.assertEqual(expected, persisted["outcome"])
        self.assertEqual(expected, interrupted_event["outcome"])
        self.assertNotIn("private", json.dumps(persisted["outcome"]))

    def test_transient_stream_failure_persists_category_before_reraising(self):
        runtime = _runtime_type()(
            self.store,
            provider="deepseek",
            stream=_ScriptedStream([TimeoutError("provider timed out")]),
        )

        async def exercise():
            events = []
            with self.assertRaisesRegex(TimeoutError, "provider timed out"):
                async for event in runtime.stream_new_run({}, "failed-thread"):
                    events.append(event)
            return events

        events = asyncio.run(exercise())
        run_events = [event for event in events if event["type"] == "run"]
        run_id = run_events[0]["run_id"]

        self.assertEqual(["running", "failed"], [event["status"] for event in run_events])
        self.assertEqual("failed", self.store.get_run(run_id)["status"])
        self.assertEqual("transient", self.store.get_run(run_id)["error_category"])
        self.assertEqual("transient", run_events[-1]["error_category"])
        self.assertEqual("transient", self.store.list_events(run_id)[-1]["error_category"])

    def test_run_timeout_stops_blocked_stream_and_persists_failure(self):
        runtime = _runtime_type()(
            self.store,
            provider="fixture",
            stream=_BlockingStream(),
            run_timeout_seconds=0.01,
        )

        async def exercise():
            events = []
            with self.assertRaisesRegex(TimeoutError, "exceeded 0.01 seconds"):
                async for event in runtime.stream_new_run({}, "timeout-thread"):
                    events.append(event)
            return events

        events = asyncio.run(asyncio.wait_for(exercise(), timeout=0.5))
        run_events = [event for event in events if event["type"] == "run"]
        run_id = run_events[0]["run_id"]

        self.assertEqual(
            ["running", "failed"],
            [event["status"] for event in run_events],
        )
        self.assertEqual("failed", self.store.get_run(run_id)["status"])
        self.assertEqual("transient", self.store.get_run(run_id)["error_category"])
        self.assertEqual(
            "transient",
            self.store.list_events(run_id)[-1]["error_category"],
        )

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

    def test_graph_step_limit_stops_loop_and_persists_failed_run(self):
        agent = build_agent(llm=_EventuallyCompletingToolLoop(6))

        async def bounded_stream(graph_input, thread_id):
            async for event in stream_agent(
                graph_input,
                thread_id,
                agent=agent,
                max_graph_steps=4,
            ):
                yield event

        runtime = _runtime_type()(
            self.store,
            provider="fixture",
            stream=bounded_stream,
        )

        async def exercise():
            events = []
            with self.assertRaises(GraphRecursionError):
                async for event in runtime.stream_new_run(
                    {"messages": [{"role": "user", "content": "loop"}]},
                    "step-limit-thread",
                ):
                    events.append(event)
            return events

        events = asyncio.run(exercise())
        run_events = [event for event in events if event["type"] == "run"]
        run_id = run_events[0]["run_id"]

        self.assertEqual(
            ["running", "failed"],
            [event["status"] for event in run_events],
        )
        self.assertEqual("failed", self.store.get_run(run_id)["status"])
        self.assertEqual("terminal", self.store.get_run(run_id)["error_category"])
        self.assertEqual(
            "terminal",
            self.store.list_events(run_id)[-1]["error_category"],
        )

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

    def test_api_ndjson_applies_configured_token_pricing(self):
        async def fake_stream(graph_input, thread_id):
            yield {
                "type": "usage",
                "model": "fixture-model",
                "usage": {
                    "input_tokens": 100,
                    "cached_input_tokens": 40,
                    "output_tokens": 20,
                    "total_tokens": 120,
                },
            }
            yield {"type": "done"}

        async def collect_lines():
            return [
                json.loads(line)
                async for line in api._agent_ndjson({}, "priced-api-thread")
            ]

        with (
            patch.object(api, "db", self.db),
            patch("app.agent.run_runtime.stream_agent", fake_stream),
            patch.object(Config, "LLM_PROVIDER", "fixture-provider"),
            patch.object(
                Config,
                "LLM_INPUT_COST_USD_PER_MILLION",
                1.0,
                create=True,
            ),
            patch.object(
                Config,
                "LLM_OUTPUT_COST_USD_PER_MILLION",
                2.0,
                create=True,
            ),
            patch.object(
                Config,
                "LLM_CACHED_INPUT_COST_USD_PER_MILLION",
                0.25,
                create=True,
            ),
        ):
            events = asyncio.run(collect_lines())

        run_id = next(event["run_id"] for event in events if event["type"] == "run")
        usage_event = next(
            event
            for event in self.store.list_events(run_id)
            if event["event_type"] == "provider_usage"
        )

        self.assertEqual(
            {
                "input_usd_per_million": 1.0,
                "output_usd_per_million": 2.0,
                "cached_input_usd_per_million": 0.25,
            },
            usage_event["outcome"]["pricing"],
        )
        self.assertEqual(0.00011, usage_event["outcome"]["estimated_cost_usd"])

    def test_api_ndjson_returns_safe_explainable_error_for_failed_run(self):
        cases = (
            (
                TimeoutError("private timeout details"),
                "transient",
                "The agent service is temporarily busy or timed out. Please try again.",
            ),
            (
                _validation_error(),
                "llm_tool_repairable",
                "The agent could not complete a model or tool step. Please try again.",
            ),
            (
                PermissionError("private permission details"),
                "user_repairable",
                "The agent needs updated authorization or corrected input before it can continue.",
            ),
            (
                ValueError("private terminal details"),
                "terminal",
                "The agent could not complete this request. The run has stopped without further actions.",
            ),
            (
                RunTokenBudgetExceeded("private token budget details"),
                "terminal",
                "This run reached its execution budget and stopped. Completed local actions were kept; no further actions were taken.",
            ),
        )

        for index, (failure, category, message) in enumerate(cases):
            with self.subTest(category=category):
                async def failing_stream(graph_input, thread_id):
                    raise failure
                    yield

                async def collect_lines():
                    return [
                        json.loads(line)
                        async for line in api._agent_ndjson(
                            {},
                            f"safe-error-thread-{index}",
                        )
                    ]

                with (
                    patch.object(api, "db", self.db),
                    patch("app.agent.run_runtime.stream_agent", failing_stream),
                    patch.object(Config, "LLM_PROVIDER", "fixture-provider"),
                ):
                    events = asyncio.run(collect_lines())

                run_events = [event for event in events if event["type"] == "run"]
                error_event = next(event for event in events if event["type"] == "error")

                self.assertEqual(
                    ["running", "failed"],
                    [event["status"] for event in run_events],
                )
                self.assertEqual(category, run_events[-1]["error_category"])
                self.assertEqual(
                    {
                        "type": "error",
                        "error_category": category,
                        "content": message,
                    },
                    error_event,
                )
                self.assertNotIn("private", error_event["content"])


if __name__ == "__main__":
    unittest.main()
