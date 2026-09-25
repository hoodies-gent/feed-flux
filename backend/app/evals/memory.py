import json
import tempfile
from pathlib import Path
from typing import Any

from langchain_core.messages import HumanMessage

from app.agent.memory_context import resolve_memory_context
from app.models.semantic_memory import (
    SemanticMemory,
    SemanticMemoryCandidate,
    SemanticMemoryCandidateEvidence,
)
from app.services.database import DatabaseService
from app.services.memory_candidate_store import MemoryCandidateStore
from app.services.semantic_memory_store import SemanticMemoryStore


PROFILE_ID = "eval-profile-a"
OTHER_PROFILE_ID = "eval-profile-b"


def _rate(passed: int, total: int, definition: str) -> dict[str, Any]:
    return {
        "passed": passed,
        "total": total,
        "rate": round(passed / total, 4) if total else None,
        "definition": definition,
    }


def run_memory_eval() -> dict[str, Any]:
    with tempfile.TemporaryDirectory() as temp_dir:
        database = DatabaseService(str(Path(temp_dir) / "memory-eval.db"))
        try:
            metrics = _run_cases(database)
        finally:
            database.engine.dispose()
    return {
        "schema_version": 1,
        "suite_id": "feedflux-semantic-memory-v1",
        "provider": "deterministic",
        "metrics": metrics,
    }


