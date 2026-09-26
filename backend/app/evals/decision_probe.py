"""Provider-only probe: does the provider's importance answer ever clear the
dismissal gate, and is it stable when asked twice?

No agent, no prompt, no bucket projection. Those three are what made the arm
comparison hard to read, and none of them are needed for the question that
decides whether the comparison is worth paying for: if the importance answer
never reaches the gate, coverage is zero and nothing measured further up
changes that. This also reports the two numbers the arm metrics cannot — the
provider's four-way action landing on an action the labels forbid, and
attention-worthy mail it would dismiss — because here the raw answer is still
in hand instead of projected into buckets."""

from __future__ import annotations

import argparse
import json
import math
import statistics
import time
from collections import defaultdict
from dataclasses import asdict, dataclass, replace
from pathlib import Path

from app.agent.triage_tools import _dismissable
from app.decisions.contract import DecisionProvider, TriageDecision, TriageItem
from app.decisions.policy import GatePolicy, apply_gate
from app.decisions.settings import DecisionSettings, decision_settings
from app.evals.decision_cases import (
    DecisionCase,
    DecisionCaseSuite,
    attention_label,
    is_important,
    load_case_suite,
    triage_items,
)
from app.evals.recorder import TrialRecorder

BACKEND_DIR = Path(__file__).resolve().parents[2]
DEFAULT_RESULTS_DIR = BACKEND_DIR / "evals" / "results"
BIN_WIDTH = 0.1


@dataclass
class Observation:
    """One provider answer for one email. Typed fields and the case id only —
    no subject, sender or body reaches the results file."""

    provider: str
    model: str | None
    rep: int
    item_id: str
    status: str
    action: str | None = None
    confidence: float | None = None
    important: bool | None = None
    importance_confidence: float | None = None
    error_category: str | None = None

    def as_decision(self) -> TriageDecision:
        """Rebuild the decision the gate would see. These are every field the
        gate reads, so asking the shipped gate about it cannot drift from what
        production would do with the same answer."""
        return TriageDecision(
            item_id=self.item_id,
            status=self.status,  # type: ignore[arg-type]
            action=self.action,  # type: ignore[arg-type]
            confidence=self.confidence,
            important=self.important,
            importance_confidence=self.importance_confidence,
        )


def probe_once(
    provider: DecisionProvider, items: list[TriageItem], rep: int
) -> tuple[list[Observation], dict]:
    """One pass of the whole case set through one provider."""
    started = time.monotonic()
    batch = provider.decide_triage(items)
    wall_ms = round((time.monotonic() - started) * 1000, 2)
    record = {
        "provider": batch.provider,
        "model": batch.model,
        "rep": rep,
        "requests": batch.requests,
        "failed_requests": batch.failed_requests,
        "provider_latency_ms": batch.latency_ms,
        "wall_ms": wall_ms,
        "input_tokens": batch.usage.input_tokens,
        "output_tokens": batch.usage.output_tokens,
        # A provider that reports nothing must not read as free.
        "usage_reported": bool(batch.usage.input_tokens or batch.usage.output_tokens),
    }
    observations = [
        Observation(
            provider=batch.provider,
            model=batch.model,
            rep=rep,
            item_id=decision.item_id,
            status=decision.status,
            action=decision.action,
            confidence=decision.confidence,
            important=decision.important,
            importance_confidence=decision.importance_confidence,
            error_category=decision.error_category,
        )
        for decision in batch.decisions
    ]
    return observations, record


def probe_provider(
    provider: DecisionProvider, items: list[TriageItem], *, reps: int = 1
) -> tuple[list[Observation], list[dict]]:
    observations: list[Observation] = []
    batches: list[dict] = []
    for rep in range(1, reps + 1):
        rep_observations, record = probe_once(provider, items, rep)
        observations.extend(rep_observations)
        batches.append(record)
    return observations, batches


