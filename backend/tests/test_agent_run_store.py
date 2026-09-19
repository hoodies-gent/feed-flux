import tempfile
import unittest
from pathlib import Path

from app.services.database import DatabaseService


class AgentRunStoreTest(unittest.TestCase):
    def test_usage_summary_aggregates_durable_provider_events(self):
        from app.services.agent_run_store import AgentRunStore

        with tempfile.TemporaryDirectory() as temp_dir:
            db_path = Path(temp_dir) / "runtime.db"
            first_db = DatabaseService(str(db_path))
            first_store = AgentRunStore(first_db)
            run_id = first_store.create_run(
                thread_id="usage-summary-thread",
                provider="fixture-provider",
            )["run_id"]
            first_store.append_event(
                run_id,
                event_type="provider_usage",
                provider="fixture-provider",
                outcome={
                    "schema_version": 1,
                    "model": "fixture-model",
                    "usage": {
                        "input_tokens": 100,
                        "cached_input_tokens": 40,
                        "output_tokens": 20,
                        "total_tokens": 120,
                    },
                    "pricing": {},
                    "estimated_cost_usd": 0.00011,
                },
            )
            first_store.append_event(
                run_id,
                event_type="provider_usage",
                provider="fixture-provider",
                outcome={
                    "schema_version": 1,
                    "model": "fixture-model",
                    "usage": {
                        "input_tokens": 50,
                        "cached_input_tokens": 0,
                        "output_tokens": 10,
                        "total_tokens": 60,
                    },
                    "pricing": {},
                    "estimated_cost_usd": 0.00007,
                },
            )
            first_db.engine.dispose()

            reopened_db = DatabaseService(str(db_path))
            summary = AgentRunStore(reopened_db).get_usage_summary(run_id)
            reopened_db.engine.dispose()

        self.assertEqual(
            {
                "input_tokens": 150,
                "cached_input_tokens": 40,
                "output_tokens": 30,
                "total_tokens": 180,
                "estimated_cost_usd": 0.00018,
            },
            summary,
        )

    def test_usage_summary_does_not_report_partial_cost(self):
        from app.services.agent_run_store import AgentRunStore

        with tempfile.TemporaryDirectory() as temp_dir:
            db = DatabaseService(str(Path(temp_dir) / "runtime.db"))
            store = AgentRunStore(db)
            run_id = store.create_run(thread_id="partial-cost-thread")["run_id"]
            for usage, cost in [
                (
                    {
                        "input_tokens": 100,
                        "cached_input_tokens": 40,
                        "output_tokens": 20,
                        "total_tokens": 120,
                    },
                    0.00011,
                ),
                (
                    {
                        "input_tokens": 50,
                        "cached_input_tokens": 0,
                        "output_tokens": 10,
                        "total_tokens": 60,
                    },
                    None,
                ),
            ]:
                store.append_event(
                    run_id,
                    event_type="provider_usage",
                    outcome={
                        "schema_version": 1,
                        "usage": usage,
                        "estimated_cost_usd": cost,
                    },
                )

            summary = store.get_usage_summary(run_id)
            db.engine.dispose()

        self.assertEqual(180, summary["total_tokens"])
        self.assertIsNone(summary["estimated_cost_usd"])

    def test_run_lifecycle_survives_database_reopen_with_same_run_id(self):
        try:
            from app.services.agent_run_store import AgentRunStore, RunStatus
        except ModuleNotFoundError:
            self.fail("agent run ledger is not implemented")

        with tempfile.TemporaryDirectory() as temp_dir:
            db_path = Path(temp_dir) / "runtime.db"
            first_db = DatabaseService(str(db_path))
            first_store = AgentRunStore(first_db)

            created = first_store.create_run(
                thread_id="durable-thread",
                provider="deepseek",
            )
            run_id = created["run_id"]
            first_store.transition_run(run_id, RunStatus.RUNNING)
            first_store.transition_run(run_id, RunStatus.INTERRUPTED)
            first_db.engine.dispose()

            reopened_db = DatabaseService(str(db_path))
            reopened_store = AgentRunStore(reopened_db)
            persisted = reopened_store.get_run(run_id)
            events = reopened_store.list_events(run_id)
            reopened_db.engine.dispose()

        self.assertEqual(run_id, persisted["run_id"])
        self.assertEqual("durable-thread", persisted["thread_id"])
        self.assertEqual("deepseek", persisted["provider"])
        self.assertEqual("interrupted", persisted["status"])
        self.assertEqual(
            ["queued", "running", "interrupted"],
            [event["status"] for event in events],
        )

    def test_terminal_run_rejects_later_transition_without_appending_event(self):
        try:
            from app.services.agent_run_store import AgentRunStore, RunStatus
        except ModuleNotFoundError:
            self.fail("agent run ledger is not implemented")

        with tempfile.TemporaryDirectory() as temp_dir:
            db = DatabaseService(str(Path(temp_dir) / "runtime.db"))
            store = AgentRunStore(db)
            run_id = store.create_run(thread_id="terminal-thread")["run_id"]
            store.transition_run(run_id, RunStatus.RUNNING)
            store.transition_run(run_id, RunStatus.COMPLETED)

            with self.assertRaises(ValueError):
                store.transition_run(run_id, RunStatus.FAILED)

            persisted = store.get_run(run_id)
            events = store.list_events(run_id)
            db.engine.dispose()

        self.assertEqual("completed", persisted["status"])
        self.assertEqual(
            ["queued", "running", "completed"],
            [event["status"] for event in events],
        )

    def test_events_record_provider_tool_outcome_and_error_category(self):
        try:
            from app.services.agent_run_store import AgentRunStore, ErrorCategory, RunStatus
        except ModuleNotFoundError:
            self.fail("agent run ledger is not implemented")

        with tempfile.TemporaryDirectory() as temp_dir:
            db = DatabaseService(str(Path(temp_dir) / "runtime.db"))
            store = AgentRunStore(db)
            run_id = store.create_run(
                thread_id="auditable-thread",
                provider="glm",
            )["run_id"]
            store.transition_run(run_id, RunStatus.RUNNING)
            store.append_event(
                run_id,
                event_type="provider_call",
                provider="glm",
            )
            store.append_event(
                run_id,
                event_type="tool_call",
                tool_name="send_reply",
                tool_call_id="tool-call-7",
            )
            store.transition_run(
                run_id,
                RunStatus.FAILED,
                outcome={"kind": "no_draft_created"},
                error_category=ErrorCategory.TERMINAL,
            )

            persisted = store.get_run(run_id)
            events = store.list_events(run_id)
            db.engine.dispose()

        provider_event = next(event for event in events if event["event_type"] == "provider_call")
        tool_event = next(event for event in events if event["event_type"] == "tool_call")
        failed_event = events[-1]

        self.assertEqual("glm", provider_event["provider"])
        self.assertEqual("send_reply", tool_event["tool_name"])
        self.assertEqual("tool-call-7", tool_event["tool_call_id"])
        self.assertEqual({"kind": "no_draft_created"}, persisted["outcome"])
        self.assertEqual("terminal", persisted["error_category"])
        self.assertEqual({"kind": "no_draft_created"}, failed_event["outcome"])
        self.assertEqual("terminal", failed_event["error_category"])


if __name__ == "__main__":
    unittest.main()
