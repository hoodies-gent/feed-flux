import unittest
from unittest import mock

from app.agent.triage_tools import build_provider, build_triage_plan, triage_unread
from app.decisions.contract import ReasonCode, TriageItem
from app.decisions.fake import FakeDecisionProvider
from app.decisions.policy import GatePolicy
from app.decisions.settings import DecisionSettings


def _items(*ids) -> list[TriageItem]:
    return [TriageItem(item_id=i, subject=f"subject {i}", sender_email="a@b.test", body_preview="p") for i in ids]


class TriagePlanTest(unittest.TestCase):
    def test_routes_accepted_proposals_into_bulk_and_needs_reply(self):
        provider = FakeDecisionProvider(
            {
                "ci": ("delete", ReasonCode.CI_NOTIFICATION, 0.95),
                "news": ("archive", ReasonCode.NEWSLETTER, 0.9),
                "ask": ("needs_reply", ReasonCode.DIRECT_QUESTION, 0.92),
            }
        )
        plan = build_triage_plan(_items("ci", "news", "ask"), provider)

        self.assertEqual(["ci", "news"], [i.email_id for i in plan.bulk])
        self.assertEqual(["ask"], [i.email_id for i in plan.needs_reply])
        self.assertEqual([], plan.review)
        self.assertEqual(3, plan.item_count)

    def test_anything_the_gate_does_not_accept_goes_to_review_with_its_rule(self):
        provider = FakeDecisionProvider(
            {
                "unsure": ("archive", ReasonCode.OTHER, 0.4),
                "risky": ("delete", ReasonCode.PROMOTION, 0.8),
                "quiet": None,
            }
        )
        plan = build_triage_plan(_items("unsure", "risky", "quiet"), provider)

        self.assertEqual([], plan.bulk)
        rules = {i.email_id: i.gate_rule for i in plan.review}
        self.assertEqual(
            {"unsure": "below_threshold", "risky": "delete_below_threshold", "quiet": "abstained"}, rules
        )

    def test_a_confident_importance_signal_pulls_a_removal_into_review(self):
        provider = FakeDecisionProvider(
            {"invoice": ("archive", ReasonCode.RECEIPT, 0.97)},
            importance={"invoice": (True, 0.93)},
        )
        plan = build_triage_plan(_items("invoice"), provider)

        self.assertEqual([], plan.bulk)
        self.assertEqual("important_bulk_action", plan.review[0].gate_rule)
        self.assertTrue(plan.review[0].important)

    def test_reason_codes_become_deterministic_text_in_the_requested_language(self):
        provider = FakeDecisionProvider({"ci": ("delete", ReasonCode.CI_NOTIFICATION, 0.95)})

        self.assertEqual("CI notification", build_triage_plan(_items("ci"), provider).bulk[0].reason)
        self.assertEqual(
            "CI 通知", build_triage_plan(_items("ci"), provider, language="zh").bulk[0].reason
        )

    def test_provider_failure_sends_items_to_review_rather_than_dropping_them(self):
        class _Failing(FakeDecisionProvider):
            def decide_triage(self, items):
                batch = super().decide_triage(items)
                for decision in batch.decisions:
                    decision.status = "failed"
                    decision.action = None
                    decision.confidence = None
                batch.failed_requests = 1
                return batch

        plan = build_triage_plan(_items("a", "b"), _Failing())

        self.assertEqual(2, len(plan.review))
        self.assertEqual({"provider_failed"}, {i.gate_rule for i in plan.review})
        self.assertEqual(1, plan.failed_requests)

    def test_records_provider_identity_cost_and_timing(self):
        plan = build_triage_plan(_items("a"), FakeDecisionProvider())

        self.assertEqual("fake", plan.provider)
        self.assertEqual(1, plan.requests)
        self.assertGreaterEqual(plan.total_latency_ms, 0.0)

    def test_empty_inbox_makes_no_provider_call(self):
        provider = FakeDecisionProvider()
        plan = build_triage_plan([], provider)

        self.assertEqual([], provider.calls)
        self.assertEqual(0, plan.item_count)

    def test_one_provider_request_covers_the_whole_batch(self):
        provider = FakeDecisionProvider()
        build_triage_plan(_items("a", "b", "c", "d"), provider)

        self.assertEqual([["a", "b", "c", "d"]], provider.calls)

    def test_thresholds_are_configurable(self):
        provider = FakeDecisionProvider({"a": ("archive", ReasonCode.NEWSLETTER, 0.5)})
        strict = build_triage_plan(_items("a"), provider, policy=GatePolicy(accept_threshold=0.9))
        loose = build_triage_plan(_items("a"), provider, policy=GatePolicy(accept_threshold=0.4))

        self.assertEqual(1, len(strict.review))
        self.assertEqual(1, len(loose.bulk))


class ProviderSelectionTest(unittest.TestCase):
    def test_selects_the_configured_provider(self):
        self.assertEqual("fake", build_provider(DecisionSettings(provider="fake")).name)
        self.assertEqual("llm", build_provider(DecisionSettings(provider="llm")).name)
        self.assertEqual("jev", build_provider(DecisionSettings(provider="jev", api_key="k")).name)


class TriageUnreadToolTest(unittest.TestCase):
    def test_tool_takes_no_per_email_arguments(self):
        schema = triage_unread.args_schema.model_json_schema()

        self.assertEqual({"limit", "language"}, set(schema["properties"]))

    def test_tool_summarises_every_bucket_and_stops_the_agent(self):
        provider = FakeDecisionProvider(
            {
                "ci": ("delete", ReasonCode.CI_NOTIFICATION, 0.95),
                "ask": ("needs_reply", ReasonCode.DIRECT_QUESTION, 0.92),
                "unsure": ("archive", ReasonCode.OTHER, 0.3),
            }
        )
        with mock.patch("app.agent.triage_tools._unread_items", return_value=_items("ci", "ask", "unsure")), \
             mock.patch("app.agent.triage_tools.build_provider", return_value=provider):
            result = triage_unread.invoke({"limit": 20})

        self.assertIn("PLAN READY: 1 bulk items + 1 needs-reply + 1 for review", result)
        self.assertIn("STOP", result)

    def test_tool_passes_the_requested_language_through(self):
        provider = FakeDecisionProvider({"ci": ("delete", ReasonCode.CI_NOTIFICATION, 0.95)})
        captured = {}

        def _capture(items, prov, *, language="en", policy=None):
            captured["language"] = language
            return build_triage_plan(items, prov, language=language, policy=policy)

        with mock.patch("app.agent.triage_tools._unread_items", return_value=_items("ci")), \
             mock.patch("app.agent.triage_tools.build_provider", return_value=provider), \
             mock.patch("app.agent.triage_tools.build_triage_plan", side_effect=_capture):
            triage_unread.invoke({"limit": 5, "language": "zh"})

        self.assertEqual("zh", captured["language"])


if __name__ == "__main__":
    unittest.main()
