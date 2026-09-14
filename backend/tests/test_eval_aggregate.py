import json
import tempfile
import unittest
from pathlib import Path

from app.evals.aggregate import aggregate_files, aggregate_records


def _record(
    trial_id,
    *,
    provider="deepseek",
    model="deepseek-chat",
    task_id="meeting-reply-propose-time",
    category="meeting_reply",
    trial_number=1,
    success=True,
    status="completed",
    target=True,
    approval=None,
    latency=100,
    input_tokens=100,
    output_tokens=20,
    cost=0.001,
    failure_codes=(),
    error=None,
):
    return {
        "schema_version": 1,
        "suite_id": "feedflux-agent-eval-v1",
        "run_id": trial_id.split(":", 1)[0],
        "trial_id": trial_id,
        "provider": provider,
        "model": model,
        "task_id": task_id,
        "category": category,
        "trial_number": trial_number,
        "status": status,
        "latency_ms": latency,
        "usage": {
            "input_tokens": input_tokens,
            "output_tokens": output_tokens,
            "total_tokens": input_tokens + output_tokens,
        },
        "estimated_cost_usd": cost,
        "grade": {
            "task_success": success,
            "target_email_identification": target,
            "tool_selection": success,
            "approval_trigger": approval,
            "final_business_state": success,
            "failures": [
                {"code": code, "detail": f"{code} detail"}
                for code in failure_codes
            ],
        },
        "error": error,
    }


class EvalAggregateTest(unittest.TestCase):
    def test_aggregates_quality_latency_tokens_cost_and_trial_variance(self):
        records = [
            _record("run-a:meeting:1", trial_number=1, latency=100, cost=0.001),
            _record(
                "run-a:meeting:2",
                trial_number=2,
                success=False,
                target=False,
                latency=200,
                input_tokens=120,
                output_tokens=30,
                cost=None,
                failure_codes=("tool_sequence", "final_state"),
            ),
            _record(
                "run-a:meeting:3",
                trial_number=3,
                latency=300,
                input_tokens=110,
                output_tokens=25,
                cost=0.003,
            ),
            _record(
                "run-b:approval:1",
                provider="glm",
                model="glm-4.7",
                task_id="high-risk-send-approval",
                category="high_risk_approval",
                status="interrupted",
                target=None,
                approval=True,
                latency=400,
                input_tokens=50,
                output_tokens=10,
                cost=0.001,
            ),
        ]

        summary = aggregate_records(list(reversed(records)))

        self.assertEqual("feedflux-agent-eval-v1", summary["suite_id"])
        self.assertEqual(["run-a", "run-b"], summary["source_runs"])
        self.assertEqual(4, summary["overall"]["trials"])
        self.assertEqual(
            {"applicable": 4, "passed": 3, "rate": 0.75},
            summary["overall"]["metrics"]["task_success"],
        )
        self.assertEqual(
            {"applicable": 3, "passed": 2, "rate": 0.6667},
            summary["overall"]["metrics"]["target_email_identification"],
        )
        self.assertEqual(
            {"applicable": 1, "passed": 1, "rate": 1.0},
            summary["overall"]["metrics"]["approval_trigger"],
        )
        self.assertEqual(
            {"count": 4, "mean": 250.0, "p50": 250.0, "p95": 400.0},
            summary["overall"]["latency_ms"],
        )
        self.assertEqual(
            {
                "input_tokens": 380,
                "output_tokens": 85,
                "total_tokens": 465,
                "mean_total_per_trial": 116.25,
            },
            summary["overall"]["usage"],
        )
        self.assertEqual(
            {"known_trials": 3, "total_usd": 0.005, "mean_usd": 0.00166667},
            summary["overall"]["cost"],
        )
        self.assertEqual(
            {"completed": 3, "interrupted": 1},
            summary["overall"]["status_counts"],
        )

        self.assertEqual(
            [("deepseek", "deepseek-chat"), ("glm", "glm-4.7")],
            [(item["provider"], item["model"]) for item in summary["providers"]],
        )
        deepseek_task = summary["providers"][0]["tasks"][0]
        self.assertEqual(3, deepseek_task["trials"])
        self.assertEqual(0.6667, deepseek_task["success_rate"])
        self.assertEqual(
            [
                {"trial_id": "run-a:meeting:1", "success": True},
                {"trial_id": "run-a:meeting:2", "success": False},
                {"trial_id": "run-a:meeting:3", "success": True},
            ],
            deepseek_task["outcomes"],
        )

    def test_classifies_failures_and_keeps_traceable_samples(self):
        records = [
            _record(
                "run-a:planning:1",
                success=False,
                failure_codes=("tool_sequence", "target_email_identification"),
            ),
            _record(
                "run-a:tool:1",
                success=False,
                failure_codes=("forbidden_tool", "approval_trigger", "final_state"),
            ),
            _record(
                "run-a:environment:1",
                success=False,
                status="failed",
                failure_codes=("runner_error",),
                error={"type": "RuntimeError", "message": "timeout"},
            ),
            _record(
                "run-a:output:1",
                success=False,
                failure_codes=("model_output",),
            ),
        ]

        summary = aggregate_records(records)

        self.assertEqual(
            {
                "environment_data": 1,
                "model_output": 1,
                "planning": 1,
                "tool": 1,
            },
            summary["failure_categories"],
        )
        self.assertEqual(4, len(summary["failure_samples"]))
        self.assertEqual(
            {
                "trial_id": "run-a:environment:1",
                "provider": "deepseek",
                "model": "deepseek-chat",
                "task_id": "meeting-reply-propose-time",
                "categories": ["environment_data"],
                "failure_codes": ["runner_error"],
                "error": {"type": "RuntimeError", "message": "timeout"},
            },
            summary["failure_samples"][0],
        )

    def test_rejects_duplicate_trials_and_mixed_suites(self):
        record = _record("run-a:meeting:1")
        with self.assertRaisesRegex(ValueError, "Duplicate trial_id"):
            aggregate_records([record, dict(record)])

        other_suite = _record("run-b:meeting:1")
        other_suite["suite_id"] = "another-suite"
        with self.assertRaisesRegex(ValueError, "same suite"):
            aggregate_records([record, other_suite])

    def test_reads_jsonl_and_writes_deterministic_summary_json(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            first_input = root / "b.jsonl"
            second_input = root / "a.jsonl"
            output = root / "summary.json"
            first_input.write_text(
                json.dumps(_record("run-b:meeting:1", provider="glm", model="glm-4.7"))
                + "\n"
            )
            second_input.write_text(json.dumps(_record("run-a:meeting:1")) + "\n")

            returned = aggregate_files(
                [first_input, second_input],
                output_path=output,
            )

            saved = json.loads(output.read_text())
            self.assertEqual(returned, saved)
            self.assertEqual(["run-a", "run-b"], saved["source_runs"])
            self.assertTrue(output.read_text().endswith("\n"))


if __name__ == "__main__":
    unittest.main()
