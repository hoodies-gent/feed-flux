import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from app.agent.execution_context import current_run_id, current_tool_call_id
from app.agent.tools import apply_triage_batch
from app.decisions.contract import ReasonCode
from app.decisions.fake import FakeDecisionProvider
from app.decisions.settings import DecisionSettings
from app.decisions.shadow import SHADOW_EVENT_TYPE, _provider, observe_triage_shadow
from app.models.email import Email
from app.services.agent_run_store import AgentRunStore
from app.services.database import DatabaseService


EMAILS = [
    ("dev-news-001", "TechDigest Weekly", "digest@news.test", "Top stories: secret salary leak"),
    ("dev-ci-001", "[GitHub] Workflow run passed", "ci@github.test", "build-and-test succeeded"),
    ("dev-fu-001", "Following up on the proposal", "kim@client.test", "Any update on pricing?"),
]

SHADOW_ENV = {"DECISION_PROVIDER_MODE": "shadow", "DECISION_PROVIDER": "fake"}

ACTIONS = [
    {"email_id": "dev-news-001", "action": "archive", "reason": "newsletter"},
    {"email_id": "dev-ci-001", "action": "mark_read", "reason": "CI passed"},
]
NEEDS_REPLY = [{"email_id": "dev-fu-001", "reason": "2nd follow-up"}]


class ShadowObservationTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.db = DatabaseService(str(Path(self._tmp.name) / "emails.db"))

        session = self.db.Session()
        for email_id, subject, sender_email, preview in EMAILS:
            session.add(
                Email(
                    id=email_id,
                    subject=subject,
                    sender_name="Sender",
                    sender_email=sender_email,
                    received_datetime=1_700_000_000,
                    body_preview=preview,
                    body_content=preview,
                    is_read=False,
                )
            )
        session.commit()
        session.close()

        self._db_patch = mock.patch("app.decisions.shadow.DatabaseService", return_value=self.db)
        self._db_patch.start()
        self.addCleanup(self._db_patch.stop)

        self.store = AgentRunStore(self.db)
        run = self.store.create_run(thread_id="t-shadow", provider="deepseek")
        self.run_id = run["run_id"]
        token = current_run_id.set(self.run_id)
        call_token = current_tool_call_id.set("call-1")
        self.addCleanup(current_run_id.reset, token)
        self.addCleanup(current_tool_call_id.reset, call_token)

    def _events(self):
        return [e for e in self.store.list_events(self.run_id) if e["event_type"] == SHADOW_EVENT_TYPE]

    def _run_with(self, provider, env=None):
        with mock.patch.dict("os.environ", env or SHADOW_ENV, clear=True), mock.patch(
            "app.decisions.shadow._provider", return_value=provider
        ):
            observe_triage_shadow(ACTIONS, NEEDS_REPLY)

    def test_off_by_default_makes_no_provider_call_and_writes_no_event(self):
        provider = FakeDecisionProvider()
        with mock.patch.dict("os.environ", {}, clear=True), mock.patch(
            "app.decisions.shadow._provider", return_value=provider
        ):
            observe_triage_shadow(ACTIONS, NEEDS_REPLY)

        self.assertEqual([], provider.calls)
        self.assertEqual([], self._events())

    def test_sends_one_request_covering_every_classified_email(self):
        provider = FakeDecisionProvider()
        self._run_with(provider)

        self.assertEqual([["dev-news-001", "dev-ci-001", "dev-fu-001"]], provider.calls)

    def test_records_agent_and_provider_actions_with_gate_outcome(self):
        provider = FakeDecisionProvider(
            {
                "dev-news-001": ("archive", ReasonCode.NEWSLETTER, 0.96),
                "dev-ci-001": ("delete", ReasonCode.CI_NOTIFICATION, 0.62),
                "dev-fu-001": None,
            }
        )
        self._run_with(provider)

        outcome = self._events()[0]["outcome"]
        by_id = {item["email_id"]: item for item in outcome["items"]}

        agreed = by_id["dev-news-001"]
        self.assertEqual(("archive", "archive", "accept"), (agreed["agent_action"], agreed["provider_action"], agreed["gate_outcome"]))
        self.assertTrue(agreed["agrees_with_agent"])
        self.assertEqual("newsletter", agreed["reason_code"])

        risky = by_id["dev-ci-001"]
        self.assertEqual(("mark_read", "delete"), (risky["agent_action"], risky["provider_action"]))
        self.assertEqual(("review", "delete_below_threshold"), (risky["gate_outcome"], risky["gate_rule"]))
        self.assertFalse(risky["agrees_with_agent"])

        abstained = by_id["dev-fu-001"]
        self.assertEqual(("needs_reply", None, "fallback"), (abstained["agent_action"], abstained["provider_action"], abstained["gate_outcome"]))
        self.assertIsNone(abstained["agrees_with_agent"])

        self.assertEqual({"items": 3, "agreements": 1, "accepted": 1, "fallback": 1, "review": 1}, {
            key: outcome["summary"][key] for key in ("items", "agreements", "accepted", "fallback", "review")
        })

    def test_recorded_event_contains_no_email_content(self):
        self._run_with(FakeDecisionProvider())

        serialized = json.dumps(self._events()[0], ensure_ascii=False)
        for fragment in ("secret salary", "TechDigest", "digest@news.test", "Any update", "2nd follow-up"):
            self.assertNotIn(fragment, serialized)

    def test_provider_failure_is_swallowed_and_writes_no_event(self):
        provider = FakeDecisionProvider(error=TimeoutError("provider down"))
        self._run_with(provider)

        self.assertEqual([], self._events())

    def test_survives_a_missing_run_context(self):
        current_run_id.set(None)
        provider = FakeDecisionProvider()
        self._run_with(provider)

        self.assertEqual([["dev-news-001", "dev-ci-001", "dev-fu-001"]], provider.calls)

    def test_skips_emails_that_are_not_in_the_database(self):
        provider = FakeDecisionProvider()
        with mock.patch.dict("os.environ", SHADOW_ENV, clear=True), mock.patch(
            "app.decisions.shadow._provider", return_value=provider
        ):
            observe_triage_shadow([{"email_id": "ghost-001", "action": "delete"}], [])

        self.assertEqual([], provider.calls)
        self.assertEqual([], self._events())

    def test_unknown_provider_name_resolves_to_nothing(self):
        self.assertIsNone(_provider(DecisionSettings(mode="shadow", provider="nope")))

    def test_missing_api_key_is_swallowed_and_writes_no_event(self):
        env = {"DECISION_PROVIDER_MODE": "shadow", "DECISION_PROVIDER": "jev"}
        with mock.patch.dict("os.environ", env, clear=True):
            observe_triage_shadow(ACTIONS, NEEDS_REPLY)

        self.assertEqual([], self._events())


