import argparse
import asyncio
import json
import os
import tempfile
import time
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from langchain_core.messages import AIMessage

from app.agent.stream import new_turn_input
from app.agent.usage import usage_totals
from app.evals.decision_arms import build_arm, build_arm_agent
from app.evals.decision_cases import (
    DecisionCase,
    DecisionCaseSuite,
    attention_label,
    load_case_suite,
    triage_items,
)
from app.evals.decision_metrics import ArmBuckets, buckets_from_plan, buckets_from_triage_batch, score_arm
from app.evals.recorder import TrialRecorder
from app.services.database import DatabaseService

BACKEND_DIR = Path(__file__).resolve().parents[2]
DEFAULT_RESULTS_DIR = BACKEND_DIR / "evals" / "results"
TRIAGE_PROMPT = "Triage all of my unread email and put the result on the review card."
ATTENTION_ORDER = ("needs_reply", "important", "low_signal")


def select_cases(suite: DecisionCaseSuite, size: int) -> list[DecisionCase]:
    """Deterministic, attention-balanced subset: a small N must not accidentally
    be all noise, or recall would have nothing to measure."""
    by_label: dict[str, list[DecisionCase]] = {label: [] for label in ATTENTION_ORDER}
    for case in sorted(suite.cases, key=lambda c: c.case_id):
        by_label[attention_label(case)].append(case)

    picked: list[DecisionCase] = []
    while len(picked) < size and any(by_label.values()):
        for label in ATTENTION_ORDER:
            if by_label[label] and len(picked) < size:
                picked.append(by_label[label].pop(0))
    return sorted(picked, key=lambda c: c.case_id)


def _received_timestamp(value: Any) -> int:
    if isinstance(value, (int, float)):
        return int(value)
    return int(datetime.fromisoformat(str(value).replace("Z", "+00:00")).timestamp())


def load_cases_into(db: DatabaseService, cases: list[DecisionCase], suite: DecisionCaseSuite) -> None:
    items = triage_items(suite)
    for case in cases:
        item = items[case.case_id]
        db.insert_email(
            {
                "id": case.case_id,
                "subject": item.subject,
                "sender_name": item.sender,
                "sender_email": item.sender_email,
                "received_datetime": _received_timestamp(item.received or 0),
                "body_preview": item.body_preview,
                "body_content": item.body_preview,
                "body_html": None,
                "is_read": False,
                "has_attachments": False,
                "attachments": None,
            }
        )


@contextmanager
def case_database(cases: list[DecisionCase], suite: DecisionCaseSuite):
    with tempfile.TemporaryDirectory() as directory:
        path = str(Path(directory) / "decision-ab.db")
        previous = os.environ.get("FEEDFLUX_DB_PATH")
        os.environ["FEEDFLUX_DB_PATH"] = path
        try:
            load_cases_into(DatabaseService(path), cases, suite)
            yield path
        finally:
            if previous is None:
                os.environ.pop("FEEDFLUX_DB_PATH", None)
            else:
                os.environ["FEEDFLUX_DB_PATH"] = previous


def agent_usage(messages: list[Any]) -> dict[str, int]:
    """Read the agent's own token use off its replies. Taken from the messages
    rather than a callback so that a provider call made inside a tool stays
    separate — the whole point is to see where the tokens went."""
    per_message = {}
    for index, message in enumerate(messages):
        usage = getattr(message, "usage_metadata", None)
        if usage:
            per_message[f"message-{index}"] = usage
    return usage_totals(per_message)


def _baseline_args(messages: list[Any]) -> dict | None:
    for message in messages:
        if not isinstance(message, AIMessage):
            continue
        for call in message.tool_calls or []:
            if call["name"] == "apply_triage_batch":
                return call["args"]
    return None


async def run_arm(
    arm_id: str,
    cases: list[DecisionCase],
    suite: DecisionCaseSuite,
    llm_factory: Callable[[str, list[DecisionCase]], Any],
    *,
    provider=None,
    mode: str = "dry_run",
    trial: int = 1,
) -> dict:
    arm = build_arm(arm_id, provider=provider)
    case_ids = [case.case_id for case in cases]

    with case_database(cases, suite):
        agent = build_arm_agent(arm, llm_factory(arm_id, cases))
        started = time.monotonic()
        state = await agent.ainvoke(
            new_turn_input(TRIAGE_PROMPT),
            config={"configurable": {"thread_id": f"decision-ab-{arm_id}-{len(cases)}"}},
        )
        latency_ms = round((time.monotonic() - started) * 1000, 2)

    if arm.result.plans:
        plan = arm.result.plans[-1]
        buckets = buckets_from_plan(plan)
        provider_stats = plan.provider.model_dump()
    else:
        args = _baseline_args(state.get("messages", []))
        buckets = (
            buckets_from_triage_batch(args, case_ids)
            if args is not None
            else ArmBuckets(untouched=set(case_ids))
        )
        provider_stats = None

    return {
        "mode": mode,
        "arm": arm_id,
        "trial": trial,
        "provider": arm.provider_name,
        "size": len(cases),
        "case_ids": case_ids,
        "latency_ms": latency_ms,
        "agent_usage": agent_usage(state.get("messages", [])),
        "provider_stats": provider_stats,
        "scores": score_arm(buckets, cases),
        "buckets": {
            "needs_reply": sorted(buckets.needs_reply),
            "important": sorted(buckets.important),
            "dismissed": sorted(buckets.dismissed),
            "untouched": sorted(buckets.untouched),
            "destructive": buckets.destructive,
        },
    }


