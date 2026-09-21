import argparse
import asyncio
import json
import os
import tempfile
import time
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, AsyncIterator, Callable

from langchain_core.callbacks import UsageMetadataCallbackHandler

from app.agent.execution_context import current_run_id
from app.agent.graph import build_agent
from app.agent.llm import get_llm
from app.agent.stream import new_turn_input, stream_agent
from app.agent.usage import TokenPricing, estimate_cost, usage_totals
from app.core.config import Config
from app.evals.grader import (
    DEFAULT_EMAIL_FIXTURE_PATH,
    GoldenTask,
    grade_task,
    load_golden_suite,
)
from app.evals.recorder import TrialRecorder
from app.models.email import DraftReply, LabelAction, SentAction
from app.services.database import DatabaseService


BACKEND_DIR = Path(__file__).resolve().parents[2]
DEFAULT_RESULTS_DIR = BACKEND_DIR / "evals" / "results"
EventSource = Callable[..., AsyncIterator[dict[str, Any]]]


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _new_run_id() -> str:
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    return f"{timestamp}-{uuid.uuid4().hex[:8]}"


@contextmanager
def _environment_value(name: str, value: str):
    previous = os.environ.get(name)
    os.environ[name] = value
    try:
        yield
    finally:
        if previous is None:
            os.environ.pop(name, None)
        else:
            os.environ[name] = previous


def _email_row(email: dict[str, Any]) -> dict[str, Any]:
    received = datetime.fromisoformat(
        email["received_datetime"].replace("Z", "+00:00")
    )
    return {
        "id": email["id"],
        "subject": email["subject"],
        "sender_name": email.get("sender_name"),
        "sender_email": email["sender_email"],
        "received_datetime": int(received.timestamp()),
        "body_preview": email.get("body_preview"),
        "body_content": email.get("body_content"),
        "body_html": email.get("body_html"),
        "is_read": email.get("is_read", False),
        "has_attachments": email.get("has_attachments", False),
        "attachments": email.get("attachments"),
        "metadata_json": {
            "category": email.get("category"),
            "fixture_kind": "eval",
        },
    }


def _prepare_trial(db: DatabaseService, task: GoldenTask) -> dict[str, Any]:
    with DEFAULT_EMAIL_FIXTURE_PATH.open() as fixture_file:
        fixtures = {
            email["id"]: email
            for email in json.load(fixture_file)
            if email["id"] in task.fixture_email_ids
        }
    for email_id in task.fixture_email_ids:
        db.insert_email(_email_row(fixtures[email_id]))

    setup: dict[str, Any] = {}
    draft = task.setup.get("draft")
    if draft:
        body = draft["body"]
        selection = draft["selection_text"]
        selection_start = body.index(selection)
        draft_id = db.create_draft(
            {
                "thread_id": "eval-setup",
                "email_id": draft["email_id"],
                "recipient": draft["recipient"],
                "subject": draft["subject"],
                "body": body,
            }
        )
        setup["draft"] = {
            **draft,
            "draft_id": draft_id,
            "selection_start": selection_start,
            "selection_end": selection_start + len(selection),
        }
    return setup


def _task_prompt(task: GoldenTask, setup: dict[str, Any]) -> str:
    draft = setup.get("draft")
    if not draft:
        return task.prompt
    return (
        f"Rewrite only the selected text in draft {draft['draft_id']} for the email "
        f"with id \"{draft['email_id']}\". Selection offsets are "
        f"{draft['selection_start']}-{draft['selection_end']}; selected text is:\n"
        f"{draft['selection_text']}\nInstruction: {task.prompt} "
        "Call apply_draft_patch with the same draft_id, original_email_id, and offsets. "
        "Return only the replacement passage and preserve every other character."
    )