def _bin(value: float) -> str:
    index = min(int(value / BIN_WIDTH), int(1 / BIN_WIDTH) - 1)
    return f"{index * BIN_WIDTH:.1f}-{(index + 1) * BIN_WIDTH:.1f}"


def confidence_histogram(observations: list[Observation], suite: DecisionCaseSuite) -> dict:
    """Where the importance confidence actually lands, split by gold label. The
    gate is a cut on this axis, so its shape decides coverage before any metric."""
    gold = {case.case_id: attention_label(case) for case in suite.cases}
    histogram: dict[str, dict[str, int]] = defaultdict(lambda: defaultdict(int))
    for observation in observations:
        if observation.importance_confidence is None:
            histogram[gold.get(observation.item_id, "unknown")]["missing"] += 1
            continue
        histogram[gold.get(observation.item_id, "unknown")][
            _bin(observation.importance_confidence)
        ] += 1
    return {label: dict(sorted(bins.items())) for label, bins in sorted(histogram.items())}


def gate_report(
    observations: list[Observation],
    suite: DecisionCaseSuite,
    *,
    policy: GatePolicy | None = None,
) -> dict:
    policy = policy or GatePolicy()
    by_id: dict[str, DecisionCase] = {case.case_id: case for case in suite.cases}
    wants_attention = {case_id for case_id, case in by_id.items() if is_important(case)}
    low_signal = set(by_id) - wants_attention

    dismissable_low_signal: dict[int, set[str]] = defaultdict(set)
    dismissable_attention: set[str] = set()
    gate_blocked: set[str] = set()
    forbidden_proposals: list[dict] = []
    failed = 0

    for observation in observations:
        if observation.status != "ok":
            failed += 1
            continue
        case = by_id.get(observation.item_id)
        if case is None:
            continue
        decision = observation.as_decision()
        gate = apply_gate(decision, policy)
        if observation.action in set(case.forbidden_actions):
            # The four-way answer is never executed today, so this cannot hurt the
            # user. It is the only honest evidence about whether it safely could be.
            forbidden_proposals.append(
                {"item_id": observation.item_id, "rep": observation.rep, "action": observation.action}
            )
        if _dismissable(decision, gate, policy):
            if observation.item_id in wants_attention:
                dismissable_attention.add(observation.item_id)
            else:
                dismissable_low_signal[observation.rep].add(observation.item_id)
        elif observation.item_id in low_signal:
            gate_blocked.add(observation.item_id)

    per_rep = [
        len(dismissable_low_signal[rep]) / len(low_signal) if low_signal else None
        for rep in sorted({o.rep for o in observations})
    ]
    clean = [value for value in per_rep if value is not None]
    return {
        "low_signal_cases": len(low_signal),
        "attention_cases": len(wants_attention),
        "failed_observations": failed,
        # Ceiling, not a score: no plan can dismiss more than the gate lets through.
        "dismissal_ceiling_median": round(statistics.median(clean), 4) if clean else None,
        "dismissal_ceiling_per_rep": [round(v, 4) for v in clean],
        "gate_blocked_low_signal": sorted(gate_blocked),
        "dismissable_attention_items": sorted(dismissable_attention),
        "forbidden_action_proposals": forbidden_proposals,
        "forbidden_action_proposal_count": len(forbidden_proposals),
    }


def determinism(observations: list[Observation]) -> dict:
    by_case: dict[str, list[Observation]] = defaultdict(list)
    for observation in observations:
        by_case[observation.item_id].append(observation)

    reps = len({o.rep for o in observations})
    flipped, jittered, spreads = [], [], []
    for item_id, group in sorted(by_case.items()):
        answers = {(o.status, o.action, o.important) for o in group}
        confidences = [o.importance_confidence for o in group if o.importance_confidence is not None]
        spread = round(max(confidences) - min(confidences), 4) if len(confidences) > 1 else 0.0
        spreads.append(spread)
        entry = {
            "item_id": item_id,
            "answers": sorted(str(a) for a in answers),
            "importance_confidence_spread": spread,
        }
        if len(answers) > 1:
            flipped.append(entry)
        elif spread > 0:
            jittered.append(entry)
    return {
        "reps": reps,
        "cases": len(by_case),
        # Two different things, kept apart: an answer that changes reroutes the email,
        # while a confidence that wobbles only matters where it crosses a threshold.
        # Reporting one number for both called a provider unstable that never once
        # changed its mind. Stability is measured, not assumed, before trials are cut.
        "answers_stable": reps > 1 and not flipped,
        "answer_flip_cases": len(flipped),
        "confidence_jitter_cases": len(jittered),
        "max_importance_confidence_spread": max(spreads) if spreads else 0.0,
        "answer_flips": flipped[:20],
        "confidence_jitter": jittered[:20],
    }