class ScriptedTriageLLM:
    """Deterministic stand-in for a chat model, so the harness can be exercised
    without spending anything. It reports output tokens proportional to the tool
    arguments it had to generate — the per-item cost the real baseline pays — which
    makes the accounting path testable but is a model of the mechanism, not a
    measurement of it."""

    def __init__(
        self,
        arm_id: str,
        cases: list[DecisionCase],
        *,
        classify: Callable[[DecisionCase], str] = lambda case: "mark_read",
        tokens_per_item: int = 109,
        base_output_tokens: int = 40,
        input_tokens: int = 15000,
    ):
        self.arm_id = arm_id
        self.cases = cases
        self.classify = classify
        self.tokens_per_item = tokens_per_item
        self.base_output_tokens = base_output_tokens
        self.input_tokens = input_tokens
        self.turns = 0

    def bind_tools(self, tools, **kwargs):
        self.tool_names = {t.name for t in tools}
        return self

    def _usage(self, output_tokens: int) -> dict:
        return {
            "input_tokens": self.input_tokens,
            "output_tokens": output_tokens,
            "total_tokens": self.input_tokens + output_tokens,
        }

    async def ainvoke(self, messages, **kwargs):
        self.turns += 1
        if self.turns > 1:
            return AIMessage(content="Done — the plan is on the card.",
                             usage_metadata=self._usage(self.base_output_tokens))
        if "triage_unread" in self.tool_names:
            return AIMessage(
                content="",
                tool_calls=[{"name": "triage_unread", "args": {"limit": 50, "language": "en"}, "id": "c1"}],
                usage_metadata=self._usage(self.base_output_tokens),
            )
        args = {
            "actions": [
                {"email_id": c.case_id, "action": self.classify(c), "reason": "scripted"}
                for c in self.cases
            ],
            "needs_reply": [],
        }
        return AIMessage(
            content="",
            tool_calls=[{"name": "apply_triage_batch", "args": args, "id": "c1"}],
            usage_metadata=self._usage(
                self.base_output_tokens + self.tokens_per_item * len(self.cases)
            ),
        )


def scripted_llm_factory(**kwargs) -> Callable[[str, list[DecisionCase]], Any]:
    return lambda arm_id, cases: ScriptedTriageLLM(arm_id, cases, **kwargs)


def dry_run_provider(cases: list[DecisionCase]):
    """Arbitrary but deterministic answers that reach all four buckets. Derived
    from the case id, never from the labels, so a dry run exercises the plumbing
    without producing anything that looks like a quality result."""
    from app.decisions.contract import ReasonCode
    from app.decisions.fake import FakeDecisionProvider

    scripted, importance = {}, {}
    for index, case in enumerate(sorted(cases, key=lambda c: c.case_id)):
        bucket = index % 4
        if bucket == 0:
            scripted[case.case_id] = ("needs_reply", ReasonCode.DIRECT_QUESTION, 0.9)
        elif bucket == 1:
            scripted[case.case_id] = ("mark_read", ReasonCode.RECEIPT, 0.9)
            importance[case.case_id] = (True, 0.9)
        elif bucket == 2:
            scripted[case.case_id] = ("mark_read", ReasonCode.NEWSLETTER, 0.9)
            importance[case.case_id] = (False, 0.9)
        else:
            scripted[case.case_id] = ("mark_read", ReasonCode.OTHER, 0.2)
    return FakeDecisionProvider(scripted, importance=importance)


