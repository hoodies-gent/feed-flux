import argparse
import json
import math
import statistics
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any


METRICS = (
    "task_success",
    "target_email_identification",
    "tool_selection",
    "approval_trigger",
    "final_business_state",
)
FAILURE_CATEGORY_BY_CODE = {
    "runner_error": "environment_data",
    "tool_sequence": "planning",
    "target_email_identification": "planning",
    "forbidden_tool": "tool",
    "approval_trigger": "tool",
    "final_state": "tool",
    "model_output": "model_output",
}


def _rate(values: list[bool | None]) -> dict[str, int | float | None]:
    applicable = [value for value in values if value is not None]
    passed = sum(value is True for value in applicable)
    return {
        "applicable": len(applicable),
        "passed": passed,
        "rate": round(passed / len(applicable), 4) if applicable else None,
    }


def _latency(records: list[dict[str, Any]]) -> dict[str, int | float | None]:
    values = sorted(float(record["latency_ms"]) for record in records)
    if not values:
        return {"count": 0, "mean": None, "p50": None, "p95": None}
    p95_index = math.ceil(len(values) * 0.95) - 1
    return {
        "count": len(values),
        "mean": round(statistics.mean(values), 3),
        "p50": round(statistics.median(values), 3),
        "p95": round(values[p95_index], 3),
    }


def _usage(records: list[dict[str, Any]]) -> dict[str, int | float | None]:
    input_tokens = sum(int(record["usage"].get("input_tokens", 0)) for record in records)
    output_tokens = sum(int(record["usage"].get("output_tokens", 0)) for record in records)
    total_tokens = sum(int(record["usage"].get("total_tokens", 0)) for record in records)
    return {
        "input_tokens": input_tokens,
        "output_tokens": output_tokens,
        "total_tokens": total_tokens,
        "mean_total_per_trial": (
            round(total_tokens / len(records), 2) if records else None
        ),
    }


def _cost(records: list[dict[str, Any]]) -> dict[str, int | float | None]:
    values = [
        float(record["estimated_cost_usd"])
        for record in records
        if record.get("estimated_cost_usd") is not None
    ]
    total = sum(values)
    return {
        "known_trials": len(values),
        "total_usd": round(total, 8),
        "mean_usd": round(total / len(values), 8) if values else None,
    }


def _statistics(records: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "trials": len(records),
        "metrics": {
            metric: _rate([record["grade"].get(metric) for record in records])
            for metric in METRICS
        },
        "latency_ms": _latency(records),
        "usage": _usage(records),
        "cost": _cost(records),
        "status_counts": dict(sorted(Counter(record["status"] for record in records).items())),
    }


def _task_summaries(records: list[dict[str, Any]]) -> list[dict[str, Any]]:
    groups: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    for record in records:
        groups[(record["task_id"], record["category"])].append(record)

    summaries = []
    for (task_id, category), task_records in sorted(groups.items()):
        ordered = sorted(
            task_records,
            key=lambda record: (
                record["run_id"],
                record["trial_number"],
                record["trial_id"],
            ),
        )
        stats = _statistics(ordered)
        summaries.append(
            {
                "task_id": task_id,
                "category": category,
                "trials": stats["trials"],
                "passed": stats["metrics"]["task_success"]["passed"],
                "success_rate": stats["metrics"]["task_success"]["rate"],
                "outcomes": [
                    {
                        "trial_id": record["trial_id"],
                        "success": record["grade"]["task_success"],
                    }
                    for record in ordered
                ],
                "metrics": stats["metrics"],
                "latency_ms": stats["latency_ms"],
                "usage": stats["usage"],
                "cost": stats["cost"],
                "status_counts": stats["status_counts"],
            }
        )
    return summaries


def _failure_details(
    records: list[dict[str, Any]],
) -> tuple[dict[str, int], list[dict[str, Any]]]:
    category_counts: Counter[str] = Counter()
    samples = []
    for record in sorted(records, key=lambda item: item["trial_id"]):
        if record["grade"]["task_success"]:
            continue
        codes = sorted(
            {
                failure["code"]
                for failure in record["grade"].get("failures", [])
            }
        )
        categories = {
            FAILURE_CATEGORY_BY_CODE[code]
            for code in codes
            if code in FAILURE_CATEGORY_BY_CODE
        }
        if record.get("error"):
            categories.add("environment_data")
        if not categories:
            categories.add("model_output")
        for category in categories:
            category_counts[category] += 1
        samples.append(
            {
                "trial_id": record["trial_id"],
                "provider": record["provider"],
                "model": record["model"],
                "task_id": record["task_id"],
                "categories": sorted(categories),
                "failure_codes": codes,
                "error": record.get("error"),
            }
        )
    return dict(sorted(category_counts.items())), samples


def _validate_records(records: list[dict[str, Any]]) -> tuple[str, int]:
    if not records:
        raise ValueError("No trial records supplied")
    trial_ids = [record.get("trial_id") for record in records]
    duplicates = sorted(
        trial_id
        for trial_id, count in Counter(trial_ids).items()
        if count > 1
    )
    if duplicates:
        raise ValueError(f"Duplicate trial_id values: {duplicates}")
    suites = {record.get("suite_id") for record in records}
    if len(suites) != 1:
        raise ValueError("All records must belong to the same suite")
    schema_versions = {record.get("schema_version") for record in records}
    if len(schema_versions) != 1:
        raise ValueError("All records must use the same schema version")
    return suites.pop(), schema_versions.pop()


def aggregate_records(records: list[dict[str, Any]]) -> dict[str, Any]:
    suite_id, source_schema_version = _validate_records(records)
    records = sorted(records, key=lambda record: record["trial_id"])
    failure_categories, failure_samples = _failure_details(records)

    provider_groups: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    for record in records:
        provider_groups[(record["provider"], record["model"])].append(record)

    providers = []
    for (provider, model), provider_records in sorted(provider_groups.items()):
        providers.append(
            {
                "provider": provider,
                "model": model,
                **_statistics(provider_records),
                "tasks": _task_summaries(provider_records),
            }
        )

    return {
        "schema_version": 1,
        "source_schema_version": source_schema_version,
        "suite_id": suite_id,
        "source_runs": sorted({record["run_id"] for record in records}),
        "overall": _statistics(records),
        "providers": providers,
        "failure_categories": failure_categories,
        "failure_samples": failure_samples,
    }


def aggregate_files(
    paths: list[str | Path],
    *,
    output_path: str | Path,
) -> dict[str, Any]:
    records = []
    for path in paths:
        with Path(path).open(encoding="utf-8") as input_file:
            records.extend(
                json.loads(line)
                for line in input_file
                if line.strip()
            )
    summary = aggregate_records(records)
    destination = Path(output_path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(
        json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return summary


def main() -> int:
    parser = argparse.ArgumentParser(description="Aggregate FeedFlux eval trial JSONL")
    parser.add_argument("inputs", nargs="+", type=Path)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()

    summary = aggregate_files(args.inputs, output_path=args.output)
    print(
        json.dumps(
            {
                "suite_id": summary["suite_id"],
                "runs": len(summary["source_runs"]),
                "trials": summary["overall"]["trials"],
                "providers": len(summary["providers"]),
                "output": str(args.output),
            }
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
