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


if __name__ == "__main__":
    unittest.main()
