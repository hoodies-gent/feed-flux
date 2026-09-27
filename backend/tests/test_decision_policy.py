import unittest

from pydantic import ValidationError

from app.decisions.contract import (
    ReasonCode,
    TriageDecision,
    TriageItem,
    align_decisions,
)
from app.decisions.fake import FakeDecisionProvider
from app.decisions.policy import REASON_TEXT, GatePolicy, apply_gate, reason_text


def _decision(action, confidence, status="ok"):
    return TriageDecision(
        item_id="e1",
        status=status,
        action=action,
        reason_code=ReasonCode.OTHER if action else None,
        confidence=confidence,
    )


class DecisionContractTest(unittest.TestCase):
    def test_rejects_actions_outside_the_finite_set(self):
        with self.assertRaises(ValidationError):
            TriageDecision(item_id="e1", status="ok", action="reply", confidence=0.9)

    def test_rejects_free_text_reason_and_out_of_range_confidence(self):
        with self.assertRaises(ValidationError):
            TriageDecision(item_id="e1", status="ok", action="archive", reason_code="looks like spam")
        with self.assertRaises(ValidationError):
            TriageDecision(item_id="e1", status="ok", action="archive", confidence=1.2)

    def test_align_keeps_input_order_fills_missing_and_drops_unknown_ids(self):
        items = [TriageItem(item_id="a", subject="A"), TriageItem(item_id="b", subject="B")]
        decisions = [
            TriageDecision(item_id="b", status="ok", action="archive", confidence=0.8),
            TriageDecision(item_id="zzz", status="ok", action="delete", confidence=0.99),
        ]

        aligned = align_decisions(items, decisions)

        self.assertEqual(["a", "b"], [d.item_id for d in aligned])
        self.assertEqual("failed", aligned[0].status)
        self.assertEqual("terminal", aligned[0].error_category)
        self.assertEqual("archive", aligned[1].action)


class GatePolicyTest(unittest.TestCase):
    def test_accepts_confident_non_delete_actions(self):
        for action in ("mark_read", "archive", "needs_reply"):
            with self.subTest(action=action):
                gate = apply_gate(_decision(action, 0.7))
                self.assertEqual(("accept", "accepted"), (gate.outcome, gate.rule))

    def test_low_confidence_falls_back_to_existing_model_path(self):
        gate = apply_gate(_decision("archive", 0.69))
        self.assertEqual(("fallback", "below_threshold"), (gate.outcome, gate.rule))

    def test_delete_needs_the_higher_threshold_and_otherwise_goes_to_review(self):
        self.assertEqual("accept", apply_gate(_decision("delete", 0.9)).outcome)
        gate = apply_gate(_decision("delete", 0.89))
        self.assertEqual(("review", "delete_below_threshold"), (gate.outcome, gate.rule))
        gate = apply_gate(_decision("delete", 0.2))
        self.assertEqual("review", gate.outcome)

    def test_failed_and_abstained_decisions_fall_back(self):
        failed = apply_gate(TriageDecision(item_id="e1", status="failed", error_category="transient"))
        abstained = apply_gate(TriageDecision(item_id="e1", status="abstained"))
        self.assertEqual(("fallback", "provider_failed"), (failed.outcome, failed.rule))
        self.assertEqual(("fallback", "abstained"), (abstained.outcome, abstained.rule))

    def test_thresholds_are_configurable(self):
        policy = GatePolicy(accept_threshold=0.5, delete_threshold=0.99)
        self.assertEqual("accept", apply_gate(_decision("archive", 0.5), policy).outcome)
        self.assertEqual("review", apply_gate(_decision("delete", 0.98), policy).outcome)


class ReasonTextTest(unittest.TestCase):
    def test_every_reason_code_has_bounded_en_and_zh_text(self):
        for code in ReasonCode:
            with self.subTest(code=code):
                self.assertIn(code, REASON_TEXT)
                for language in ("en", "zh"):
                    text = reason_text(code, language)
                    self.assertTrue(text)
                    self.assertLessEqual(len(text), 20)


class FakeDecisionProviderTest(unittest.TestCase):
    def test_returns_scripted_default_and_abstained_decisions_in_input_order(self):
        provider = FakeDecisionProvider(
            {
                "ci": ("delete", ReasonCode.CI_NOTIFICATION, 0.95),
                "unsure": None,
            }
        )
        items = [TriageItem(item_id=i, subject=i) for i in ("unsure", "ci", "other")]

        batch = provider.decide_triage(items)

        self.assertEqual(["unsure", "ci", "other"], [d.item_id for d in batch.decisions])
        self.assertEqual("abstained", batch.decisions[0].status)
        self.assertEqual(("delete", 0.95), (batch.decisions[1].action, batch.decisions[1].confidence))
        self.assertEqual("mark_read", batch.decisions[2].action)
        self.assertEqual([["unsure", "ci", "other"]], provider.calls)

    def test_can_simulate_provider_failure(self):
        provider = FakeDecisionProvider(error=TimeoutError("fake timeout"))
        with self.assertRaises(TimeoutError):
            provider.decide_triage([TriageItem(item_id="a", subject="A")])


if __name__ == "__main__":
    unittest.main()
