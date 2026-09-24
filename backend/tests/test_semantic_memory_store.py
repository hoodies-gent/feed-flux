import tempfile
import unittest
from pathlib import Path

from app.services.database import DatabaseService


class SemanticMemoryStoreTest(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.database = DatabaseService(
            str(Path(self.temp_dir.name) / "semantic-memory.db")
        )

    def tearDown(self):
        self.database.engine.dispose()
        self.temp_dir.cleanup()

    def test_remember_and_list_are_isolated_by_profile(self):
        try:
            from app.services.semantic_memory_store import SemanticMemoryStore
        except ModuleNotFoundError:
            self.fail("semantic memory store is not implemented")

        store = SemanticMemoryStore(self.database)
        remembered = store.remember(
            profile_id="profile-a",
            memory_type="preference",
            workflow_scope="drafting",
            contact_scope="pat@example.com",
            key="Reply Tone",
            value="Be concise.",
            source="explicit_user",
            source_ref="thread-1",
        )

        self.assertEqual("active", remembered["status"])
        self.assertEqual("reply tone", remembered["normalized_key"])
        self.assertEqual([remembered], store.list_memories(profile_id="profile-a"))
        self.assertEqual([], store.list_memories(profile_id="profile-b"))

    def test_same_value_is_noop_and_new_value_supersedes_current(self):
        from app.services.semantic_memory_store import SemanticMemoryStore

        store = SemanticMemoryStore(self.database)
        original = store.remember(
            profile_id="profile-a",
            memory_type="preference",
            workflow_scope="drafting",
            contact_scope=None,
            key="Reply Tone",
            value="Be concise.",
            source="explicit_user",
            source_ref="thread-1",
        )

        try:
            replay = store.remember(
                profile_id="profile-a",
                memory_type="PREFERENCE",
                workflow_scope="DRAFTING",
                contact_scope=None,
                key="  reply   tone ",
                value="Be concise.",
                source="explicit_user",
                source_ref="thread-2",
            )
            replacement = store.remember(
                profile_id="profile-a",
                memory_type="preference",
                workflow_scope="drafting",
                contact_scope=None,
                key="Reply Tone",
                value="Use a warm, detailed tone.",
                source="management_ui",
                source_ref="memory-dialog",
            )
        except Exception as exc:
            self.fail(f"remember conflict rule is not implemented: {exc}")

        self.assertEqual(original["id"], replay["id"])
        self.assertEqual(2, replacement["version"])
        self.assertEqual(original["id"], replacement["supersedes_id"])
        self.assertEqual(original["lineage_id"], replacement["lineage_id"])
        self.assertEqual("management_ui", replacement["source"])
        self.assertEqual(
            [replacement],
            store.list_memories(profile_id="profile-a"),
        )

        history = store.list_memories(profile_id="profile-a", include_history=True)
        self.assertEqual(["superseded", "active"], [item["status"] for item in history])
        self.assertEqual(
            ["explicit_user", "management_ui"],
            [item["source"] for item in history],
        )

    def test_update_disable_and_enable_enforce_current_state(self):
        from app.services.semantic_memory_store import SemanticMemoryStore

        store = SemanticMemoryStore(self.database)
        original = store.remember(
            profile_id="profile-a",
            memory_type="constraint",
            workflow_scope="scheduling",
            contact_scope=None,
            key="Meeting window",
            value="After 10am.",
            source="explicit_user",
        )

        updated = store.update(
            profile_id="profile-a",
            memory_id=original["id"],
            value="After 11am.",
            source="management_ui",
            source_ref="memory-dialog",
        )
        self.assertEqual(2, updated["version"])
        self.assertEqual("After 11am.", updated["value"])
        self.assertEqual("Meeting window", updated["key"])

        with self.assertRaisesRegex(ValueError, "current"):
            store.update(
                profile_id="profile-a",
                memory_id=original["id"],
                value="After noon.",
                source="management_ui",
            )
        with self.assertRaisesRegex(ValueError, "disabled"):
            store.enable(profile_id="profile-a", memory_id=updated["id"])

        disabled = store.disable(profile_id="profile-a", memory_id=updated["id"])
        self.assertEqual("disabled", disabled["status"])
        self.assertIsNotNone(disabled["disabled_at"])

        enabled = store.enable(profile_id="profile-a", memory_id=updated["id"])
        self.assertEqual("active", enabled["status"])
        self.assertIsNone(enabled["disabled_at"])

        with self.assertRaisesRegex(ValueError, "active"):
            store.disable(profile_id="profile-a", memory_id=original["id"])

    def test_forget_scrubs_lineage_without_crossing_profile_boundary(self):
        from app.services.semantic_memory_store import SemanticMemoryStore

        store = SemanticMemoryStore(self.database)
        original = store.remember(
            profile_id="profile-a",
            memory_type="preference",
            workflow_scope="drafting",
            contact_scope="pat@example.com",
            key="Reply tone",
            value="Be concise.",
            source="explicit_user",
            source_ref="thread-secret",
        )
        current = store.update(
            profile_id="profile-a",
            memory_id=original["id"],
            value="Be warm and concise.",
            source="management_ui",
            source_ref="dialog-secret",
        )

        with self.assertRaisesRegex(KeyError, "not found"):
            store.forget(profile_id="profile-b", memory_id=current["id"])
        self.assertEqual(1, len(store.list_memories(profile_id="profile-a")))

        result = store.forget(profile_id="profile-a", memory_id=current["id"])
        self.assertEqual(2, result["forgotten_count"])
        self.assertEqual([], store.list_memories(profile_id="profile-a"))

        history = store.list_memories(profile_id="profile-a", include_history=True)
        self.assertEqual(["forgotten", "forgotten"], [item["status"] for item in history])
        for item in history:
            self.assertIsNone(item["key"])
            self.assertIsNone(item["normalized_key"])
            self.assertIsNone(item["value"])
            self.assertIsNone(item["contact_scope"])
            self.assertIsNone(item["workflow_scope"])
            self.assertIsNone(item["source_ref"])
            self.assertIsNotNone(item["forgotten_at"])
        self.assertEqual(
            ["explicit_user", "management_ui"],
            [item["source"] for item in history],
        )

    def test_reset_scrubs_only_matching_profile_memories(self):
        from app.services.semantic_memory_store import SemanticMemoryStore

        store = SemanticMemoryStore(self.database)
        store.remember(
            profile_id="profile-a",
            memory_type="preference",
            workflow_scope="triage",
            contact_scope=None,
            key="Urgent sender",
            value="Treat finance@example.com as urgent.",
            source="explicit_user",
        )
        retained = store.remember(
            profile_id="profile-a",
            memory_type="fact",
            workflow_scope="drafting",
            contact_scope=None,
            key="Role",
            value="Pat is my manager.",
            source="explicit_user",
        )
        other_profile = store.remember(
            profile_id="profile-b",
            memory_type="preference",
            workflow_scope="triage",
            contact_scope=None,
            key="Urgent sender",
            value="Treat legal@example.com as urgent.",
            source="explicit_user",
        )

        result = store.reset(profile_id="profile-a", memory_type="preference")

        self.assertEqual(1, result["forgotten_count"])
        self.assertEqual([retained], store.list_memories(profile_id="profile-a"))
        self.assertEqual([other_profile], store.list_memories(profile_id="profile-b"))

    def test_remembering_disabled_value_reactivates_as_new_version(self):
        from app.services.semantic_memory_store import SemanticMemoryStore

        store = SemanticMemoryStore(self.database)
        original = store.remember(
            profile_id="profile-a",
            memory_type="preference",
            workflow_scope="drafting",
            contact_scope=None,
            key="Reply tone",
            value="Be concise.",
            source="explicit_user",
        )
        store.disable(profile_id="profile-a", memory_id=original["id"])

        reactivated = store.remember(
            profile_id="profile-a",
            memory_type="preference",
            workflow_scope="drafting",
            contact_scope=None,
            key="Reply tone",
            value="Be concise.",
            source="explicit_user",
            source_ref="thread-2",
        )

        self.assertNotEqual(original["id"], reactivated["id"])
        self.assertEqual(2, reactivated["version"])
        self.assertEqual("active", reactivated["status"])
        history = store.list_memories(profile_id="profile-a", include_history=True)
        self.assertEqual(["superseded", "active"], [item["status"] for item in history])


if __name__ == "__main__":
    unittest.main()
