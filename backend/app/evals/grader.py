import json
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any


M1_CATEGORIES = {
    "meeting_reply",
    "batch_triage",
    "draft_creation",
    "draft_rewrite",
    "high_risk_approval",
}
APPROVAL_EXPECTATIONS = {"required", "forbidden", "not_applicable"}
ASSERTION_OPERATORS = {"eq", "contains", "set_equals"}
DEFAULT_SUITE_PATH = Path(__file__).resolve().parents[2] / "evals" / "golden_tasks.json"
DEFAULT_EMAIL_FIXTURE_PATH = Path(__file__).resolve().parents[1] / "seed" / "eval_emails.json"


@dataclass(frozen=True)
class StateAssertion:
    path: str
    operator: str
    value: Any


@dataclass(frozen=True)
class GoldenExpectations:
    required_tool_sequence: tuple[str, ...]
    forbidden_tools: tuple[str, ...]
    target_email_ids: tuple[str, ...]
    approval: str
    approval_tool: str | None
    final_state: tuple[StateAssertion, ...]


@dataclass(frozen=True)
class GoldenTask:
    id: str
    category: str
    prompt: str
    fixture_email_ids: tuple[str, ...]
    setup: dict[str, Any]
    expectations: GoldenExpectations


@dataclass(frozen=True)
class GoldenSuite:
    schema_version: int
    suite_id: str
    tasks: tuple[GoldenTask, ...]


@dataclass(frozen=True)
class GradeFailure:
    code: str
    detail: str


@dataclass(frozen=True)
class GradeResult:
    task_id: str
    task_success: bool
    target_email_identification: bool | None
    tool_selection: bool
    approval_trigger: bool | None
    final_business_state: bool
    failures: tuple[GradeFailure, ...]

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


def _parse_task(raw: dict[str, Any]) -> GoldenTask:
    expected = raw["expectations"]
    assertions = tuple(
        StateAssertion(
            path=item["path"],
            operator=item["operator"],
            value=item.get("value"),
        )
        for item in expected["final_state"]
    )
    return GoldenTask(
        id=raw["id"],
        category=raw["category"],
        prompt=raw["prompt"],
        fixture_email_ids=tuple(raw.get("fixture_email_ids", [])),
        setup=raw.get("setup", {}),
        expectations=GoldenExpectations(
            required_tool_sequence=tuple(expected["required_tool_sequence"]),
            forbidden_tools=tuple(expected.get("forbidden_tools", [])),
            target_email_ids=tuple(expected.get("target_email_ids", [])),
            approval=expected.get("approval", "not_applicable"),
            approval_tool=expected.get("approval_tool"),
            final_state=assertions,
        ),
    )


def _validate_suite(suite: GoldenSuite, fixture_path: Path) -> None:
    if suite.schema_version != 1:
        raise ValueError(f"Unsupported golden suite schema: {suite.schema_version}")
    if not suite.suite_id:
        raise ValueError("Golden suite requires a suite_id")

    task_ids = [task.id for task in suite.tasks]
    categories = [task.category for task in suite.tasks]
    if len(task_ids) != len(set(task_ids)):
        raise ValueError("Golden task IDs must be unique")
    if len(categories) != len(set(categories)):
        raise ValueError("Golden task categories must be unique")
    if set(categories) != M1_CATEGORIES:
        raise ValueError(
            f"Golden suite categories must be {sorted(M1_CATEGORIES)}, got {sorted(categories)}"
        )

    with fixture_path.open() as fixture_file:
        fixture_ids = {email["id"] for email in json.load(fixture_file)}

    for task in suite.tasks:
        if not task.prompt.strip():
            raise ValueError(f"Golden task {task.id!r} requires a prompt")
        missing_fixtures = set(task.fixture_email_ids) - fixture_ids
        if missing_fixtures:
            raise ValueError(
                f"Golden task {task.id!r} references missing fixtures: {sorted(missing_fixtures)}"
            )
        if task.expectations.approval not in APPROVAL_EXPECTATIONS:
            raise ValueError(
                f"Golden task {task.id!r} has invalid approval expectation"
            )
        if (
            task.expectations.approval == "required"
            and not task.expectations.approval_tool
        ):
            raise ValueError(
                f"Golden task {task.id!r} requires an approval_tool"
            )
        invalid_operators = {
            assertion.operator
            for assertion in task.expectations.final_state
            if assertion.operator not in ASSERTION_OPERATORS
        }
        if invalid_operators:
            raise ValueError(
                f"Golden task {task.id!r} has invalid assertion operators: "
                f"{sorted(invalid_operators)}"
            )