class ToolIsUnaffectedTest(unittest.TestCase):
    def _invoke(self):
        return apply_triage_batch.invoke({"actions": ACTIONS, "needs_reply": NEEDS_REPLY})

    def test_tool_result_is_identical_with_shadow_off_and_on(self):
        with mock.patch.dict("os.environ", {}, clear=True):
            baseline = self._invoke()

        with mock.patch.dict("os.environ", SHADOW_ENV, clear=True), mock.patch(
            "app.decisions.shadow._observe"
        ) as observed:
            with_shadow = self._invoke()

        self.assertEqual(baseline, with_shadow)
        self.assertIn("PLAN READY: 2 bulk items + 1 needs-reply", baseline)
        observed.assert_called_once()

    def test_observes_the_pydantic_items_the_tool_decorator_coerces_args_into(self):
        seen = []

        def _capture(actions, needs_reply):
            seen.append((list(actions), list(needs_reply)))

        with mock.patch.dict("os.environ", SHADOW_ENV, clear=True), mock.patch(
            "app.decisions.shadow._observe", side_effect=_capture
        ):
            self._invoke()

        actions, needs_reply = seen[0]
        self.assertFalse(isinstance(actions[0], dict))
        from app.decisions.shadow import _field

        self.assertEqual("dev-news-001", _field(actions[0], "email_id"))
        self.assertEqual("archive", _field(actions[0], "action"))
        self.assertEqual("dev-fu-001", _field(needs_reply[0], "email_id"))

    def test_tool_still_returns_when_the_observation_raises(self):
        with mock.patch.dict("os.environ", SHADOW_ENV, clear=True), mock.patch(
            "app.decisions.shadow._observe", side_effect=RuntimeError("boom")
        ):
            result = self._invoke()

        self.assertIn("PLAN READY", result)


if __name__ == "__main__":
    unittest.main()
