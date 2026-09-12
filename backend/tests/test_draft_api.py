import asyncio
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import api
from fastapi import HTTPException

from app.models.email import DraftReply, Email, SentAction
from app.services.database import DatabaseService


class DraftApiTest(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db = DatabaseService(str(Path(self.temp_dir.name) / "emails.db"))
        self.db.insert_email({
            "id": "email-a",
            "subject": "Weekly sync",
            "sender_name": "Sarah",
            "sender_email": "sarah@example.com",
            "received_datetime": 1,
            "body_preview": "Can we meet Tuesday?",
            "body_content": "Can we meet Tuesday?",
        })
        self.db_patch = patch.object(api, "db", self.db)
        self.db_patch.start()

    def tearDown(self):
        self.db_patch.stop()
        self.db.engine.dispose()
        self.temp_dir.cleanup()

    def _draft(self, body="Initial body"):
        return self.db.create_draft({
            "thread_id": "thread-a",
            "email_id": "email-a",
            "recipient": "sarah@example.com",
            "subject": "Re: Weekly sync",
            "body": body,
        })

    def test_list_drafts_returns_active_versions_for_email(self):
        first_id = self._draft("First")
        second_id = self._draft("Second")
        self.db.discard_draft(first_id)

        drafts = asyncio.run(api.list_email_drafts("email-a"))

        self.assertEqual([second_id], [draft["id"] for draft in drafts])

    def test_list_drafts_exposes_latest_active_draft_as_canonical(self):
        self._draft("First")
        second_id = self._draft("Second")

        drafts = asyncio.run(api.list_email_drafts("email-a"))

        self.assertEqual([second_id], [draft["id"] for draft in drafts])

    def test_create_reply_draft_starts_blank_and_reuses_active_draft(self):
        created = asyncio.run(api.create_email_draft("email-a"))

        self.assertEqual("email-a", created["email_id"])
        self.assertEqual("sarah@example.com", created["recipient"])
        self.assertEqual("Re: Weekly sync", created["subject"])
        self.assertEqual("", created["body"])
        self.assertEqual("draft", created["status"])

        reused = asyncio.run(api.create_email_draft("email-a"))
        self.assertEqual(created["id"], reused["id"])

    def test_update_draft_returns_edited_draft(self):
        draft_id = self._draft()

        updated = asyncio.run(api.update_draft(draft_id, api.DraftUpdateRequest(body="Edited")))

        self.assertEqual(draft_id, updated["id"])
        self.assertEqual("Edited", updated["body"])

    def test_discard_draft_marks_it_discarded(self):
        draft_id = self._draft()

        discarded = asyncio.run(api.discard_draft(draft_id))

        self.assertEqual(draft_id, discarded["id"])
        self.assertEqual("discarded", discarded["status"])

    def test_send_draft_records_dry_run_and_marks_sent(self):
        draft_id = self._draft()

        result = asyncio.run(api.send_draft(draft_id))

        self.assertTrue(result["ok"])
        self.assertEqual(draft_id, result["draft_id"])
        session = self.db.Session()
        try:
            draft = session.query(DraftReply).filter_by(id=draft_id).one()
            sent = session.query(SentAction).filter_by(original_email_id="email-a").one()
        finally:
            session.close()
        self.assertEqual("sent", draft.status)
        self.assertEqual("Initial body", sent.body)

    def test_mutations_map_unknown_draft_to_404(self):
        with self.assertRaises(HTTPException) as ctx:
            asyncio.run(api.send_draft(999))
        self.assertEqual(404, ctx.exception.status_code)

    def test_mutations_map_closed_draft_to_409(self):
        draft_id = self._draft()
        self.db.discard_draft(draft_id)

        with self.assertRaises(HTTPException) as ctx:
            asyncio.run(api.update_draft(draft_id, api.DraftUpdateRequest(body="Late edit")))
        self.assertEqual(409, ctx.exception.status_code)


if __name__ == "__main__":
    unittest.main()