def load_golden_suite(
    path: str | Path = DEFAULT_SUITE_PATH,
    fixture_path: str | Path = DEFAULT_EMAIL_FIXTURE_PATH,
) -> GoldenSuite:
    with Path(path).open() as suite_file:
        raw = json.load(suite_file)
    suite = GoldenSuite(
        schema_version=raw["schema_version"],
        suite_id=raw["suite_id"],
        tasks=tuple(_parse_task(task) for task in raw["tasks"]),
    )
    _validate_suite(suite, Path(fixture_path))
    return suite


def _is_ordered_subsequence(required: tuple[str, ...], observed: list[str]) -> bool:
    observed_iter = iter(observed)
    return all(any(name == candidate for candidate in observed_iter) for name in required)


_MISSING = object()


def _resolve_path(state: dict[str, Any], path: str) -> Any:
    current: Any = state
    for part in path.split("."):
        if not isinstance(current, dict) or part not in current:
            return _MISSING
        current = current[part]
    return current


def _assert_state(assertion: StateAssertion, state: dict[str, Any]) -> bool:
    actual = _resolve_path(state, assertion.path)
    if actual is _MISSING:
        return False
    if assertion.operator == "eq":
        return actual == assertion.value
    if assertion.operator == "contains":
        try:
            return assertion.value in actual
        except TypeError:
            return False
    if assertion.operator == "set_equals":
        try:
            return set(actual) == set(assertion.value)
        except TypeError:
            return False
    return False


def grade_task(task: GoldenTask, observation: dict[str, Any]) -> GradeResult:
    failures: list[GradeFailure] = []
    if observation.get("error"):
        failures.append(GradeFailure("runner_error", str(observation["error"])))

    observed_tools = [call.get("name") for call in observation.get("tool_calls", [])]
    has_sequence = _is_ordered_subsequence(
        task.expectations.required_tool_sequence,
        observed_tools,
    )
    forbidden_used = sorted(
        set(observed_tools).intersection(task.expectations.forbidden_tools)
    )
    if not has_sequence:
        failures.append(
            GradeFailure(
                "tool_sequence",
                f"expected ordered tools {list(task.expectations.required_tool_sequence)}, "
                f"observed {observed_tools}",
            )
        )
    if forbidden_used:
        failures.append(
            GradeFailure("forbidden_tool", f"used forbidden tools {forbidden_used}")
        )
    tool_selection = has_sequence and not forbidden_used

    if task.expectations.target_email_ids:
        expected_targets = set(task.expectations.target_email_ids)
        observed_targets = set(observation.get("target_email_ids", []))
        target_email_identification = observed_targets == expected_targets
        if not target_email_identification:
            failures.append(
                GradeFailure(
                    "target_email_identification",
                    f"expected targets {sorted(expected_targets)}, "
                    f"observed {sorted(observed_targets)}",
                )
            )
    else:
        target_email_identification = None

    approval = observation.get("approval", {})
    approval_expectation = task.expectations.approval
    if approval_expectation == "not_applicable":
        approval_trigger = None
    elif approval_expectation == "required":
        approval_trigger = bool(approval.get("triggered")) and (
            approval.get("tool") == task.expectations.approval_tool
        )
    else:
        approval_trigger = not bool(approval.get("triggered"))
    if approval_trigger is False:
        failures.append(
            GradeFailure(
                "approval_trigger",
                f"expected approval={approval_expectation!r} "
                f"for tool={task.expectations.approval_tool!r}",
            )
        )

    final_state = observation.get("final_state", {})
    failed_paths = [
        assertion.path
        for assertion in task.expectations.final_state
        if not _assert_state(assertion, final_state)
    ]
    final_business_state = not failed_paths
    if failed_paths:
        failures.append(
            GradeFailure(
                "final_state",
                f"failed business-state assertions: {failed_paths}",
            )
        )

    task_success = not failures
    return GradeResult(
        task_id=task.id,
        task_success=task_success,
        target_email_identification=target_email_identification,
        tool_selection=tool_selection,
        approval_trigger=approval_trigger,
        final_business_state=final_business_state,
        failures=tuple(failures),
    )
