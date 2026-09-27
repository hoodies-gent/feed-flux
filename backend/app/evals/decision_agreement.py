import json
from collections import Counter
from pathlib import Path

from pydantic import BaseModel, Field

from app.decisions.contract import TRIAGE_ACTIONS, TriageAction
from app.evals.decision_cases import BACKEND_DIR, DecisionCaseSuite


DEFAULT_ANNOTATIONS_PATH = BACKEND_DIR / "evals" / "triage_decision_annotations.json"


class Annotation(BaseModel):
    case_id: str
    reply_needed: bool
    default_policy_action: TriageAction
    acceptable_actions: list[TriageAction] = Field(min_length=1)
    forbidden_actions: list[TriageAction] = Field(default_factory=list)


class Annotator(BaseModel):
    annotator_id: str
    model_family: str
    cases: list[Annotation]


class AnnotationSet(BaseModel):
    schema_version: int
    suite_id: str
    protocol: str
    caveat: str
    annotators: list[Annotator] = Field(min_length=3)


class MajorityLabel(BaseModel):
    reply_needed: bool
    default_policy_action: TriageAction
    acceptable_actions: list[TriageAction]
    forbidden_actions: list[TriageAction]


def load_annotations(path: Path | str = DEFAULT_ANNOTATIONS_PATH) -> AnnotationSet:
    return AnnotationSet.model_validate(json.loads(Path(path).read_text(encoding="utf-8")))


def fleiss_kappa(ratings: list[list]) -> tuple[float, float]:
    """Returns (observed agreement, Fleiss' kappa) for one categorical label.
    Each row holds one rating per annotator for a single case."""
    if not ratings:
        raise ValueError("no ratings")
    raters = len(ratings[0])
    if raters < 2 or any(len(row) != raters for row in ratings):
        raise ValueError("every case needs the same number of raters, at least 2")
    rows = [Counter(row) for row in ratings]
    categories = sorted({category for row in rows for category in row}, key=str)
    n = len(rows)
    proportions = {c: sum(row.get(c, 0) for row in rows) / (n * raters) for c in categories}
    per_case = [
        (sum(row.get(c, 0) ** 2 for c in categories) - raters) / (raters * (raters - 1))
        for row in rows
    ]
    observed = sum(per_case) / n
    expected = sum(value**2 for value in proportions.values())
    # Perfectly skewed marginals leave no room for chance disagreement.
    kappa = 1.0 if expected >= 1 else (observed - expected) / (1 - expected)
    return observed, kappa


def _by_case(annotations: AnnotationSet) -> dict[str, list[Annotation]]:
    grouped: dict[str, list[Annotation]] = {}
    for annotator in annotations.annotators:
        for case in annotator.cases:
            grouped.setdefault(case.case_id, []).append(case)
    return grouped


def majority_labels(annotations: AnnotationSet) -> tuple[dict[str, MajorityLabel], list[str]]:
    """Majority of the annotators, with no tie-breaking: a case without a
    majority is returned as unresolved so a person has to settle it."""
    labels: dict[str, MajorityLabel] = {}
    unresolved: list[str] = []
    for case_id, rows in _by_case(annotations).items():
        threshold = len(rows) // 2 + 1
        defaults = Counter(row.default_policy_action for row in rows)
        action, votes = defaults.most_common(1)[0]
        if votes < threshold:
            unresolved.append(case_id)
            continue
        labels[case_id] = MajorityLabel(
            reply_needed=sum(row.reply_needed for row in rows) >= threshold,
            default_policy_action=action,
            acceptable_actions=[
                a for a in TRIAGE_ACTIONS if sum(a in row.acceptable_actions for row in rows) >= threshold
            ],
            forbidden_actions=[
                a for a in TRIAGE_ACTIONS if sum(a in row.forbidden_actions for row in rows) >= threshold
            ],
        )
    return labels, sorted(unresolved)


def agreement_report(annotations: AnnotationSet) -> dict:
    grouped = _by_case(annotations)
    case_ids = sorted(grouped)
    report: dict = {"cases": len(case_ids), "annotators": len(annotations.annotators), "labels": {}}

    def add(name: str, pick) -> None:
        ratings = [[pick(row) for row in grouped[case_id]] for case_id in case_ids]
        observed, kappa = fleiss_kappa(ratings)
        unanimous = sum(1 for row in ratings if len(set(row)) == 1)
        entry = {
            "observed_agreement": round(observed, 4),
            "fleiss_kappa": round(kappa, 4),
            "unanimous_cases": unanimous,
        }
        positives = [sum(1 for value in row if value is True) for row in ratings]
        if any(positives):
            marked = sum(1 for count in positives if count)
            # Kappa is unstable when almost every case shares one value, so say when that holds.
            entry["rare_label"] = marked <= 0.1 * len(case_ids) or marked >= 0.9 * len(case_ids)
        report["labels"][name] = entry

    add("reply_needed", lambda row: row.reply_needed)
    add("default_policy_action", lambda row: row.default_policy_action)
    for action in TRIAGE_ACTIONS:
        add(f"forbids_{action}", lambda row, a=action: a in row.forbidden_actions)

    _, unresolved = majority_labels(annotations)
    report["unresolved_cases"] = unresolved
    return report


def shipped_label_mismatches(suite: DecisionCaseSuite, annotations: AnnotationSet) -> list[str]:
    """Case ids where the shipped label is not the majority of the annotators."""
    labels, unresolved = majority_labels(annotations)
    mismatches = list(unresolved)
    for case in suite.cases:
        majority = labels.get(case.case_id)
        if majority is None:
            if case.case_id not in mismatches:
                mismatches.append(case.case_id)
            continue
        shipped = (
            case.reply_needed,
            case.default_policy_action,
            sorted(case.acceptable_actions),
            sorted(case.forbidden_actions),
        )
        resolved = (
            majority.reply_needed,
            majority.default_policy_action,
            sorted(majority.acceptable_actions),
            sorted(majority.forbidden_actions),
        )
        if shipped != resolved:
            mismatches.append(case.case_id)
    return sorted(mismatches)
