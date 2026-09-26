import unittest
from unittest import mock

from app.agent.triage_tools import build_provider, build_triage_plan, triage_unread
from app.decisions.contract import ReasonCode, TriageItem
from app.decisions.fake import FakeDecisionProvider
from app.decisions.policy import GatePolicy
from app.decisions.settings import DecisionSettings


def _items(*ids) -> list[TriageItem]:
    return [TriageItem(item_id=i, subject=f"subject {i}", sender_email="a@b.test", body_preview="p") for i in ids]


def _provider(scripted, importance=None):
    return FakeDecisionProvider(scripted, importance=importance or {})


class AttentionRoutingTest(unittest.TestCase):
    def test_splits_the_batch_into_reply_attention_and_dismissable(self):
        provider = _provider(
            {
                "ask": ("needs_reply", ReasonCode.DIRECT_QUESTION, 0.93),
                "invoice": ("archive", ReasonCode.RECEIPT, 0.9),
                "news": ("archive", ReasonCode.NEWSLETTER, 0.9),
            },
            importance={"invoice": (True, 0.9), "news": (False, 0.9)},
        )
        plan = build_triage_plan(_items("ask", "invoice", "news"), provider)

        self.assertEqual(["ask"], [i.email_id for i in plan.needs_reply])
        self.assertEqual(["invoice"], [i.email_id for i in plan.important])
        self.assertEqual(["news"], [i.email_id for i in plan.bulk])
        self.assertEqual([], plan.unresolved)
        self.assertEqual(2, plan.attention_count)

    def test_never_proposes_archive_or_delete_however_the_provider_answers(self):
        provider = _provider(
            {i: (action, ReasonCode.PROMOTION, 0.99) for i, action in
             (("a", "delete"), ("b", "archive"), ("c", "mark_read"))},
            importance={i: (False, 0.95) for i in ("a", "b", "c")},
        )
        plan = build_triage_plan(_items("a", "b", "c"), provider)

        self.assertEqual(3, len(plan.bulk))
        self.assertEqual({"delete", "archive", "mark_read"}, {i.proposed_action for i in plan.bulk})

    def test_an_important_email_is_surfaced_even_when_the_provider_wants_it_gone(self):
        provider = _provider(
            {"invoice": ("delete", ReasonCode.RECEIPT, 0.99)},
            importance={"invoice": (True, 0.95)},
        )
        plan = build_triage_plan(_items("invoice"), provider)

        self.assertEqual(["invoice"], [i.email_id for i in plan.important])
        self.assertEqual([], plan.bulk)
        self.assertEqual("delete", plan.important[0].proposed_action)


class UncertaintyTest(unittest.TestCase):
    def test_low_confidence_leaves_the_email_untouched(self):
        provider = _provider({"a": ("mark_read", ReasonCode.OTHER, 0.4)})
        plan = build_triage_plan(_items("a"), provider)

        self.assertEqual(["a"], [i.email_id for i in plan.unresolved])
        self.assertEqual("below_threshold", plan.unresolved[0].gate_rule)

    def test_unclear_importance_is_not_dismissed(self):
        provider = _provider(
            {"a": ("mark_read", ReasonCode.STATUS_UPDATE, 0.95)},
            importance={"a": (False, 0.3)},
        )
        plan = build_triage_plan(_items("a"), provider)

        self.assertEqual([], plan.bulk)
        self.assertEqual("importance_unclear", plan.unresolved[0].gate_rule)

    def test_an_abstaining_provider_touches_nothing(self):
        provider = _provider({"a": None, "b": None})
        plan = build_triage_plan(_items("a", "b"), provider)

        self.assertEqual(2, len(plan.unresolved))
        self.assertEqual({"abstained"}, {i.gate_rule for i in plan.unresolved})
        self.assertEqual(0, len(plan.bulk) + plan.attention_count)

    def test_a_provider_without_the_importance_axis_still_works(self):
        provider = _provider({"a": ("mark_read", ReasonCode.NEWSLETTER, 0.95)})
        plan = build_triage_plan(_items("a"), provider)

        self.assertEqual(["a"], [i.email_id for i in plan.bulk])
        self.assertIsNone(plan.bulk[0].important)

    def test_every_item_lands_in_exactly_one_bucket(self):
        provider = _provider(
            {
                "ask": ("needs_reply", ReasonCode.DIRECT_QUESTION, 0.95),
                "keep": ("mark_read", ReasonCode.RECEIPT, 0.95),
                "weak": ("mark_read", ReasonCode.OTHER, 0.2),
                "news": ("archive", ReasonCode.NEWSLETTER, 0.95),
            },
            importance={"keep": (True, 0.9), "news": (False, 0.9)},
        )
        ids = ("ask", "keep", "weak", "news")

        plan = build_triage_plan(_items(*ids), provider)

        placed = [i.email_id for i in plan.needs_reply + plan.important + plan.bulk + plan.unresolved]
        self.assertEqual(sorted(ids), sorted(placed))
        self.assertEqual(len(ids), plan.item_count)