def _run_cases(database: DatabaseService) -> dict[str, Any]:
    memory_store = SemanticMemoryStore(database)
    candidate_store = MemoryCandidateStore(database)
    database.insert_email(
        {
            "id": "memory-eval-focused-email",
            "subject": "Fixture drafting request",
            "sender_name": "Fixture contact",
            "sender_email": "pat@example.com",
            "received_datetime": 1,
        }
    )

    drafting_global = memory_store.remember(
        profile_id=PROFILE_ID,
        memory_type="preference",
        workflow_scope="drafting",
        contact_scope=None,
        key="Reply length",
        value="Keep replies concise.",
        source="eval_fixture",
    )
    drafting_contact = memory_store.remember(
        profile_id=PROFILE_ID,
        memory_type="preference",
        workflow_scope="drafting",
        contact_scope="pat@example.com",
        key="Contact tone",
        value="Use a warm tone for this contact.",
        source="eval_fixture",
    )
    memory_store.remember(
        profile_id=PROFILE_ID,
        memory_type="rule",
        workflow_scope="triage",
        contact_scope=None,
        key="Triage rule",
        value="Archive fixture newsletters.",
        source="eval_fixture",
    )
    memory_store.remember(
        profile_id=PROFILE_ID,
        memory_type="preference",
        workflow_scope="drafting",
        contact_scope="other@example.com",
        key="Other contact tone",
        value="Use a formal tone for another contact.",
        source="eval_fixture",
    )
    memory_store.remember(
        profile_id=OTHER_PROFILE_ID,
        memory_type="preference",
        workflow_scope="drafting",
        contact_scope=None,
        key="Private profile rule",
        value="Private profile preference",
        source="eval_fixture",
    )

    conflict_old = memory_store.remember(
        profile_id=PROFILE_ID,
        memory_type="preference",
        workflow_scope="drafting",
        contact_scope=None,
        key="Signature style",
        value="Use the old fixture signature.",
        source="eval_fixture",
    )
    conflict_new = memory_store.remember(
        profile_id=PROFILE_ID,
        memory_type="preference",
        workflow_scope="drafting",
        contact_scope=None,
        key="Signature style",
        value="Use the current fixture signature.",
        source="eval_fixture",
    )
    conflict_history = [
        item
        for item in memory_store.list_memories(
            profile_id=PROFILE_ID,
            include_history=True,
        )
        if item["lineage_id"] == conflict_old["lineage_id"]
    ]
    conflict_passed = int(
        [item["status"] for item in conflict_history] == ["superseded", "active"]
        and conflict_new["supersedes_id"] == conflict_old["id"]
    )

    forgotten = memory_store.remember(
        profile_id=PROFILE_ID,
        memory_type="preference",
        workflow_scope="drafting",
        contact_scope=None,
        key="Temporary fixture preference",
        value="Forget this fixture value.",
        source="eval_fixture",
    )
    memory_store.forget(profile_id=PROFILE_ID, memory_id=forgotten["id"])
    forgotten_history = [
        item
        for item in memory_store.list_memories(
            profile_id=PROFILE_ID,
            include_history=True,
        )
        if item["lineage_id"] == forgotten["lineage_id"]
    ]
    forget_passed = int(
        len(forgotten_history) == 1
        and forgotten_history[0]["status"] == "forgotten"
        and forgotten_history[0]["value"] is None
    )

    candidate_args = {
        "profile_id": PROFILE_ID,
        "memory_type": "preference",
        "workflow_scope": "drafting",
        "contact_scope": None,
        "key": "Punctuation style",
        "value": "Never use exclamation marks",
        "source": "agent_correction",
    }
    candidate_store.record_candidate(**candidate_args, source_ref="candidate-thread-1")
    candidate_store.record_candidate(**candidate_args, source_ref="candidate-thread-1")
    suggested_candidate = candidate_store.record_candidate(
        **candidate_args,
        source_ref="candidate-thread-2",
    )
    candidate_store.record_candidate(
        profile_id=PROFILE_ID,
        memory_type="preference",
        workflow_scope="drafting",
        contact_scope=None,
        key="Greeting style",
        value="Use a fixture greeting.",
        source="agent_correction",
        source_ref="noise-thread-1",
    )
    candidate_store.record_candidate(
        profile_id=PROFILE_ID,
        memory_type="preference",
        workflow_scope="drafting",
        contact_scope=None,
        key="Punctuation style",
        value="Always use exclamation marks.",
        source="agent_correction",
        source_ref="conflict-thread-1",
    )
    for source_ref in ("other-profile-thread-1", "other-profile-thread-2"):
        candidate_store.record_candidate(
            profile_id=OTHER_PROFILE_ID,
            memory_type="preference",
            workflow_scope="drafting",
            contact_scope=None,
            key="Other profile candidate",
            value="Do not expose this candidate.",
            source="agent_correction",
            source_ref=source_ref,
        )

    suggestions = candidate_store.list_candidates(
        profile_id=PROFILE_ID,
        status="suggested",
    )
    relevant_suggestions = sum(
        item["id"] == suggested_candidate["id"] for item in suggestions
    )
    candidate_precision = (
        relevant_suggestions / len(suggestions) if suggestions else None
    )

    session = database.Session()
    try:
        duplicate_identity_count = (
            session.query(SemanticMemoryCandidate)
            .filter_by(
                profile_id=PROFILE_ID,
                normalized_key=suggested_candidate["normalized_key"],
                normalized_value="never use exclamation marks",
            )
            .count()
        )
        evidence_count = (
            session.query(SemanticMemoryCandidateEvidence)
            .filter_by(candidate_id=suggested_candidate["id"])
            .count()
        )
        evaluated_candidates = (
            session.query(SemanticMemoryCandidate)
            .filter_by(profile_id=PROFILE_ID)
            .count()
        )
        false_promotions = (
            session.query(SemanticMemory)
            .filter_by(
                profile_id=PROFILE_ID,
                source="candidate_confirmation",
            )
            .count()
        )
    finally:
        session.close()

    duplicate_merge_passed = int(
        duplicate_identity_count == 1
        and evidence_count == 2
        and suggested_candidate["evidence_count"] == 2
    )
    candidate_store.confirm(
        profile_id=PROFILE_ID,
        candidate_id=suggested_candidate["id"],
    )
    candidate_memory = next(
        item
        for item in memory_store.list_memories(profile_id=PROFILE_ID)
        if item["source_ref"] == f"candidate:{suggested_candidate['id']}"
    )

    expected_global = {
        drafting_global["id"],
        conflict_new["id"],
        candidate_memory["id"],
    }
    expected_contact = {*expected_global, drafting_contact["id"]}
    application_cases = (
        ("Draft a reply to this email", ["memory-eval-focused-email"], expected_contact),
        ("Rewrite the reply for this email", ["memory-eval-focused-email"], expected_contact),
        ("Draft a general reply", [], expected_global),
    )
    application_passed = 0
    relevant_retrieved = 0
    total_retrieved = 0
    leaked = sum(item["profile_id"] != PROFILE_ID for item in suggestions)
    token_samples = []
    for prompt, context_email_ids, expected_ids in application_cases:
        resolved = resolve_memory_context(
            [HumanMessage(content=prompt)],
            context_email_ids=context_email_ids,
            profile_id=PROFILE_ID,
            database=database,
        )
        retrieved_ids = set(resolved["memory_ids"])
        application_passed += int(retrieved_ids == expected_ids)
        relevant_retrieved += len(retrieved_ids.intersection(expected_ids))
        total_retrieved += len(retrieved_ids)
        leaked += sum(
            item["profile_id"] != PROFILE_ID for item in resolved["memories"]
        )
        token_samples.append(resolved["estimated_tokens"])

    application_total = len(application_cases)
    observed_scoped_items = total_retrieved + len(suggestions)
    mean_tokens = sum(token_samples) / len(token_samples)
    return {
        "preference_application_accuracy": {
            **_rate(
                application_passed,
                application_total,
                "Relevant confirmed preferences exactly match the structured Agent context.",
            ),
            "measurement": "structured_context_injection_proxy",
        },
        "relevant_retrieval_precision": {
            "relevant": relevant_retrieved,
            "retrieved": total_retrieved,
            "rate": round(relevant_retrieved / total_retrieved, 4),
            "definition": "Share of injected memories relevant to the evaluated workflow/contact.",
        },
        "conflict_correctness": _rate(
            conflict_passed,
            1,
            "A conflicting value leaves exactly one active successor and one superseded predecessor.",
        ),
        "forget_correctness": _rate(
            forget_passed,
            1,
            "Forgotten lineage is inactive and its stored value is scrubbed.",
        ),
        "cross_profile_leakage": {
            "leaked": leaked,
            "observed": observed_scoped_items,
            "rate": round(leaked / observed_scoped_items, 4),
            "definition": "Share of retrieved memories or suggestions owned by another profile; lower is better.",
        },
        "injection_token_overhead": {
            "samples": len(token_samples),
            "mean_estimated_tokens": round(mean_tokens, 2),
            "max_estimated_tokens": max(token_samples),
            "definition": "Characters in structured memory context divided by four.",
        },
        "candidate_precision": {
            "relevant": relevant_suggestions,
            "suggested": len(suggestions),
            "rate": round(candidate_precision, 4) if candidate_precision is not None else None,
            "definition": "Share of surfaced suggestions backed by the intended repeated signal.",
        },
        "candidate_duplicate_merge_correctness": _rate(
            duplicate_merge_passed,
            1,
            "Same-thread replay is deduplicated while two distinct threads produce one candidate with two evidence rows.",
        ),
        "false_promotion": {
            "count": false_promotions,
            "evaluated": evaluated_candidates,
            "rate": round(false_promotions / evaluated_candidates, 4),
            "definition": "Candidates promoted to confirmed memory before explicit acceptance; lower is better.",
        },
    }


def main() -> int:
    print(json.dumps(run_memory_eval(), indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