def _build_provider_agent(
    provider: str,
    model: str,
    *,
    request_timeout: float,
    max_retries: int,
):
    llm = get_llm(
        provider,
        model_name=model,
        timeout=request_timeout,
        max_retries=max_retries,
    )
    return build_agent(llm=llm)


async def _provider_events(
    graph_input: dict[str, Any],
    thread_id: str,
    callbacks: list[Any],
    tool_output_limit: int | None,
    provider: str,
    model: str,
    request_timeout: float,
    max_retries: int,
) -> AsyncIterator[dict[str, Any]]:
    agent = _build_provider_agent(
        provider,
        model,
        request_timeout=request_timeout,
        max_retries=max_retries,
    )
    async for event in stream_agent(
        graph_input,
        thread_id,
        callbacks=callbacks,
        tool_output_limit=tool_output_limit,
        agent=agent,
    ):
        yield event


def _ids_from_value(value: Any) -> set[str]:
    ids: set[str] = set()
    if isinstance(value, dict):
        for key, item in value.items():
            if key in {"email_id", "original_email_id"} and isinstance(item, str):
                ids.add(item)
            else:
                ids.update(_ids_from_value(item))
    elif isinstance(value, list):
        for item in value:
            ids.update(_ids_from_value(item))
    return ids


def _final_state(
    db: DatabaseService,
    task: GoldenTask,
    setup: dict[str, Any],
    events: list[dict[str, Any]],
) -> dict[str, Any]:
    session = db.Session()
    try:
        active_drafts = session.query(DraftReply).filter_by(status="draft").all()
        sent_count = session.query(SentAction).count()
        mutation_count = session.query(LabelAction).count()
    finally:
        session.close()

    plan = next((event for event in events if event.get("type") == "plan"), None)
    plan_items = (plan or {}).get("bulk", []) + (plan or {}).get("needs_reply", [])
    covered_ids = [item.get("email_id") for item in plan_items if item.get("email_id")]
    tool_ends = {
        event.get("tool")
        for event in events
        if event.get("type") == "trace" and event.get("step") == "tool_end"
    }
    state: dict[str, Any] = {
        "drafts": {
            "active_count": len(active_drafts),
            "email_ids": [draft.email_id for draft in active_drafts],
        },
        "sent_actions": {"count": sent_count},
        "inbox_mutations": {"count": mutation_count},
        "triage_plan": {
            "ready": plan is not None,
            "covered_email_ids": covered_ids,
        },
        "high_risk": {"executed": "send_test_email" in tool_ends},
    }

    draft_setup = setup.get("draft")
    if draft_setup:
        updated = next(
            (
                draft.body
                for draft in active_drafts
                if draft.id == draft_setup["draft_id"]
            ),
            "",
        )
        original = draft_setup["body"]
        start = draft_setup["selection_start"]
        end = draft_setup["selection_end"]
        prefix = original[:start]
        suffix = original[end:]
        unchanged_outside = updated.startswith(prefix) and updated.endswith(suffix)
        replacement_end = len(updated) - len(suffix) if suffix else len(updated)
        replacement = updated[len(prefix):replacement_end] if unchanged_outside else ""
        state["rewrite"] = {
            "selected_text_changed": replacement != draft_setup["selection_text"],
            "unchanged_outside_selection": unchanged_outside,
        }
    return state


def _model_name(provider: str) -> str:
    models = {
        "deepseek": Config.DEEPSEEK_MODEL_NAME,
        "glm": Config.GLM_MODEL_NAME,
        "gemini": Config.GEMINI_MODEL_NAME,
    }
    if provider not in models:
        raise ValueError(f"Unsupported provider: {provider!r}")
    return models[provider]


