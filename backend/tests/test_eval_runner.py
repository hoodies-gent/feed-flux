import asyncio
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from app.agent.execution_context import current_thread_id, current_tool_call_id
from app.agent.tools import save_reply_draft
from app.evals.grader import load_golden_suite
from app.evals.recorder import TrialRecorder
from app.evals.runner import TokenPricing, _build_provider_agent, run_suite, run_trial


async def _meeting_events(graph_input, thread_id, callbacks, tool_output_limit):
    callbacks[0].usage_metadata["deepseek-chat"] = {
        "input_tokens": 100,
        "output_tokens": 20,
        "total_tokens": 120,
        "input_token_details": {"cache_read": 40},
    }
    yield {
        "type": "trace",
        "step": "tool_start",
        "tool": "find_email",
        "args": {"sender_contains": "Priya"},
    }
    yield {
        "type": "trace",
        "step": "tool_end",
        "tool": "find_email",
        "output": "[{'id': 'eval-mtg-002'}]",
    }
    yield {
        "type": "trace",
        "step": "tool_start",
        "tool": "read_calendar",
        "args": {"days_ahead": 7},
    }
    yield {
        "type": "trace",
        "step": "tool_end",
        "tool": "read_calendar",
        "output": "{'free_slots': ['Tue Sep 15 10:30']}",
    }
    reply_args = {
        "original_email_id": "eval-mtg-002",
        "recipient": "priya.nair@acme.co",
        "subject": "Re: Design review",
        "body": "Tuesday at 10:30 works for me.",
    }
    yield {
        "type": "trace",
        "step": "tool_start",
        "tool": "save_reply_draft",
        "args": reply_args,
    }
    thread_token = current_thread_id.set(thread_id)
    call_token = current_tool_call_id.set("eval-send-reply")
    try:
        output = save_reply_draft.invoke(reply_args)
    finally:
        current_tool_call_id.reset(call_token)
        current_thread_id.reset(thread_token)
    yield {
        "type": "trace",
        "step": "tool_end",
        "tool": "save_reply_draft",
        "output": output,
    }
    yield {"type": "token", "content": "Draft ready."}
    yield {"type": "done"}


async def _approval_events(graph_input, thread_id, callbacks, tool_output_limit):
    callbacks[0].usage_metadata["deepseek-chat"] = {
        "input_tokens": 50,
        "output_tokens": 10,
        "total_tokens": 60,
    }
    args = {
        "recipient": "finance@example.com",
        "subject": "Q3 expense report",
        "body": "The report is ready.",
    }
    yield {"type": "interrupt", "tool": "send_test_email", "args": args}
    yield {"type": "done"}


async def _failing_events(graph_input, thread_id, callbacks, tool_output_limit):
    if False:
        yield {}
    raise RuntimeError("provider timeout")


async def _hanging_events(graph_input, thread_id, callbacks, tool_output_limit):
    await asyncio.sleep(0.1)
    if False:
        yield {}


class EvalRunnerTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        suite = load_golden_suite()
        cls.tasks = {task.category: task for task in suite.tasks}

    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.output_path = Path(self.temp_dir.name) / "results" / "trials.jsonl"
        self.recorder = TrialRecorder(self.output_path)

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_provider_agent_uses_requested_model_and_request_limits(self):
        llm = object()
        agent = object()
        with (
            patch("app.evals.runner.get_llm", return_value=llm) as get_llm,
            patch("app.evals.runner.build_agent", return_value=agent) as build_agent,
        ):
            result = _build_provider_agent(
                "gemini",
                "gemini-3.5-flash-lite",
                request_timeout=12,
                max_retries=0,
            )

        self.assertIs(agent, result)
        get_llm.assert_called_once_with(
            "gemini",
            model_name="gemini-3.5-flash-lite",
            timeout=12,
            max_retries=0,
        )
        build_agent.assert_called_once_with(llm=llm)

    def test_completed_trial_records_trace_usage_cost_grade_and_isolated_state(self):
        untouched_db = Path(self.temp_dir.name) / "untouched.db"
        previous_path = os.environ.get("FEEDFLUX_DB_PATH")
        os.environ["FEEDFLUX_DB_PATH"] = str(untouched_db)
        try:
            record = asyncio.run(
                run_trial(
                    self.tasks["meeting_reply"],
                    provider="deepseek",
                    model="deepseek-chat",
                    trial_number=1,
                    run_id="run-001",
                    recorder=self.recorder,
                    event_source=_meeting_events,
                    pricing=TokenPricing(
                        input_usd_per_million=1.0,
                        output_usd_per_million=2.0,
                        cached_input_usd_per_million=0.25,
                    ),
                    request_timeout=17.5,
                    max_retries=2,
                )
            )
        finally:
            restored_path = os.environ.get("FEEDFLUX_DB_PATH")
            if previous_path is None:
                os.environ.pop("FEEDFLUX_DB_PATH", None)
            else:
                os.environ["FEEDFLUX_DB_PATH"] = previous_path

        self.assertEqual(str(untouched_db), restored_path)
        self.assertFalse(untouched_db.exists())
        self.assertEqual("run-001:meeting-reply-propose-time:1", record["trial_id"])
        self.assertEqual("feedflux-agent-eval-v2", record["suite_id"])
        self.assertEqual("completed", record["status"])
        self.assertEqual(["started", "completed"], [item["status"] for item in record["lifecycle"]])
        self.assertEqual(
            {
                "input_tokens": 100,
                "cached_input_tokens": 40,
                "output_tokens": 20,
                "total_tokens": 120,
            },
            record["usage"],
        )
        self.assertAlmostEqual(0.00011, record["estimated_cost_usd"])
        self.assertEqual(
            {"request_timeout_seconds": 17.5, "max_retries": 2},
            record["runtime"],
        )
        self.assertTrue(record["grade"]["task_success"])
        self.assertEqual(["eval-mtg-002"], record["target_email_ids"])
        self.assertEqual(1, record["final_state"]["drafts"]["active_count"])
        self.assertEqual(0, record["final_state"]["sent_actions"]["count"])
        self.assertEqual("Draft ready.", record["output"])

        saved = [json.loads(line) for line in self.output_path.read_text().splitlines()]
        self.assertEqual([record], saved)

    def test_expected_approval_interrupt_is_a_successful_interrupted_trial(self):
        record = asyncio.run(
            run_trial(
                self.tasks["high_risk_approval"],
                provider="deepseek",
                model="deepseek-chat",
                trial_number=1,
                run_id="run-approval",
                recorder=self.recorder,
                event_source=_approval_events,
            )
        )

        self.assertEqual("interrupted", record["status"])
        self.assertEqual(
            {"triggered": True, "tool": "send_test_email"},
            record["approval"],
        )
        self.assertFalse(record["final_state"]["high_risk"]["executed"])
        self.assertTrue(record["grade"]["task_success"])

    def test_provider_failure_is_recorded_instead_of_losing_the_trial(self):
        record = asyncio.run(
            run_trial(
                self.tasks["draft_creation"],
                provider="deepseek",
                model="deepseek-chat",
                trial_number=2,
                run_id="run-failed",
                recorder=self.recorder,
                event_source=_failing_events,
            )
        )

        self.assertEqual("failed", record["status"])
        self.assertEqual(
            {"type": "RuntimeError", "message": "provider timeout"},
            record["error"],
        )
        self.assertFalse(record["grade"]["task_success"])
        self.assertEqual("runner_error", record["grade"]["failures"][0]["code"])
        self.assertTrue(self.output_path.exists())

    def test_trial_deadline_records_a_hanging_provider_as_failed(self):
        record = asyncio.run(
            run_trial(
                self.tasks["draft_creation"],
                provider="deepseek",
                model="deepseek-flash",
                trial_number=1,
                run_id="run-timeout",
                recorder=self.recorder,
                event_source=_hanging_events,
                request_timeout=0.01,
            )
        )

        self.assertEqual("failed", record["status"])
        self.assertEqual("TimeoutError", record["error"]["type"])

    def test_suite_runs_requested_trial_count_with_stable_unique_ids(self):
        records = asyncio.run(
            run_suite(
                provider="deepseek",
                trials=2,
                task_ids=["high-risk-send-approval"],
                output_path=self.output_path,
                run_id="run-suite",
                event_source=_approval_events,
            )
        )

        self.assertEqual(2, len(records))
        self.assertEqual(
            {
                "run-suite:high-risk-send-approval:1",
                "run-suite:high-risk-send-approval:2",
            },
            {record["trial_id"] for record in records},
        )
        self.assertEqual(2, len(self.output_path.read_text().splitlines()))


if __name__ == "__main__":
    unittest.main()