async def run_suite(
    sizes: list[int],
    arm_ids: list[str],
    llm_factory,
    *,
    provider_factory=None,
    mode: str = "dry_run",
    suite: DecisionCaseSuite | None = None,
    trials: int = 1,
) -> list[dict]:
    suite = suite or load_case_suite()
    records = []
    for size in sizes:
        cases = select_cases(suite, size)
        for arm_id in arm_ids:
            for trial in range(1, trials + 1):
                provider = (
                    provider_factory(arm_id)
                    if provider_factory and arm_id != "baseline"
                    else None
                )
                records.append(
                    await run_arm(
                        arm_id, cases, suite, llm_factory,
                        provider=provider, mode=mode, trial=trial,
                    )
                )
    return records


def aggregate(records: list[dict]) -> list[dict]:
    """Collapse repeated trials so run-to-run variation is visible rather than
    hidden behind a single number."""
    import statistics

    grouped: dict[tuple, list[dict]] = {}
    for record in records:
        grouped.setdefault((record["arm"], record["provider"], record["size"]), []).append(record)

    rows = []
    for (arm, provider, size), runs in sorted(grouped.items(), key=lambda kv: (kv[0][2], kv[0][0])):
        latencies = sorted(r["latency_ms"] for r in runs)
        recalls = [r["scores"]["overall"]["attention_recall"] for r in runs]
        recalls = [r for r in recalls if r is not None]
        rows.append({
            "arm": arm,
            "provider": provider,
            "size": size,
            "trials": len(runs),
            "latency_ms_median": statistics.median(latencies),
            "latency_ms_min": latencies[0],
            "latency_ms_max": latencies[-1],
            "agent_output_tokens_median": statistics.median(
                r["agent_usage"]["output_tokens"] for r in runs
            ),
            "agent_total_tokens_median": statistics.median(
                r["agent_usage"]["total_tokens"] for r in runs
            ),
            "provider_input_tokens_median": statistics.median(
                (r["provider_stats"] or {}).get("input_tokens", 0) for r in runs
            ),
            "provider_requests_median": statistics.median(
                (r["provider_stats"] or {}).get("requests", 0) for r in runs
            ),
            "attention_recall_min": min(recalls) if recalls else None,
            "attention_recall_max": max(recalls) if recalls else None,
            "buried_max": max(r["scores"]["overall"]["buried_count"] for r in runs),
            "forbidden_violations_max": max(
                len(r["scores"]["overall"]["forbidden_action_violations"]) for r in runs
            ),
            "review_effort_median": statistics.median(
                r["scores"]["overall"]["review_effort"] for r in runs
            ),
            "one_click_median": statistics.median(
                r["scores"]["overall"]["one_click_dismissals"] for r in runs
            ),
        })
    return rows


def summarise(records: list[dict]) -> dict:
    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "modes": sorted({r["mode"] for r in records}),
        "aggregated": aggregate(records),
        "runs": [
            {
                "arm": r["arm"],
                "provider": r["provider"],
                "size": r["size"],
                "trial": r["trial"],
                "latency_ms": r["latency_ms"],
                "agent_output_tokens": r["agent_usage"]["output_tokens"],
                "agent_total_tokens": r["agent_usage"]["total_tokens"],
                "attention_recall": r["scores"]["overall"]["attention_recall"],
                "buried": r["scores"]["overall"]["buried_count"],
                "forbidden_violations": len(r["scores"]["overall"]["forbidden_action_violations"]),
                "review_effort": r["scores"]["overall"]["review_effort"],
                "one_click_dismissals": r["scores"]["overall"]["one_click_dismissals"],
            }
            for r in records
        ],
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Batch-triage A/B across decision arms.")
    parser.add_argument("--sizes", type=int, nargs="+", default=[6, 20, 38])
    parser.add_argument("--arms", nargs="+", default=["baseline", "provider_tool"])
    parser.add_argument("--trials", type=int, default=1)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_RESULTS_DIR)
    parser.add_argument(
        "--live",
        action="store_true",
        help="Use the configured chat model and decision provider instead of the scripted stand-ins.",
    )
    args = parser.parse_args()

    if args.live:
        from app.agent.llm import get_llm
        from app.agent.triage_tools import build_provider

        llm_factory = lambda arm_id, cases: get_llm(temperature=0)
        provider_factory = None
        mode = "live"
    else:
        llm_factory = scripted_llm_factory()
        mode = "dry_run"
        suite_cases = load_case_suite().cases
        provider_factory = lambda arm_id: dry_run_provider(suite_cases)

    records = asyncio.run(
        run_suite(
            args.sizes, args.arms, llm_factory,
            provider_factory=provider_factory, mode=mode, trials=args.trials,
        )
    )
    args.output_dir.mkdir(parents=True, exist_ok=True)
    recorder = TrialRecorder(args.output_dir / f"decision-ab-{mode}.jsonl")
    for record in records:
        recorder.append(record)
    summary = summarise(records)
    (args.output_dir / f"decision-ab-{mode}-summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
