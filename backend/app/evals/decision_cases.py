import json
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field, model_validator

from app.decisions.contract import TRIAGE_ACTIONS, TriageAction, TriageItem


BACKEND_DIR = Path(__file__).resolve().parents[2]
DEFAULT_CASES_PATH = BACKEND_DIR / "evals" / "triage_decision_cases.json"
FIXTURE_PATHS = {
    "eval": BACKEND_DIR / "app" / "seed" / "eval_emails.json",
    "dev": BACKEND_DIR / "app" / "seed" / "dev_emails.json",
}

CaseLanguage = Literal["en", "zh", "mixed"]


class InlineEmail(BaseModel):
    subject: str
    sender_name: str | None = None
    sender_email: str
    received: str
    body_preview: str


class CaseSource(BaseModel):
    kind: Literal["fixture", "inline"]
    fixture: Literal["eval", "dev"] | None = None
    email_id: str | None = None
    email: InlineEmail | None = None

    @model_validator(mode="after")
    def _check_shape(self):
        if self.kind == "fixture" and not (self.fixture and self.email_id):
            raise ValueError("fixture source needs fixture and email_id")
        if self.kind == "inline" and self.email is None:
            raise ValueError("inline source needs email")
        return self


class DecisionCase(BaseModel):
    case_id: str
    language: CaseLanguage
    source: CaseSource
    reply_needed: bool
    default_policy_action: TriageAction
    acceptable_actions: list[TriageAction] = Field(min_length=1)
    forbidden_actions: list[TriageAction] = Field(default_factory=list)
    note: str

    @model_validator(mode="after")
    def _check_labels(self):
        if self.default_policy_action not in self.acceptable_actions:
            raise ValueError(f"{self.case_id}: default action must be acceptable")
        overlap = set(self.acceptable_actions) & set(self.forbidden_actions)
        if overlap:
            raise ValueError(f"{self.case_id}: {sorted(overlap)} both acceptable and forbidden")
        if self.reply_needed != (self.default_policy_action == "needs_reply"):
            raise ValueError(f"{self.case_id}: reply_needed disagrees with default action")
        if self.reply_needed and "needs_reply" in self.forbidden_actions:
            raise ValueError(f"{self.case_id}: needs_reply cannot be forbidden when a reply is needed")
        return self


class DecisionCaseSuite(BaseModel):
    schema_version: int
    suite_id: str
    label_policy: dict[str, str]
    cases: list[DecisionCase] = Field(min_length=1)

    @model_validator(mode="after")
    def _unique_ids(self):
        ids = [case.case_id for case in self.cases]
        if len(ids) != len(set(ids)):
            raise ValueError("duplicate case_id")
        return self


def load_case_suite(path: Path | str = DEFAULT_CASES_PATH) -> DecisionCaseSuite:
    return DecisionCaseSuite.model_validate(json.loads(Path(path).read_text(encoding="utf-8")))


def _fixture_emails(name: str) -> dict[str, dict]:
    rows = json.loads(FIXTURE_PATHS[name].read_text(encoding="utf-8"))
    return {row["id"]: row for row in rows}


def triage_items(suite: DecisionCaseSuite) -> dict[str, TriageItem]:
    """Build the provider input for each case. Only the fields every adapter
    already sends leave this function."""
    fixtures: dict[str, dict[str, dict]] = {}
    items = {}
    for case in suite.cases:
        source = case.source
        if source.kind == "inline":
            email = source.email
            items[case.case_id] = TriageItem(
                item_id=case.case_id,
                subject=email.subject,
                sender=email.sender_name or email.sender_email,
                sender_email=email.sender_email,
                received=email.received,
                body_preview=email.body_preview,
            )
            continue
        if source.fixture not in fixtures:
            fixtures[source.fixture] = _fixture_emails(source.fixture)
        row = fixtures[source.fixture].get(source.email_id)
        if row is None:
            raise KeyError(f"{case.case_id}: unknown {source.fixture} fixture {source.email_id}")
        items[case.case_id] = TriageItem(
            item_id=case.case_id,
            subject=row["subject"],
            sender=row.get("sender_name") or row["sender_email"],
            sender_email=row["sender_email"],
            received=row["received_datetime"],
            body_preview=row.get("body_preview") or "",
        )
    return items


AttentionLabel = Literal["needs_reply", "important", "low_signal"]


def is_important(case: DecisionCase) -> bool:
    """Gold answer to the provider's importance question. Derived rather than
    stored: a case is important exactly when a person is waiting on it or when
    some action would lose something the annotators agreed the user needs."""
    return case.reply_needed or bool(case.forbidden_actions)


def attention_label(case: DecisionCase) -> AttentionLabel:
    if case.reply_needed:
        return "needs_reply"
    return "important" if case.forbidden_actions else "low_signal"


def attention_groups(suite: DecisionCaseSuite) -> dict[AttentionLabel, list[str]]:
    groups: dict[AttentionLabel, list[str]] = {}
    for case in suite.cases:
        groups.setdefault(attention_label(case), []).append(case.case_id)
    return groups


def language_groups(suite: DecisionCaseSuite) -> dict[str, list[str]]:
    groups: dict[str, list[str]] = {}
    for case in suite.cases:
        groups.setdefault(case.language, []).append(case.case_id)
    return groups


__all__ = [
    "TRIAGE_ACTIONS",
    "DEFAULT_CASES_PATH",
    "DecisionCase",
    "DecisionCaseSuite",
    "attention_groups",
    "attention_label",
    "is_important",
    "language_groups",
    "load_case_suite",
    "triage_items",
]
