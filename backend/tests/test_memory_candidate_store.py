import tempfile
import unittest
from pathlib import Path

from app.services.database import DatabaseService


class MemoryCandidateStoreTest(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.database = DatabaseService(
            str(Path(self.temp_dir.name) / "memory-candidates.db")
        )

    def tearDown(self):
        self.database.engine.dispose()
        self.temp_dir.cleanup()

    def _suggest_candidate(
        self,
        store,
        *,
        profile_id="profile-a",
        value="Keep replies concise.",
    ):
        for thread_id in ("thread-1", "thread-2"):
            candidate = store.record_candidate(
                profile_id=profile_id,
                memory_type="preference",
                workflow_scope="drafting",
                contact_scope=None,
                key="Reply length",
                value=value,
                source="agent_correction",
                source_ref=thread_id,
            )
        return candidate

    def test_distinct_threads_are_required_before_candidate_is_suggested(self):
        try:
            from app.services.memory_candidate_store import MemoryCandidateStore
        except ModuleNotFoundError:
            self.fail("memory candidate store is not implemented")

        store = MemoryCandidateStore(self.database)
        first = store.record_candidate(
            profile_id="profile-a",
            memory_type="preference",
            workflow_scope="drafting",
            contact_scope=None,
            key="Reply length",
            value="Keep replies concise.",
            source="agent_correction",
            source_ref="thread-1",
        )
        replay = store.record_candidate(
            profile_id="profile-a",
            memory_type="PREFERENCE",
            workflow_scope="DRAFTING",
            contact_scope=None,
            key="  reply   length ",
            value=" Keep replies concise. ",
            source="agent_correction",
            source_ref="thread-1",
        )
        suggested = store.record_candidate(
            profile_id="profile-a",
            memory_type="preference",
            workflow_scope="drafting",
            contact_scope=None,
            key="Reply length",
            value="Keep replies concise.",
            source="agent_correction",
            source_ref="thread-2",
        )

        self.assertEqual("pending", first["status"])
        self.assertEqual(1, first["evidence_count"])
        self.assertEqual(first["id"], replay["id"])
        self.assertEqual(1, replay["evidence_count"])
        self.assertEqual("suggested", suggested["status"])
        self.assertEqual(2, suggested["evidence_count"])
        self.assertIsNotNone(suggested["suggested_at"])

    def test_listing_candidates_is_isolated_by_profile_and_status(self):
        from app.services.memory_candidate_store import MemoryCandidateStore

        store = MemoryCandidateStore(self.database)
        pending = store.record_candidate(
            profile_id="profile-a",
            memory_type="preference",
            workflow_scope="drafting",
            contact_scope=None,
            key="Reply length",
            value="Keep replies concise.",
            source="agent_correction",
            source_ref="thread-a1",
        )
        store.record_candidate(
            profile_id="profile-b",
            memory_type="preference",
            workflow_scope="drafting",
            contact_scope=None,
            key="Reply length",
            value="Keep replies concise.",
            source="agent_correction",
            source_ref="thread-b1",
        )
        suggested = store.record_candidate(
            profile_id="profile-b",
            memory_type="preference",
            workflow_scope="drafting",
            contact_scope=None,
            key="Reply length",
            value="Keep replies concise.",
            source="agent_correction",
            source_ref="thread-b2",
        )

        self.assertEqual(
            [pending],
            store.list_candidates(profile_id="profile-a", status="pending"),
        )
        self.assertEqual([], store.list_candidates(profile_id="profile-a", status="suggested"))
        self.assertEqual(
            [suggested],
            store.list_candidates(profile_id="profile-b", status="suggested"),
        )

    def test_rejected_candidate_cannot_be_reopened_by_new_evidence(self):
        from app.services.memory_candidate_store import MemoryCandidateStore

        store = MemoryCandidateStore(self.database)
        store.record_candidate(
            profile_id="profile-a",
            memory_type="preference",
            workflow_scope="drafting",
            contact_scope=None,
            key="Reply length",
            value="Keep replies concise.",
            source="agent_correction",
            source_ref="thread-1",
        )
        suggested = store.record_candidate(
            profile_id="profile-a",
            memory_type="preference",
            workflow_scope="drafting",
            contact_scope=None,
            key="Reply length",
            value="Keep replies concise.",
            source="agent_correction",
            source_ref="thread-2",
        )

        with self.assertRaisesRegex(KeyError, "not found"):
            store.reject(profile_id="profile-b", candidate_id=suggested["id"])

        rejected = store.reject(
            profile_id="profile-a",
            candidate_id=suggested["id"],
        )
        replay = store.record_candidate(
            profile_id="profile-a",
            memory_type="preference",
            workflow_scope="drafting",
            contact_scope=None,
            key="Reply length",
            value="Keep replies concise.",
            source="agent_correction",
            source_ref="thread-3",
        )

        self.assertEqual("rejected", rejected["status"])
        self.assertIsNotNone(rejected["rejected_at"])
        self.assertEqual("rejected", replay["status"])
        self.assertEqual(2, replay["evidence_count"])

    def test_expired_candidate_cannot_be_reopened_by_new_evidence(self):
        from app.services.memory_candidate_store import MemoryCandidateStore

        store = MemoryCandidateStore(self.database)
        pending = store.record_candidate(
            profile_id="profile-a",
            memory_type="preference",
            workflow_scope="drafting",
            contact_scope=None,
            key="Reply length",
            value="Keep replies concise.",
            source="agent_correction",
            source_ref="thread-1",
        )

        expired = store.expire(
            profile_id="profile-a",
            candidate_id=pending["id"],
        )
        replay = store.record_candidate(
            profile_id="profile-a",
            memory_type="preference",
            workflow_scope="drafting",
            contact_scope=None,
            key="Reply length",
            value="Keep replies concise.",
            source="agent_correction",
            source_ref="thread-2",
        )

        self.assertEqual("expired", expired["status"])
        self.assertIsNotNone(expired["expired_at"])
        self.assertEqual("expired", replay["status"])
        self.assertEqual(1, replay["evidence_count"])

    def test_confirm_promotes_suggested_candidate_to_active_memory(self):
        from app.services.memory_candidate_store import MemoryCandidateStore
        from app.services.semantic_memory_store import SemanticMemoryStore

        candidate_store = MemoryCandidateStore(self.database)
        candidate = self._suggest_candidate(candidate_store)

        confirmed = candidate_store.confirm(
            profile_id="profile-a",
            candidate_id=candidate["id"],
        )
        memories = SemanticMemoryStore(self.database).list_memories(
            profile_id="profile-a"
        )

        self.assertEqual("confirmed", confirmed["status"])
        self.assertIsNotNone(confirmed["confirmed_at"])
        self.assertEqual(1, len(memories))
        self.assertEqual("active", memories[0]["status"])
        self.assertEqual("Keep replies concise.", memories[0]["value"])
        self.assertEqual("candidate_confirmation", memories[0]["source"])
        self.assertEqual(f"candidate:{candidate['id']}", memories[0]["source_ref"])

    def test_confirm_requires_suggested_candidate_owned_by_profile(self):
        from app.services.memory_candidate_store import MemoryCandidateStore

        store = MemoryCandidateStore(self.database)
        pending = store.record_candidate(
            profile_id="profile-a",
            memory_type="preference",
            workflow_scope="drafting",
            contact_scope=None,
            key="Reply length",
            value="Keep replies concise.",
            source="agent_correction",
            source_ref="thread-1",
        )

        with self.assertRaisesRegex(KeyError, "not found"):
            store.confirm(profile_id="profile-b", candidate_id=pending["id"])
        with self.assertRaisesRegex(ValueError, "suggested"):
            store.confirm(profile_id="profile-a", candidate_id=pending["id"])

    def test_confirm_uses_conflict_rule_and_is_idempotent(self):
        from app.services.memory_candidate_store import MemoryCandidateStore
        from app.services.semantic_memory_store import SemanticMemoryStore

        memory_store = SemanticMemoryStore(self.database)
        original = memory_store.remember(
            profile_id="profile-a",
            memory_type="preference",
            workflow_scope="drafting",
            contact_scope=None,
            key="Reply length",
            value="Write detailed replies.",
            source="explicit_user",
            source_ref="thread-original",
        )
        candidate_store = MemoryCandidateStore(self.database)
        candidate = self._suggest_candidate(candidate_store)

        first = candidate_store.confirm(
            profile_id="profile-a",
            candidate_id=candidate["id"],
        )
        replay = candidate_store.confirm(
            profile_id="profile-a",
            candidate_id=candidate["id"],
        )

        self.assertEqual(first, replay)
        history = memory_store.list_memories(
            profile_id="profile-a",
            include_history=True,
        )
        self.assertEqual(2, len(history))
        self.assertEqual(["superseded", "active"], [item["status"] for item in history])
        self.assertEqual(original["id"], history[1]["supersedes_id"])
        self.assertEqual("Keep replies concise.", history[1]["value"])

    def test_forget_scrubs_confirmed_candidate_and_deletes_its_evidence(self):
        from app.models.semantic_memory import (
            SemanticMemoryCandidate,
            SemanticMemoryCandidateEvidence,
        )
        from app.services.memory_candidate_store import MemoryCandidateStore
        from app.services.semantic_memory_store import SemanticMemoryStore

        candidate_store = MemoryCandidateStore(self.database)
        candidate = self._suggest_candidate(candidate_store)
        candidate_store.confirm(
            profile_id="profile-a",
            candidate_id=candidate["id"],
        )
        memory_store = SemanticMemoryStore(self.database)
        memory = memory_store.list_memories(profile_id="profile-a")[0]

        memory_store.forget(profile_id="profile-a", memory_id=memory["id"])

        session = self.database.Session()
        try:
            scrubbed = session.get(SemanticMemoryCandidate, candidate["id"])
            evidence_count = (
                session.query(SemanticMemoryCandidateEvidence)
                .filter_by(candidate_id=candidate["id"])
                .count()
            )
        finally:
            session.close()
        self.assertEqual("forgotten", scrubbed.status)
        self.assertEqual("", scrubbed.workflow_scope)
        self.assertEqual("", scrubbed.contact_scope)
        self.assertEqual("", scrubbed.key)
        self.assertEqual(f"forgotten:{candidate['id']}", scrubbed.normalized_key)
        self.assertEqual("", scrubbed.value)
        self.assertEqual("", scrubbed.normalized_value)
        self.assertEqual(0, scrubbed.evidence_count)
        self.assertEqual(0, evidence_count)

    def test_reset_scrubs_only_matching_profile_confirmed_candidates(self):
        from app.models.semantic_memory import SemanticMemoryCandidate
        from app.services.memory_candidate_store import MemoryCandidateStore
        from app.services.semantic_memory_store import SemanticMemoryStore

        candidate_store = MemoryCandidateStore(self.database)
        candidate_a = self._suggest_candidate(candidate_store, profile_id="profile-a")
        candidate_b = self._suggest_candidate(candidate_store, profile_id="profile-b")
        candidate_store.confirm(
            profile_id="profile-a",
            candidate_id=candidate_a["id"],
        )
        candidate_store.confirm(
            profile_id="profile-b",
            candidate_id=candidate_b["id"],
        )

        SemanticMemoryStore(self.database).reset(profile_id="profile-a")

        session = self.database.Session()
        try:
            scrubbed_a = session.get(SemanticMemoryCandidate, candidate_a["id"])
            retained_b = session.get(SemanticMemoryCandidate, candidate_b["id"])
        finally:
            session.close()
        self.assertEqual("forgotten", scrubbed_a.status)
        self.assertEqual("", scrubbed_a.value)
        self.assertEqual("confirmed", retained_b.status)
        self.assertEqual("Keep replies concise.", retained_b.value)

    def test_same_value_accept_is_scrubbed_when_existing_memory_is_forgotten(self):
        from app.models.semantic_memory import (
            SemanticMemoryCandidate,
            SemanticMemoryCandidateEvidence,
        )
        from app.services.memory_candidate_store import MemoryCandidateStore
        from app.services.semantic_memory_store import SemanticMemoryStore

        memory_store = SemanticMemoryStore(self.database)
        existing = memory_store.remember(
            profile_id="profile-a",
            memory_type="preference",
            workflow_scope="drafting",
            contact_scope=None,
            key="Reply length",
            value="Keep replies concise.",
            source="explicit_user",
        )
        candidate_store = MemoryCandidateStore(self.database)
        candidate = self._suggest_candidate(candidate_store)
        candidate_store.confirm(
            profile_id="profile-a",
            candidate_id=candidate["id"],
        )

        self.assertEqual(
            [existing],
            memory_store.list_memories(profile_id="profile-a"),
        )
        memory_store.forget(profile_id="profile-a", memory_id=existing["id"])

        session = self.database.Session()
        try:
            scrubbed = session.get(SemanticMemoryCandidate, candidate["id"])
            evidence_count = (
                session.query(SemanticMemoryCandidateEvidence)
                .filter_by(candidate_id=candidate["id"])
                .count()
            )
        finally:
            session.close()
        self.assertEqual("forgotten", scrubbed.status)
        self.assertEqual("", scrubbed.value)
        self.assertEqual(0, evidence_count)

    def test_same_value_accept_is_scrubbed_when_existing_memory_is_reset(self):
        from app.models.semantic_memory import SemanticMemoryCandidate
        from app.services.memory_candidate_store import MemoryCandidateStore
        from app.services.semantic_memory_store import SemanticMemoryStore

        memory_store = SemanticMemoryStore(self.database)
        memory_store.remember(
            profile_id="profile-a",
            memory_type="preference",
            workflow_scope="drafting",
            contact_scope=None,
            key="Reply length",
            value="Keep replies concise.",
            source="explicit_user",
        )
        candidate_store = MemoryCandidateStore(self.database)
        candidate = self._suggest_candidate(candidate_store)
        candidate_store.confirm(
            profile_id="profile-a",
            candidate_id=candidate["id"],
        )

        memory_store.reset(
            profile_id="profile-a",
            memory_type="preference",
            workflow_scope="drafting",
        )

        session = self.database.Session()
        try:
            scrubbed = session.get(SemanticMemoryCandidate, candidate["id"])
        finally:
            session.close()
        self.assertEqual("forgotten", scrubbed.status)
        self.assertEqual("", scrubbed.value)


if __name__ == "__main__":
    unittest.main()