def summarise_provider(
    observations: list[Observation], batches: list[dict], suite: DecisionCaseSuite
) -> dict:
    walls = [b["wall_ms"] for b in batches]
    return {
        "provider": batches[0]["provider"] if batches else None,
        "model": batches[0]["model"] if batches else None,
        "reps": len(batches),
        "requests": sum(b["requests"] for b in batches),
        "failed_requests": sum(b["failed_requests"] for b in batches),
        "usage_reported": all(b["usage_reported"] for b in batches),
        "input_tokens": sum(b["input_tokens"] for b in batches),
        "output_tokens": sum(b["output_tokens"] for b in batches),
        "wall_ms_median": round(statistics.median(walls), 2) if walls else None,
        "wall_ms_range": [min(walls), max(walls)] if walls else None,
        "gate": gate_report(observations, suite),
        "determinism": determinism(observations),
        "importance_confidence_histogram": confidence_histogram(observations, suite),
    }


def planned_requests(item_count: int, provider_count: int, reps: int, settings: DecisionSettings) -> int:
    chunks = math.ceil(item_count / max(1, settings.max_items_per_request))
    return chunks * reps * provider_count


def build_probe_provider(name: str, settings: DecisionSettings | None = None) -> DecisionProvider:
    from app.agent.triage_tools import build_provider

    return build_provider(replace(settings or decision_settings(), provider=name))


def dry_run_probe_provider(cases: list[DecisionCase], name: str) -> DecisionProvider:
    """Offline stand-in. Answers are derived from the position of the case id, never
    from its labels, so a dry run exercises the plumbing and the histogram without
    producing anything that looks like a quality result."""
    from app.decisions.contract import ReasonCode
    from app.decisions.fake import FakeDecisionProvider

    confidences = (0.05, 0.3, 0.55, 0.8, 0.95)
    scripted, importance = {}, {}
    for index, case in enumerate(sorted(cases, key=lambda c: c.case_id)):
        action = ("needs_reply", "mark_read", "archive", "delete")[index % 4]
        scripted[case.case_id] = (action, ReasonCode.OTHER, 0.6)
        importance[case.case_id] = (index % 2 == 0, confidences[index % len(confidences)])
    provider = FakeDecisionProvider(scripted, importance=importance)
    provider.name = name
    return provider


def run_probe(
    provider_names: list[str],
    *,
    reps: int = 1,
    provider_factory=None,
    suite: DecisionCaseSuite | None = None,
) -> tuple[dict, list[Observation], list[dict]]:
    suite = suite or load_case_suite()
    items = [triage_items(suite)[case.case_id] for case in sorted(suite.cases, key=lambda c: c.case_id)]
    factory = provider_factory or build_probe_provider

    # Built once and then alternated rep by rep. Running a provider's reps as its
    # own block measures it under its own stretch of network, and the latency
    # comparison between two such blocks is unpaired — which is how a 4x gap got
    # reported from two runs taken minutes apart. Interleaving puts every rep of
    # every provider in the same conditions, and the batch rows land in the file
    # in call order so the interleaving is checkable afterwards.
    providers = {name: factory(name) for name in provider_names}
    by_provider: dict[str, list[Observation]] = {name: [] for name in provider_names}
    batches_by_provider: dict[str, list[dict]] = {name: [] for name in provider_names}
    everything: list[Observation] = []
    all_batches: list[dict] = []
    for rep in range(1, reps + 1):
        for name in provider_names:
            observations, record = probe_once(providers[name], items, rep)
            by_provider[name].extend(observations)
            batches_by_provider[name].append(record)
            everything.extend(observations)
            all_batches.append(record)

    summary = {
        name: summarise_provider(by_provider[name], batches_by_provider[name], suite)
        for name in provider_names
    }
    return summary, everything, all_batches


