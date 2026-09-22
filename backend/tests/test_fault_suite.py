import json
import subprocess
import tempfile
import unittest
from pathlib import Path


def _fault_suite_module():
    try:
        from app.agent import fault_suite
    except ImportError as exc:
        raise AssertionError("fault-injection suite is not implemented") from exc
    return fault_suite


class FaultSuiteTest(unittest.TestCase):
    def test_run_suite_writes_versioned_raw_results_and_summary(self):
        fault_suite = _fault_suite_module()
        cases = (
            fault_suite.FaultCase(
                id="passing-case",
                category="provider_failure",
                tests=("tests/test_example.py::Example::test_pass",),
            ),
            fault_suite.FaultCase(
                id="failing-case",
                category="tool_failure",
                tests=("tests/test_example.py::Example::test_fail",),
            ),
        )

        def run_test(command, **kwargs):
            failed = any("test_fail" in argument for argument in command)
            return subprocess.CompletedProcess(
                command,
                1 if failed else 0,
                stdout="failed output" if failed else "passed output",
                stderr="",
            )

        with tempfile.TemporaryDirectory() as temp_dir:
            summary = fault_suite.run_fault_suite(
                output_dir=Path(temp_dir),
                cases=cases,
                run_id="fault-run-1",
                runner=run_test,
            )
            raw_path = Path(temp_dir) / "fault-injection-results.jsonl"
            summary_path = Path(temp_dir) / "fault-injection-summary.json"
            records = [
                json.loads(line)
                for line in raw_path.read_text(encoding="utf-8").splitlines()
            ]
            persisted_summary = json.loads(summary_path.read_text(encoding="utf-8"))

        self.assertEqual(1, records[0]["schema_version"])
        self.assertEqual("feedflux-agent-fault-injection-v1", records[0]["suite_id"])
        self.assertEqual("fault-run-1", records[0]["run_id"])
        self.assertEqual("passed", records[0]["status"])
        self.assertEqual("failed", records[1]["status"])
        self.assertEqual(1, records[1]["outcome"]["pytest_return_code"])
        self.assertEqual("failed output", records[1]["trace"][0]["output"])
        self.assertEqual("pytest_failure", records[1]["error"]["type"])
        self.assertEqual(summary, persisted_summary)
        self.assertEqual(2, summary["total_cases"])
        self.assertEqual(1, summary["passed"])
        self.assertEqual(1, summary["failed"])
        self.assertEqual("failed", summary["status"])
        self.assertEqual(
            ["passing-case", "failing-case"],
            [case["case_id"] for case in summary["cases"]],
        )


if __name__ == "__main__":
    unittest.main()
