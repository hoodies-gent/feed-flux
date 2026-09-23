import tempfile
import unittest
from pathlib import Path

from app.agent import context
from app.services.database import DatabaseService


class AgentContextTest(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db = DatabaseService(str(Path(self.temp_dir.name) / "emails.db"))

    def tearDown(self):
        self.db.engine.dispose()
        self.temp_dir.cleanup()

    def _insert_email(
        self,
        email_id: str,
        *,
        subject: str,
        summary: str | None = None,
        body_content: str | None = None,
        body_preview: str | None = None,
    ) -> None:
        self.db.insert_email({
            "id": email_id,
            "subject": subject,
            "sender_name": "Marcus Patel",
            "sender_email": "marcus@example.com",
            "received_datetime": 1,
            "summary": summary,
            "body_content": body_content,
            "body_preview": body_preview,
        })

    def test_empty_selection_resolves_to_empty_context(self):
        resolved = context.resolve_email_context([], database=self.db)

        self.assertEqual([], resolved["email_ids"])
        self.assertEqual("", resolved["prompt"])
        self.assertEqual([], resolved["references"])
        self.assertEqual(0, resolved["context_chars"])

    def test_resolves_one_two_or_three_email_ids_in_request_order(self):
        for index in range(1, 4):
            self._insert_email(
                f"email-{index}",
                subject=f"Email {index}",
                body_content=f"Body {index}",
            )

        for count in (1, 2, 3):
            with self.subTest(count=count):
                email_ids = [f"email-{index}" for index in range(1, count + 1)]
                resolved = context.resolve_email_context(
                    email_ids,
                    database=self.db,
                )

                self.assertEqual(email_ids, resolved["email_ids"])
                self.assertEqual(
                    email_ids,
                    [reference["email_id"] for reference in resolved["references"]],
                )

    def test_selects_one_controlled_content_source_per_email(self):
        self._insert_email(
            "email-summary",
            subject="Summary email",
            summary="Use this saved summary.",
            body_content="Do not include this full body.",
            body_preview="Do not include this preview.",
        )
        self._insert_email(
            "email-body",
            subject="Body email",
            body_content="Use this body content.",
            body_preview="Do not include the fallback preview.",
        )
        self._insert_email(
            "email-preview",
            subject="Preview email",
            body_preview="Use this preview.",
        )
        resolved = context.resolve_email_context(
            ["email-summary", "email-body", "email-preview"],
            database=self.db,
        )

        self.assertIn("Use this saved summary.", resolved["prompt"])
        self.assertNotIn("Do not include this full body.", resolved["prompt"])
        self.assertIn("Use this body content.", resolved["prompt"])
        self.assertNotIn("Do not include the fallback preview.", resolved["prompt"])
        self.assertIn("Use this preview.", resolved["prompt"])
        self.assertEqual(
            [
                {
                    "email_id": "email-summary",
                    "subject": "Summary email",
                    "sender": "Marcus Patel",
                },
                {
                    "email_id": "email-body",
                    "subject": "Body email",
                    "sender": "Marcus Patel",
                },
                {
                    "email_id": "email-preview",
                    "subject": "Preview email",
                    "sender": "Marcus Patel",
                },
            ],
            resolved["references"],
        )

    def test_limits_final_prompt_to_six_thousand_characters(self):
        for index, marker in enumerate(("A", "B", "C"), start=1):
            self._insert_email(
                f"email-{index}",
                subject=f"Large email {index}",
                body_content=marker * 10_000,
            )
        resolved = context.resolve_email_context(
            ["email-1", "email-2", "email-3"],
            database=self.db,
        )

        self.assertLessEqual(len(resolved["prompt"]), 6_000)
        self.assertEqual(len(resolved["prompt"]), resolved["context_chars"])
        self.assertEqual(3, resolved["prompt"].count("</email_context>"))
        self.assertEqual(3, len(resolved["references"]))

    def test_rejects_more_than_three_requested_email_ids(self):
        with self.assertRaises(context.EmailContextError) as caught:
            context.resolve_email_context(
                ["email-1", "email-2", "email-3", "email-4"],
                database=self.db,
            )

        self.assertIn("at most 3", caught.exception.user_message)

    def test_missing_email_returns_access_safe_error(self):
        with self.assertRaises(context.EmailContextError) as caught:
            context.resolve_email_context(["private-email-id"], database=self.db)

        self.assertIn("unavailable", caught.exception.user_message)
        self.assertNotIn("private-email-id", str(caught.exception))


if __name__ == "__main__":
    unittest.main()