def main() -> int:
    parser = argparse.ArgumentParser(description="Provider-only probe of the dismissal gate.")
    parser.add_argument("--providers", nargs="+", default=["jev", "llm"])
    parser.add_argument("--reps", type=int, default=3)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_RESULTS_DIR)
    parser.add_argument(
        "--max-requests",
        type=int,
        default=24,
        help="Refuse to start if the plan would exceed this many provider requests.",
    )
    parser.add_argument(
        "--live", action="store_true", help="Call the configured providers instead of the stand-in."
    )
    parser.add_argument("--label", default=None, help="Name this run instead of stamping it with the time.")
    args = parser.parse_args()

    suite = load_case_suite()
    settings = decision_settings()
    planned = planned_requests(len(suite.cases), len(args.providers), args.reps, settings)
    mode = "live" if args.live else "dry_run"
    if args.live and planned > args.max_requests:
        print(
            f"refusing to start: {planned} provider requests planned, budget is "
            f"{args.max_requests} ({len(suite.cases)} cases, {args.reps} reps, "
            f"{len(args.providers)} providers, {settings.max_items_per_request} per request)"
        )
        return 1

    factory = None if args.live else (lambda name: dry_run_probe_provider(suite.cases, name))
    run_id = args.label or time.strftime("%Y%m%dT%H%M%S", time.gmtime())
    summary, observations, batches = run_probe(
        args.providers, reps=args.reps, provider_factory=factory, suite=suite
    )
    report = {
        "mode": mode,
        "run_id": run_id,
        "cases": len(suite.cases),
        "reps": args.reps,
        "planned_requests": planned,
        "providers": summary,
    }

    args.output_dir.mkdir(parents=True, exist_ok=True)
    # The run id is in the filename because a paid run must not be overwritten by
    # the next one: the batch rows go in beside the answers so any summary here can
    # be rebuilt from the file instead of being re-bought.
    recorder = TrialRecorder(args.output_dir / f"decision-probe-{mode}-{run_id}.jsonl")
    for batch in batches:
        recorder.append({"kind": "batch", "run_id": run_id, **batch})
    for observation in observations:
        recorder.append({"kind": "answer", "run_id": run_id, **asdict(observation)})
    (args.output_dir / f"decision-probe-{mode}-{run_id}-summary.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )

    print(f"mode={mode} cases={len(suite.cases)} reps={args.reps} planned_requests={planned}")
    for name, entry in summary.items():
        gate = entry["gate"]
        print(
            f"{name:<5} model={entry['model']} req={entry['requests']} "
            f"in_tok={entry['input_tokens']} out_tok={entry['output_tokens']} "
            f"usage_reported={entry['usage_reported']} wall_med={entry['wall_ms_median']}ms"
        )
        print(
            f"      dismissal_ceiling={gate['dismissal_ceiling_median']} "
            f"(of {gate['low_signal_cases']} low-signal) "
            f"gate_blocked={len(gate['gate_blocked_low_signal'])} "
            f"would_dismiss_attention={len(gate['dismissable_attention_items'])} "
            f"forbidden_proposals={gate['forbidden_action_proposal_count']} "
            f"answers_stable={entry['determinism']['answers_stable']} "
            f"flips={entry['determinism']['answer_flip_cases']} "
            f"jitter={entry['determinism']['confidence_jitter_cases']}"
            f"/{entry['determinism']['max_importance_confidence_spread']}"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