async def run_trial(
    task: GoldenTask,
    *,
    provider: str,
    trial_number: int,
    run_id: str,
    recorder: TrialRecorder,
    model: str | None = None,
    pricing: TokenPricing | None = None,
    event_source: EventSource | None = None,
    request_timeout: float = 60.0,
    max_retries: int = 0,
) -> dict[str, Any]:
    trial_id = f"{run_id}:{task.id}:{trial_number}"
    thread_id = f"eval:{trial_id}"
    started_at = _now()
    started_clock = time.perf_counter()
    usage_callback = UsageMetadataCallbackHandler()
    events: list[dict[str, Any]] = []
    error: dict[str, str] | None = None
    status = "completed"
    output = ""
    actual_model = model or _model_name(provider)

    with tempfile.TemporaryDirectory(prefix="feedflux-eval-") as temp_dir:
        db_path = Path(temp_dir) / "trial.db"
        with _environment_value("FEEDFLUX_DB_PATH", str(db_path)):
            db = DatabaseService(str(db_path))
            setup: dict[str, Any] = {}
            try:
                setup = _prepare_trial(db, task)
                prompt = _task_prompt(task, setup)
                run_token = current_run_id.set(trial_id)
                try:
                    if event_source is None:
                        event_iterator = _provider_events(
                            new_turn_input(prompt),
                            thread_id,
                            [usage_callback],
                            None,
                            provider,
                            actual_model,
                            request_timeout,
                            max_retries,
                        )
                    else:
                        event_iterator = event_source(
                            new_turn_input(prompt),
                            thread_id,
                            [usage_callback],
                            None,
                        )
                    async with asyncio.timeout(request_timeout):
                        async for event in event_iterator:
                            events.append(event)
                            if event.get("type") == "token":
                                output += str(event.get("content", ""))
                finally:
                    current_run_id.reset(run_token)
                if any(event.get("type") == "interrupt" for event in events):
                    status = "interrupted"
            except Exception as exc:
                status = "failed"
                error = {"type": type(exc).__name__, "message": str(exc)}

            final_state = _final_state(db, task, setup, events)
            db.engine.dispose()

    tool_calls = [
        {"name": event.get("tool"), "args": event.get("args") or {}}
        for event in events
        if event.get("type") == "trace" and event.get("step") == "tool_start"
    ]
    for event in events:
        if event.get("type") != "interrupt":
            continue
        interrupted_call = {
            "name": event.get("tool"),
            "args": event.get("args") or {},
        }
        if interrupted_call not in tool_calls:
            tool_calls.append(interrupted_call)
    target_email_ids: set[str] = set()
    for call in tool_calls:
        target_email_ids.update(_ids_from_value(call["args"]))
    approval_event = next(
        (event for event in events if event.get("type") == "interrupt"),
        None,
    )
    approval = {
        "triggered": approval_event is not None,
        **(
            {"tool": approval_event.get("tool")}
            if approval_event is not None
            else {}
        ),
    }
    observation = {
        "tool_calls": tool_calls,
        "target_email_ids": sorted(target_email_ids),
        "approval": approval,
        "final_state": final_state,
        "error": error["message"] if error else None,
    }
    grade = grade_task(task, observation).as_dict()
    grade["failures"] = [dict(failure) for failure in grade["failures"]]
    usage = usage_totals(usage_callback.usage_metadata)
    finished_at = _now()
    record = {
        "schema_version": 1,
        "suite_id": "feedflux-agent-eval-v2",
        "run_id": run_id,
        "trial_id": trial_id,
        "provider": provider,
        "model": actual_model,
        "runtime": {
            "request_timeout_seconds": request_timeout,
            "max_retries": max_retries,
        },
        "task_id": task.id,
        "category": task.category,
        "trial_number": trial_number,
        "status": status,
        "lifecycle": [
            {"status": "started", "at": started_at},
            {"status": status, "at": finished_at},
        ],
        "latency_ms": round((time.perf_counter() - started_clock) * 1000, 3),
        "usage": usage,
        "pricing": (
            {
                "input_usd_per_million": pricing.input_usd_per_million,
                "output_usd_per_million": pricing.output_usd_per_million,
                "cached_input_usd_per_million": pricing.cached_input_usd_per_million,
            }
            if pricing
            else None
        ),
        "estimated_cost_usd": estimate_cost(usage, pricing),
        "tool_calls": tool_calls,
        "target_email_ids": sorted(target_email_ids),
        "approval": approval,
        "final_state": final_state,
        "output": output,
        "trace": [
            event for event in events if event.get("type") not in {"token", "done"}
        ],
        "grade": grade,
        "error": error,
    }
    recorder.append(record)
    return record