class PlanMechanicsTest(unittest.TestCase):
    def test_reason_codes_become_deterministic_text_in_the_requested_language(self):
        provider = _provider({"ci": ("mark_read", ReasonCode.CI_NOTIFICATION, 0.95)})

        self.assertEqual("CI notification", build_triage_plan(_items("ci"), provider).bulk[0].reason)
        self.assertEqual("CI 通知", build_triage_plan(_items("ci"), provider, language="zh").bulk[0].reason)

    def test_one_provider_request_covers_the_whole_batch(self):
        provider = _provider({})
        build_triage_plan(_items("a", "b", "c", "d"), provider)

        self.assertEqual([["a", "b", "c", "d"]], provider.calls)

    def test_records_provider_identity_cost_and_timing(self):
        plan = build_triage_plan(_items("a"), _provider({}))

        self.assertEqual("fake", plan.provider.provider)
        self.assertEqual(1, plan.provider.requests)
        self.assertGreaterEqual(plan.total_latency_ms, 0.0)

    def test_empty_inbox_makes_no_provider_call(self):
        provider = _provider({})
        plan = build_triage_plan([], provider)

        self.assertEqual([], provider.calls)
        self.assertEqual(0, plan.item_count)

    def test_thresholds_are_configurable(self):
        provider = _provider({"a": ("mark_read", ReasonCode.NEWSLETTER, 0.5)})
        strict = build_triage_plan(_items("a"), provider, policy=GatePolicy(accept_threshold=0.9))
        loose = build_triage_plan(_items("a"), provider, policy=GatePolicy(accept_threshold=0.4))

        self.assertEqual(1, len(strict.unresolved))
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

    def test_tool_reports_every_bucket_and_stops_the_agent(self):
        provider = _provider(
            {
                "ask": ("needs_reply", ReasonCode.DIRECT_QUESTION, 0.95),
                "keep": ("mark_read", ReasonCode.RECEIPT, 0.95),
                "news": ("mark_read", ReasonCode.NEWSLETTER, 0.95),
                "weak": ("mark_read", ReasonCode.OTHER, 0.2),
            },
            importance={"keep": (True, 0.9), "news": (False, 0.9)},
        )
        with mock.patch("app.agent.triage_tools._unread_items", return_value=_items("ask", "keep", "news", "weak")), \
             mock.patch("app.agent.triage_tools.build_provider", return_value=provider):
            result = triage_unread.invoke({"limit": 20})

        self.assertIn("1 needing a reply, 1 worth a look, 1 safe to mark read, 1 left untouched", result)
        self.assertIn("STOP", result)

    def test_tool_passes_the_requested_language_through(self):
        provider = _provider({"ci": ("mark_read", ReasonCode.CI_NOTIFICATION, 0.95)})
        captured = {}

        def _capture(items, prov, *, language="en", **kwargs):
            captured["language"] = language
            return build_triage_plan(items, prov, language=language)

        with mock.patch("app.agent.triage_tools._unread_items", return_value=_items("ci")), \
             mock.patch("app.agent.triage_tools.build_provider", return_value=provider), \
             mock.patch("app.agent.triage_tools.build_triage_plan", side_effect=_capture):
            triage_unread.invoke({"limit": 5, "language": "zh"})

        self.assertEqual("zh", captured["language"])


if __name__ == "__main__":
    unittest.main()
