from dataclasses import dataclass, field

from app.agent.triage_tools import TriagePlan
from app.evals.decision_cases import DecisionCase, attention_label

DESTRUCTIVE_ACTIONS = {"archive", "delete"}


@dataclass
class ArmBuckets:
    """An arm's output in terms the user feels: what was put in front of them,
    what was offered for dismissal, and what it declined to touch."""

    needs_reply: set[str] = field(default_factory=set)
    important: set[str] = field(default_factory=set)
    dismissed: set[str] = field(default_factory=set)
    untouched: set[str] = field(default_factory=set)
    # Dismissals that would remove mail from the inbox rather than just mark it read.
    destructive: dict[str, str] = field(default_factory=dict)

    @property
    def surfaced(self) -> set[str]:
        return self.needs_reply | self.important


def buckets_from_plan(plan: TriagePlan) -> ArmBuckets:
    return ArmBuckets(
        needs_reply={i.email_id for i in plan.needs_reply},
        important={i.email_id for i in plan.important},
        dismissed={i.email_id for i in plan.bulk},
        untouched={i.email_id for i in plan.unresolved},
    )


def buckets_from_triage_batch(args: dict, all_ids: list[str]) -> ArmBuckets:
    """The baseline agent's apply_triage_batch arguments in the same terms. Its
    three bulk actions all take mail off the user's attention list, so they count
    as dismissals; archive and delete are recorded separately as destructive."""
    actions = args.get("actions") or []
    needs_reply = {n.get("email_id") for n in (args.get("needs_reply") or []) if n.get("email_id")}
    dismissed, destructive = set(), {}
    for action in actions:
        email_id = action.get("email_id")
        if not email_id:
            continue
        dismissed.add(email_id)
        if action.get("action") in DESTRUCTIVE_ACTIONS:
            destructive[email_id] = action["action"]
    covered = needs_reply | dismissed
    return ArmBuckets(
        needs_reply=needs_reply,
        dismissed=dismissed,
        untouched={i for i in all_ids if i not in covered},
        destructive=destructive,
    )


def _prf(true_positive: int, predicted: int, actual: int) -> dict[str, float]:
    precision = true_positive / predicted if predicted else 0.0
    recall = true_positive / actual if actual else 0.0
    f1 = 2 * precision * recall / (precision + recall) if precision + recall else 0.0
    return {"precision": round(precision, 4), "recall": round(recall, 4), "f1": round(f1, 4)}


def score_buckets(buckets: ArmBuckets, cases: list[DecisionCase]) -> dict:
    by_id = {case.case_id: case for case in cases}
    gold = {case_id: attention_label(case) for case_id, case in by_id.items()}
    scored = set(by_id)

    wants_attention = {i for i, label in gold.items() if label != "low_signal"}
    low_signal = scored - wants_attention
    gold_reply = {i for i, label in gold.items() if label == "needs_reply"}

    surfaced = buckets.surfaced & scored
    dismissed = buckets.dismissed & scored
    untouched = buckets.untouched & scored

    buried = sorted(wants_attention & dismissed)
    destroyed = sorted(
        i for i in wants_attention if i in buckets.destructive
    )
    forbidden_violations = sorted(
        i
        for i, case in by_id.items()
        if buckets.destructive.get(i) in set(case.forbidden_actions)
    )

    return {
        "items": len(scored),
        # The risk in this design is burying something, not deleting it.
        "attention_recall": round(len(wants_attention & surfaced) / len(wants_attention), 4)
        if wants_attention
        else None,
        "buried": buried,
        "buried_count": len(buried),
        "buried_destructively": destroyed,
        "forbidden_action_violations": forbidden_violations,
        "needs_reply": _prf(
            len(gold_reply & buckets.needs_reply), len(buckets.needs_reply & scored), len(gold_reply)
        ),
        "false_surfacing": len(low_signal & surfaced),
        # What the user is left to do by hand, and what one click clears.
        "review_effort": len(surfaced) + len(untouched),
        "one_click_dismissals": len(dismissed),
        "untouched": len(untouched),
        "coverage": round(1 - len(untouched) / len(scored), 4) if scored else None,
    }


def score_by_group(buckets: ArmBuckets, cases: list[DecisionCase], key) -> dict[str, dict]:
    groups: dict[str, list[DecisionCase]] = {}
    for case in cases:
        groups.setdefault(key(case), []).append(case)
    return {name: score_buckets(buckets, group) for name, group in sorted(groups.items())}


def score_arm(buckets: ArmBuckets, cases: list[DecisionCase]) -> dict:
    return {
        "overall": score_buckets(buckets, cases),
        "by_language": score_by_group(buckets, cases, lambda c: c.language),
        "by_attention": score_by_group(buckets, cases, attention_label),
        "by_origin": score_by_group(buckets, cases, lambda c: c.source.kind),
    }
