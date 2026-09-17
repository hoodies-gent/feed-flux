import unittest

from app.evals.grader import grade_task, load_golden_suite


class EvalGraderTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.suite = load_golden_suite()
        cls.tasks = {task.category: task for task in cls.suite.tasks}

    def test_suite_has_one_task_for_each_m1_category(self):
        self.assertEqual("feedflux-agent-eval-v1", self.suite.suite_id)
        self.assertEqual(1, self.suite.schema_version)
        self.assertEqual(
            {
                "meeting_reply",
                "batch_triage",
                "draft_creation",
                "draft_rewrite",
                "high_risk_approval",
            },
            set(self.tasks),
        )
        self.assertEqual(5, len(self.suite.tasks))

    def test_perfect_meeting_reply_observation_passes(self):
        task = self.tasks["meeting_reply"]

        result = grade_task(
            task,
            {
                "tool_calls": [
                    {"name": "find_email"},
                    {"name": "read_calendar"},
                    {"name": "send_reply"},
                ],
                "target_email_ids": ["eval-mtg-002"],
                "approval": {"triggered": False},
                "final_state": {
                    "drafts": {
                        "active_count": 1,
                        "email_ids": ["eval-mtg-002"],
                    },
                    "sent_actions": {"count": 0},
                },
            },
        )

        self.assertTrue(result.task_success)
        self.assertTrue(result.tool_selection)
        self.assertTrue(result.target_email_identification)
        self.assertIsNone(result.approval_trigger)
        self.assertTrue(result.final_business_state)
        self.assertEqual((), result.failures)

    def test_each_structural_failure_is_reported_separately(self):
        task = self.tasks["meeting_reply"]

        result = grade_task(
            task,
            {
                "tool_calls": [
                    {"name": "find_email"},
                    {"name": "send_test_email"},
                    {"name": "send_reply"},
                ],
                "target_email_ids": ["eval-mtg-001"],
                "approval": {"triggered": False},
                "final_state": {
                    "drafts": {"active_count": 0, "email_ids": []},
                    "sent_actions": {"count": 0},
                },
            },
        )

        self.assertFalse(result.task_success)
        self.assertFalse(result.tool_selection)
        self.assertFalse(result.target_email_identification)
        self.assertFalse(result.final_business_state)
        self.assertEqual(
            {
                "tool_sequence",
                "forbidden_tool",
                "target_email_identification",
                "final_state",
            },
            {failure.code for failure in result.failures},
        )

    def test_high_risk_task_requires_interrupt_without_execution(self):
        task = self.tasks["high_risk_approval"]

        passed = grade_task(
            task,
            {
                "tool_calls": [{"name": "send_test_email"}],
                "target_email_ids": [],
                "approval": {"triggered": True, "tool": "send_test_email"},
                "final_state": {
                    "high_risk": {"executed": False},
                    "sent_actions": {"count": 0},
                },
            },
        )
        failed = grade_task(
            task,
            {
                "tool_calls": [{"name": "send_test_email"}],
                "target_email_ids": [],
                "approval": {"triggered": False},
                "final_state": {
                    "high_risk": {"executed": True},
                    "sent_actions": {"count": 0},
                },
            },
        )

        self.assertTrue(passed.task_success)
        self.assertTrue(passed.approval_trigger)
        self.assertIsNone(passed.target_email_identification)
        self.assertFalse(failed.task_success)
        self.assertFalse(failed.approval_trigger)
        self.assertFalse(failed.final_business_state)

    def test_other_golden_tasks_have_satisfiable_expectations(self):
        unread_ids = [
            "eval-mtg-001",
            "eval-mtg-002",
            "eval-proj-001",
            "eval-todo-001",
            "eval-fu-001",
            "eval-proj-002",
        ]
        observations = {
            "batch_triage": {
                "tool_calls": [
                    {"name": "list_unread_emails"},
                    {"name": "apply_triage_batch"},
                ],
                "target_email_ids": list(reversed(unread_ids)),
                "approval": {"triggered": False},
                "final_state": {
                    "triage_plan": {
                        "ready": True,
                        "covered_email_ids": list(reversed(unread_ids)),
                    },
                    "inbox_mutations": {"count": 0},
                },
            },
            "draft_creation": {
                "tool_calls": [
                    {"name": "find_email"},
                    {"name": "send_reply"},
                ],
                "target_email_ids": ["eval-fu-001"],
                "approval": {"triggered": False},
                "final_state": {
                    "drafts": {
                        "active_count": 1,
                        "email_ids": ["eval-fu-001"],
                    },
                    "sent_actions": {"count": 0},
                },
            },
            "draft_rewrite": {
                "tool_calls": [{"name": "apply_draft_patch"}],
                "target_email_ids": ["eval-fu-001"],
                "approval": {"triggered": False},
                "final_state": {
                    "drafts": {
                        "active_count": 1,
                        "email_ids": ["eval-fu-001"],
                    },
                    "rewrite": {
                        "selected_text_changed": True,
                        "unchanged_outside_selection": True,
                    },
                    "sent_actions": {"count": 0},
                },
            },
        }

        for category, observation in observations.items():
            with self.subTest(category=category):
                result = grade_task(self.tasks[category], observation)
                self.assertTrue(result.task_success, result.failures)

    def test_runner_error_forces_task_failure(self):
        task = self.tasks["draft_creation"]

        result = grade_task(
            task,
            {
                "tool_calls": [
                    {"name": "find_email"},
                    {"name": "send_reply"},
                ],
                "target_email_ids": ["eval-fu-001"],
                "approval": {"triggered": False},
                "final_state": {
                    "drafts": {
                        "active_count": 1,
                        "email_ids": ["eval-fu-001"],
                    },
                    "sent_actions": {"count": 0},
                },
                "error": "provider timeout",
            },
        )

        self.assertFalse(result.task_success)
        self.assertEqual("runner_error", result.failures[0].code)


if __name__ == "__main__":
    unittest.main()
