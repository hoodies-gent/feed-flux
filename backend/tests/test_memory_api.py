import asyncio
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi import HTTPException

from app.api import semantic_memory as memory_api
from app.core.profile import LOCAL_PROFILE_ID
from app.services.database import DatabaseService
from app.services.semantic_memory_store import SemanticMemoryStore


class MemoryApiTest(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db = DatabaseService(str(Path(self.temp_dir.name) / "memory-api.db"))
        self.store = SemanticMemoryStore(self.db)
        self.db_patch = patch.object(memory_api, "db", self.db)
        self.db_patch.start()

    def tearDown(self):
        self.db_patch.stop()
        self.db.engine.dispose()
        self.temp_dir.cleanup()

    def _remember(
        self,
        *,
        key: str,
        workflow_scope: str = "drafting",
        contact_scope: str | None = None,
        profile_id: str = LOCAL_PROFILE_ID,
    ) -> dict:
        return self.store.remember(
            profile_id=profile_id,
            memory_type="preference",
            workflow_scope=workflow_scope,
            contact_scope=contact_scope,
            key=key,
            value=f"value for {key}",
            source="explicit_user",
        )

    def test_list_returns_current_local_profile_memories_with_filters(self):
        active = self._remember(key="active")
        disabled = self._remember(key="disabled", workflow_scope="triage")
        self.store.disable(profile_id=LOCAL_PROFILE_ID, memory_id=disabled["id"])
        self._remember(key="other-profile", profile_id="profile-b")

        response = asyncio.run(
            memory_api.list_semantic_memories(
                memory_type="preference",
                workflow_scope="drafting",
                contact_scope=None,
            )
        )

        self.assertEqual(1, response["count"])
        self.assertEqual([active["id"]], [item["id"] for item in response["memories"]])

        unfiltered = asyncio.run(
            memory_api.list_semantic_memories(
                memory_type=None,
                workflow_scope=None,
                contact_scope=None,
            )
        )
        self.assertEqual(
            {active["id"], disabled["id"]},
            {item["id"] for item in unfiltered["memories"]},
        )

    def test_update_supersedes_value_and_is_immediately_retrievable(self):
        original = self._remember(key="reply-tone")

        updated = asyncio.run(
            memory_api.update_semantic_memory(
                original["id"],
                memory_api.MemoryUpdateRequest(value="Use a warm and concise tone."),
            )
        )
        retrieved = self.store.retrieve_active(
            profile_id=LOCAL_PROFILE_ID,
            workflow_scope="drafting",
            contact_scope=None,
        )

        self.assertEqual(2, updated["version"])
        self.assertEqual("management_ui", updated["source"])
        self.assertEqual("Use a warm and concise tone.", updated["value"])
        self.assertEqual([updated["id"]], [item["id"] for item in retrieved])

    def test_disable_and_enable_take_effect_immediately(self):
        memory = self._remember(key="reply-tone")

        disabled = asyncio.run(memory_api.disable_semantic_memory(memory["id"]))
        after_disable = self.store.retrieve_active(
            profile_id=LOCAL_PROFILE_ID,
            workflow_scope="drafting",
            contact_scope=None,
        )
        enabled = asyncio.run(memory_api.enable_semantic_memory(memory["id"]))
        after_enable = self.store.retrieve_active(
            profile_id=LOCAL_PROFILE_ID,
            workflow_scope="drafting",
            contact_scope=None,
        )

        self.assertEqual("disabled", disabled["status"])
        self.assertEqual([], after_disable)
        self.assertEqual("active", enabled["status"])
        self.assertEqual([memory["id"]], [item["id"] for item in after_enable])

    def test_delete_scrubs_lineage_and_hides_other_profiles(self):
        memory = self._remember(key="reply-tone")
        updated = self.store.update(
            profile_id=LOCAL_PROFILE_ID,
            memory_id=memory["id"],
            value="replacement",
            source="management_ui",
        )
        other = self._remember(key="other-profile", profile_id="profile-b")

        with self.assertRaises(HTTPException) as context:
            asyncio.run(memory_api.delete_semantic_memory(other["id"]))
        self.assertEqual(404, context.exception.status_code)

        deleted = asyncio.run(memory_api.delete_semantic_memory(updated["id"]))

        self.assertEqual(2, deleted["forgotten_count"])
        self.assertEqual([], self.store.list_memories(profile_id=LOCAL_PROFILE_ID))
        history = self.store.list_memories(
            profile_id=LOCAL_PROFILE_ID,
            include_history=True,
        )
        self.assertTrue(all(item["value"] is None for item in history))

    def test_clear_applies_filters_without_crossing_profile_boundary(self):
        self._remember(key="drafting")
        retained = self._remember(key="triage", workflow_scope="triage")
        other = self._remember(key="other-profile", profile_id="profile-b")

        result = asyncio.run(
            memory_api.clear_semantic_memories(
                memory_type=None,
                workflow_scope="drafting",
                contact_scope=None,
            )
        )

        self.assertEqual(1, result["forgotten_count"])
        self.assertEqual(
            [retained["id"]],
            [item["id"] for item in self.store.list_memories(profile_id=LOCAL_PROFILE_ID)],
        )
        self.assertEqual(
            [other["id"]],
            [item["id"] for item in self.store.list_memories(profile_id="profile-b")],
        )


if __name__ == "__main__":
    unittest.main()
