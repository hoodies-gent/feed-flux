import tempfile
import unittest
from pathlib import Path

from app.models.email import DraftReply
from app.services.database import DatabaseService


class ToolExecutionStoreTest(unittest.TestCase):
    def test_same_idempotency_key_replays_result_without_duplicate_side_effect(self):
        try:
            from app.services.tool_execution_store import ToolExecutionStore
        except ModuleNotFoundError:
            self.fail("tool execution ledger is not implemented")

        with tempfile.TemporaryDirectory() as temp_dir:
            db = DatabaseService(str(Path(temp_dir) / "runtime.db"))
            store = ToolExecutionStore(db)

            def create_draft(session):
                draft = DraftReply(
                    thread_id="fixture-thread",
                    email_id="fixture-email",
                    recipient="recipient@example.com",
                    subject="Fixture subject",
                    body="Fixture body",
                )
                session.add(draft)
                session.flush()
                return {"draft_id": draft.id, "status": "draft"}

            first_result, first_executed = store.execute_once(
                idempotency_key="fixture-key",
                operation="send_reply",
                request_payload={"email_id": "fixture-email", "body": "Fixture body"},
                execute=create_draft,
            )
            db.engine.dispose()

            reopened_db = DatabaseService(str(Path(temp_dir) / "runtime.db"))
            reopened_store = ToolExecutionStore(reopened_db)
            replayed_result, replayed_executed = reopened_store.execute_once(
                idempotency_key="fixture-key",
                operation="send_reply",
                request_payload={"body": "Fixture body", "email_id": "fixture-email"},
                execute=create_draft,
            )

            session = reopened_db.Session()
            try:
                draft_count = session.query(DraftReply).count()
            finally:
                session.close()
                reopened_db.engine.dispose()

        self.assertTrue(first_executed)
        self.assertFalse(replayed_executed)
        self.assertEqual(first_result, replayed_result)
        self.assertEqual({"draft_id": 1, "status": "draft"}, replayed_result)
        self.assertEqual(1, draft_count)

    def test_same_idempotency_key_rejects_different_request(self):
        from app.services.tool_execution_store import ToolExecutionStore

        with tempfile.TemporaryDirectory() as temp_dir:
            db = DatabaseService(str(Path(temp_dir) / "runtime.db"))
            store = ToolExecutionStore(db)

            def result(session):
                return {"draft_id": 1}

            store.execute_once(
                idempotency_key="conflicting-key",
                operation="send_reply",
                request_payload={"email_id": "email-1"},
                execute=result,
            )

            with self.assertRaisesRegex(ValueError, "different request"):
                store.execute_once(
                    idempotency_key="conflicting-key",
                    operation="send_reply",
                    request_payload={"email_id": "email-2"},
                    execute=result,
                )

            db.engine.dispose()

    def test_same_idempotency_key_rejects_different_operation(self):
        from app.services.tool_execution_store import ToolExecutionStore

        with tempfile.TemporaryDirectory() as temp_dir:
            db = DatabaseService(str(Path(temp_dir) / "runtime.db"))
            store = ToolExecutionStore(db)

            def result(session):
                return {"draft_id": 1}

            store.execute_once(
                idempotency_key="operation-conflict-key",
                operation="send_reply",
                request_payload={"email_id": "email-1"},
                execute=result,
            )

            with self.assertRaisesRegex(ValueError, "different request"):
                store.execute_once(
                    idempotency_key="operation-conflict-key",
                    operation="apply_draft_patch",
                    request_payload={"email_id": "email-1"},
                    execute=result,
                )

            db.engine.dispose()


if __name__ == "__main__":
    unittest.main()