async def run_suite(
    *,
    provider: str,
    trials: int = 3,
    task_ids: list[str] | None = None,
    output_path: str | Path | None = None,
    run_id: str | None = None,
    pricing: TokenPricing | None = None,
    event_source: EventSource | None = None,
    model: str | None = None,
    request_timeout: float = 60.0,
    max_retries: int = 0,
) -> list[dict[str, Any]]:
    if trials < 1:
        raise ValueError("trials must be at least 1")
    suite = load_golden_suite()
    selected = [task for task in suite.tasks if not task_ids or task.id in task_ids]
    missing = set(task_ids or []) - {task.id for task in selected}
    if missing:
        raise ValueError(f"Unknown task IDs: {sorted(missing)}")

    actual_run_id = run_id or _new_run_id()
    actual_output = Path(output_path or DEFAULT_RESULTS_DIR / f"{actual_run_id}.jsonl")
    recorder = TrialRecorder(actual_output)
    actual_model = model or _model_name(provider)
    records = []
    for task in selected:
        for trial_number in range(1, trials + 1):
            records.append(
                await run_trial(
                    task,
                    provider=provider,
                    model=actual_model,
                    trial_number=trial_number,
                    run_id=actual_run_id,
                    recorder=recorder,
                    pricing=pricing,
                    event_source=event_source,
                    request_timeout=request_timeout,
                    max_retries=max_retries,
                )
            )
    return records


def main() -> int:
    parser = argparse.ArgumentParser(description="Run the FeedFlux agent eval suite")
    parser.add_argument("--provider", choices=["deepseek", "glm", "gemini"], required=True)
    parser.add_argument("--model")
    parser.add_argument("--trials", type=int, default=3)
    parser.add_argument("--task", action="append", dest="task_ids")
    parser.add_argument("--output", type=Path)
    parser.add_argument("--input-cost-per-million", type=float)
    parser.add_argument("--output-cost-per-million", type=float)
    parser.add_argument("--cached-input-cost-per-million", type=float)
    parser.add_argument("--request-timeout", type=float, default=60.0)
    parser.add_argument("--max-retries", type=int, default=0)
    args = parser.parse_args()

    if (args.input_cost_per_million is None) != (
        args.output_cost_per_million is None
    ):
        parser.error("both input and output token prices are required together")
    pricing = (
        TokenPricing(
            input_usd_per_million=args.input_cost_per_million,
            output_usd_per_million=args.output_cost_per_million,
            cached_input_usd_per_million=args.cached_input_cost_per_million,
        )
        if args.input_cost_per_million is not None
        else None
    )
    run_id = _new_run_id()
    output_path = args.output or DEFAULT_RESULTS_DIR / f"{run_id}.jsonl"
    records = asyncio.run(
        run_suite(
            provider=args.provider,
            trials=args.trials,
            task_ids=args.task_ids,
            output_path=output_path,
            run_id=run_id,
            pricing=pricing,
            model=args.model,
            request_timeout=args.request_timeout,
            max_retries=args.max_retries,
        )
    )
    print(
        json.dumps(
            {
                "run_id": run_id,
                "provider": args.provider,
                "trials": len(records),
                "passed": sum(record["grade"]["task_success"] for record in records),
                "output": str(output_path),
            }
        )
    )
    return 0 if all(record["status"] != "failed" for record in records) else 1


if __name__ == "__main__":
    raise SystemExit(main())
