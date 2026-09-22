import argparse
import json
import subprocess
import sys
import time
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Sequence

from app.evals.recorder import TrialRecorder


SCHEMA_VERSION = 1
SUITE_ID = "feedflux-agent-fault-injection-v1"
BACKEND_DIR = Path(__file__).resolve().parents[2]
DEFAULT_RESULTS_DIR = BACKEND_DIR / "evals" / "results"
RAW_RESULTS_NAME = "fault-injection-results.jsonl"
SUMMARY_NAME = "fault-injection-summary.json"


@dataclass(frozen=True)
class FaultCase:
    id: str
    category: str
    tests: tuple[str, ...]


FAULT_CASES = (
    FaultCase(
        id="provider-timeout-429-recovers",
        category="provider_transient_failure",
        tests=(
            "tests/test_provider_retry.py::ProviderRetryTest::test_transient_provider_failures_recover_within_attempt_budget",
        ),
    ),
    FaultCase(
        id="provider-5xx-stops-at-budget",
        category="provider_transient_failure",
        tests=(
            "tests/test_provider_retry.py::ProviderRetryTest::test_transient_provider_failure_stops_at_attempt_budget",
        ),
    ),
    FaultCase(
        id="provider-validation-is-not-retried",
        category="provider_permanent_failure",
        tests=(
            "tests/test_provider_retry.py::ProviderRetryTest::test_permanent_validation_failure_is_not_retried",
        ),
    ),
    FaultCase(
        id="tool-permanent-failure-is-not-retried",
        category="tool_permanent_failure",
        tests=(
            "tests/test_tool_failure.py::ToolFailureTest::test_permanent_tool_failure_is_not_retried_and_fails_run",
        ),
    ),
    FaultCase(
        id="hitl-resumes-after-checkpoint-reopen",
        category="checkpoint_recovery",
        tests=(
            "tests/test_agent_checkpoint.py::AgentCheckpointTest::test_interrupt_resumes_after_runtime_reopens_same_sqlite_database",
        ),
    ),
    FaultCase(
        id="idempotency-replay-creates-one-draft",
        category="idempotency",
        tests=(
            "tests/test_draft_agent.py::DraftAgentTest::test_save_reply_draft_replays_same_tool_call_without_duplicate_draft",
        ),
    ),
)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _new_run_id() -> str:
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    return f"{timestamp}-{uuid.uuid4().hex[:8]}"


def _run_case(case: FaultCase, run_id: str, runner) -> dict:
    command = [sys.executable, "-m", "pytest", *case.tests, "-q"]
    started_at = _now()
    started_clock = time.perf_counter()
    result = runner(
        command,
        cwd=BACKEND_DIR,
        capture_output=True,
        text=True,
        check=False,
    )
    output = "\n".join(part for part in (result.stdout, result.stderr) if part).strip()
    status = "passed" if result.returncode == 0 else "failed"
    return {
        "schema_version": SCHEMA_VERSION,
        "suite_id": SUITE_ID,
        "run_id": run_id,
        "case_id": case.id,
        "category": case.category,
        "provider": "fixture",
        "status": status,
        "lifecycle": [
            {"status": "started", "at": started_at},
            {"status": status, "at": _now()},
        ],
        "latency_ms": round((time.perf_counter() - started_clock) * 1000, 3),
        "outcome": {
            "pytest_return_code": result.returncode,
            "tests": list(case.tests),
        },
        "trace": [
            {
                "type": "pytest",
                "tests": list(case.tests),
                "output": output,
            }
        ],
        "error": (
            None
            if result.returncode == 0
            else {"type": "pytest_failure", "message": output}
        ),
    }


def run_fault_suite(
    *,
    output_dir: str | Path = DEFAULT_RESULTS_DIR,
    cases: Sequence[FaultCase] = FAULT_CASES,
    run_id: str | None = None,
    runner=subprocess.run,
) -> dict:
    destination = Path(output_dir)
    destination.mkdir(parents=True, exist_ok=True)
    raw_path = destination / RAW_RESULTS_NAME
    summary_path = destination / SUMMARY_NAME
    raw_path.write_text("", encoding="utf-8")

    actual_run_id = run_id or _new_run_id()
    recorder = TrialRecorder(raw_path)
    records = []
    for case in cases:
        record = _run_case(case, actual_run_id, runner)
        recorder.append(record)
        records.append(record)

    passed = sum(record["status"] == "passed" for record in records)
    summary = {
        "schema_version": SCHEMA_VERSION,
        "source_schema_version": SCHEMA_VERSION,
        "suite_id": SUITE_ID,
        "run_id": actual_run_id,
        "status": "passed" if passed == len(records) else "failed",
        "total_cases": len(records),
        "passed": passed,
        "failed": len(records) - passed,
        "raw_results": RAW_RESULTS_NAME,
        "cases": [
            {
                "case_id": record["case_id"],
                "category": record["category"],
                "status": record["status"],
            }
            for record in records
        ],
    }
    summary_path.write_text(
        json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return summary


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Run the deterministic FeedFlux fault-injection suite"
    )
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_RESULTS_DIR)
    args = parser.parse_args()

    summary = run_fault_suite(output_dir=args.output_dir)
    print(json.dumps(summary, ensure_ascii=False, sort_keys=True))
    return 0 if summary["status"] == "passed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
