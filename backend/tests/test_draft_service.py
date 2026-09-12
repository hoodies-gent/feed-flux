import tempfile
import unittest
from pathlib import Path

from app.models.email import DraftReply, SentAction
from app.services.database import DatabaseService


class DraftServiceTest(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        db_path = Path(self.temp_dir.name) / "emails.db"
        self.db = DatabaseService(str(db_path))

    def tearDown(self):
        self.db.engine.dispose()
        self.temp_dir.cleanup()

    def test_create_drafts_keeps_versions_isolated_by_email(self):
        create_draft = getattr(self.db, "create_draft", None)
        list_drafts = getattr(self.db, "get_drafts_for_email", None)
        self.assertIsNotNone(create_draft, "DatabaseService.create_draft is required")
        self.assertIsNotNone(list_drafts, "DatabaseService.get_drafts_for_email is required")

        first_id = create_draft({
            "thread_id": "thread-a",
            "email_id": "email-a",
            "recipient": "sarah@example.com",
            "subject": "Re: Weekly sync",
            "body": "First version",
        })
        second_id = create_draft({
            "thread_id": "thread-a",
            "email_id": "email-a",
            "recipient": "sarah@example.com",
            "subject": "Re: Weekly sync",
            "body": "Second version",
        })
        create_draft({
            "thread_id": "thread-b",
            "email_id": "email-b",
            "recipient": "marcus@example.com",
            "subject": "Re: Project update",
            "body": "Other email",
        })

        drafts = list_drafts("email-a")

        self.assertEqual([second_id, first_id], [draft["id"] for draft in drafts])
        self.assertEqual(["Second version", "First version"], [draft["body"] for draft in drafts])
        self.assertTrue(all(draft["email_id"] == "email-a" for draft in drafts))
        self.assertTrue(all(draft["status"] == "draft" for draft in drafts))

    def test_update_draft_persists_edited_body(self):
        draft_id = self.db.create_draft({
            "thread_id": "thread-a",
            "email_id": "email-a",
            "recipient": "sarah@example.com",
            "subject": "Re: Weekly sync",
            "body": "Original body",
        })
        update_draft = getattr(self.db, "update_draft", None)
        self.assertIsNotNone(update_draft, "DatabaseService.update_draft is required")

        updated = update_draft(draft_id, "Edited body")

        self.assertEqual("Edited body", updated["body"])
        self.assertEqual("Edited body", self.db.get_drafts_for_email("email-a")[0]["body"])

    def test_apply_draft_patch_replaces_only_selected_range(self):
        draft_id = self.db.create_draft({
            "thread_id": "thread-a",
            "email_id": "email-a",
            "recipient": "sarah@example.com",
            "subject": "Re: Weekly sync",
            "body": "Hello Sarah,\nTuesday works for me.\nBest,\nAlex",
        })

        updated = self.db.apply_draft_patch(draft_id, 13, 35, "Wednesday at 10:00 works.\n")

        self.assertEqual(
            "Hello Sarah,\nWednesday at 10:00 works.\nBest,\nAlex",
            updated["body"],
        )

    def test_apply_draft_patch_rejects_invalid_range(self):
        draft_id = self.db.create_draft({
            "thread_id": "thread-a",
            "email_id": "email-a",
            "recipient": "sarah@example.com",
            "subject": "Re: Weekly sync",
            "body": "Short body",
        })

        with self.assertRaises(ValueError):
            self.db.apply_draft_patch(draft_id, 3, 99, "replacement")

    def test_discard_draft_removes_it_from_active_drafts(self):
        draft_id = self.db.create_draft({
            "thread_id": "thread-a",
            "email_id": "email-a",
            "recipient": "sarah@example.com",
            "subject": "Re: Weekly sync",
            "body": "No longer needed",
        })
        discard_draft = getattr(self.db, "discard_draft", None)
        self.assertIsNotNone(discard_draft, "DatabaseService.discard_draft is required")

        discarded = discard_draft(draft_id)

        self.assertEqual("discarded", discarded["status"])
        self.assertEqual([], self.db.get_drafts_for_email("email-a"))

    def test_discarded_draft_cannot_be_edited(self):
        draft_id = self.db.create_draft({
            "thread_id": "thread-a",
            "email_id": "email-a",
            "recipient": "sarah@example.com",
            "subject": "Re: Weekly sync",
            "body": "Original body",
        })
        self.db.discard_draft(draft_id)

        with self.assertRaises(ValueError):
            self.db.update_draft(draft_id, "Edited after discard")

    def test_send_draft_records_sent_action_and_closes_draft(self):
        draft_id = self.db.create_draft({
            "thread_id": "thread-a",
            "email_id": "email-a",
            "recipient": "sarah@example.com",
            "subject": "Re: Weekly sync",
            "body": "See you Tuesday.",
        })
        send_draft = getattr(self.db, "send_draft", None)
        self.assertIsNotNone(send_draft, "DatabaseService.send_draft is required")

        result = send_draft(draft_id)

        session = self.db.Session()
        try:
            draft = session.query(DraftReply).filter_by(id=draft_id).one()
            sent = session.query(SentAction).filter_by(id=result["sent_action_id"]).one()
            self.assertEqual("sent", draft.status)
            self.assertEqual("thread-a", sent.thread_id)
            self.assertEqual("email-a", sent.original_email_id)
            self.assertEqual("sarah@example.com", sent.recipient)
            self.assertEqual("Re: Weekly sync", sent.subject)
            self.assertEqual("See you Tuesday.", sent.body)
        finally:
            session.close()
        self.assertEqual([], self.db.get_drafts_for_email("email-a"))

    def test_sent_draft_cannot_be_sent_twice(self):
        draft_id = self.db.create_draft({
            "thread_id": "thread-a",
            "email_id": "email-a",
            "recipient": "sarah@example.com",
            "subject": "Re: Weekly sync",
            "body": "Send once.",
        })
        self.db.send_draft(draft_id)

        with self.assertRaises(ValueError):
            self.db.send_draft(draft_id)

        session = self.db.Session()
        try:
            self.assertEqual(1, session.query(SentAction).count())
        finally:
            session.close()

    def test_sent_draft_cannot_be_discarded(self):
        draft_id = self.db.create_draft({
            "thread_id": "thread-a",
            "email_id": "email-a",
            "recipient": "sarah@example.com",
            "subject": "Re: Weekly sync",
            "body": "Already sent.",
        })
        self.db.send_draft(draft_id)

        with self.assertRaises(ValueError):
            self.db.discard_draft(draft_id)


if __name__ == "__main__":
    unittest.main()
