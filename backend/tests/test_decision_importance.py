import unittest

from app.decisions.contract import IMPORTANCE_CRITERIA, ReasonCode, TriageDecision, TriageItem
from app.decisions.fake import FakeDecisionProvider
from app.decisions.jev import JevDecisionProvider
from app.decisions.policy import GatePolicy, apply_gate
from app.evals.decision_cases import (
    attention_groups,
    attention_label,
    is_important,
    load_case_suite,
)
from app.decisions.settings import DecisionSettings


def _decision(action="archive", confidence=0.95, important=None, importance_confidence=None):
    return TriageDecision(
        item_id="e1",
        status="ok",
        action=action,
        reason_code=ReasonCode.OTHER,
        confidence=confidence,
        important=important,
        importance_confidence=importance_confidence,
    )


class ImportanceGateTest(unittest.TestCase):
    def test_confident_importance_blocks_a_confident_removal(self):
        for action in ("archive", "delete"):
            with self.subTest(action=action):
                gate = apply_gate(_decision(action, 0.99, important=True, importance_confidence=0.9))
                self.assertEqual(("review", "important_bulk_action"), (gate.outcome, gate.rule))
                self.assertTrue(gate.important)

    def test_importance_does_not_block_keeping_the_mail_in_the_inbox(self):
        gate = apply_gate(_decision("mark_read", 0.95, important=True, importance_confidence=0.99))
        self.assertEqual("accept", gate.outcome)

    def test_importance_does_not_block_a_needs_reply_proposal(self):
        gate = apply_gate(_decision("needs_reply", 0.95, important=True, importance_confidence=0.99))
        self.assertEqual("accept", gate.outcome)

    def test_unconfident_importance_does_not_override_the_action(self):
        gate = apply_gate(_decision("archive", 0.95, important=True, importance_confidence=0.5))
        self.assertEqual("accept", gate.outcome)

    def test_behaviour_is_unchanged_when_the_provider_omits_importance(self):
        self.assertEqual("accept", apply_gate(_decision("archive", 0.95)).outcome)
        self.assertEqual("accept", apply_gate(_decision("delete", 0.95)).outcome)
        unconfident_delete = apply_gate(_decision("delete", 0.5))
        self.assertEqual(("review", "delete_below_threshold"), (unconfident_delete.outcome, unconfident_delete.rule))

    def test_not_important_leaves_the_existing_rules_in_charge(self):
        gate = apply_gate(_decision("delete", 0.8, important=False, importance_confidence=0.95))
        self.assertEqual(("review", "delete_below_threshold"), (gate.outcome, gate.rule))

    def test_threshold_is_configurable(self):
        policy = GatePolicy(importance_threshold=0.4)
        gate = apply_gate(_decision("archive", 0.95, important=True, importance_confidence=0.5), policy)
        self.assertEqual("review", gate.outcome)


class JevImportanceQuestionTest(unittest.TestCase):
    class _Session:
        def __init__(self, noul=None):
            self.noul = noul
            self.payloads = []

        def post(self, url, json=None, headers=None, timeout=None):
            self.payloads.append(json)
            answers = {}
            for key in json["questions"]:
                if key.startswith("action_"):
                    answers[key] = {"choice": "archive", "confidence": 0.9}
                elif key.startswith("reason_"):
                    answers[key] = {"choice": str(ReasonCode.RECEIPT)}
                elif self.noul is not None:
                    answers[key] = {"type": "noul", "noul": self.noul}
            return type("R", (), {"status_code": 200, "json": lambda _self: {"answers": answers}})()

    def _provider(self, session):
        return JevDecisionProvider(
            DecisionSettings(mode="shadow", api_key="k"), session=session, sleep=lambda _: None
        )

    def _item(self):
        return [TriageItem(item_id="i1", subject="s", sender_email="a@b.test", body_preview="p")]

    def test_asks_importance_as_a_noul_in_the_same_request(self):
        session = self._Session(noul=0.9)
        self._provider(session).decide_triage(self._item())

        questions = session.payloads[0]["questions"]
        self.assertEqual(1, len(session.payloads))
        self.assertIn("important_e0", questions)
        self.assertEqual("noul", questions["important_e0"]["type"])
        self.assertEqual(IMPORTANCE_CRITERIA, questions["important_e0"]["criteria"])

    def test_maps_a_high_noul_to_important_with_high_confidence(self):
        decision = self._provider(self._Session(noul=0.95)).decide_triage(self._item()).decisions[0]
        self.assertTrue(decision.important)
        self.assertAlmostEqual(0.9, decision.importance_confidence, places=6)

    def test_maps_a_low_noul_to_not_important_with_high_confidence(self):
        decision = self._provider(self._Session(noul=0.02)).decide_triage(self._item()).decisions[0]
        self.assertFalse(decision.important)
        self.assertAlmostEqual(0.96, decision.importance_confidence, places=6)

    def test_maps_an_undecided_noul_to_low_confidence(self):
        decision = self._provider(self._Session(noul=0.5)).decide_triage(self._item()).decisions[0]
        self.assertAlmostEqual(0.0, decision.importance_confidence, places=6)

    def test_missing_importance_answer_leaves_the_field_unset(self):
        decision = self._provider(self._Session(noul=None)).decide_triage(self._item()).decisions[0]
        self.assertIsNone(decision.important)
        self.assertIsNone(decision.importance_confidence)
        self.assertEqual("ok", decision.status)


class FakeImportanceTest(unittest.TestCase):
    def test_importance_can_be_scripted_per_item(self):
        provider = FakeDecisionProvider(importance={"a": (True, 0.9)})
        decisions = provider.decide_triage(
            [TriageItem(item_id="a", subject="a"), TriageItem(item_id="b", subject="b")]
        ).decisions

        self.assertEqual((True, 0.9), (decisions[0].important, decisions[0].importance_confidence))
        self.assertIsNone(decisions[1].important)


class GoldImportanceTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.suite = load_case_suite()

    def test_attention_axis_partitions_every_case(self):
        groups = attention_groups(self.suite)
        self.assertEqual({"needs_reply", "important", "low_signal"}, set(groups))
        self.assertEqual(len(self.suite.cases), sum(len(v) for v in groups.values()))
        for label, ids in groups.items():
            with self.subTest(label=label):
                self.assertGreaterEqual(len(ids), 10)

    def test_importance_is_the_union_of_reply_needed_and_protected_cases(self):
        for case in self.suite.cases:
            with self.subTest(case=case.case_id):
                self.assertEqual(
                    case.reply_needed or bool(case.forbidden_actions), is_important(case)
                )
                self.assertEqual(is_important(case), attention_label(case) != "low_signal")

    def test_each_attention_bucket_covers_every_language(self):
        seen: dict[str, set[str]] = {}
        for case in self.suite.cases:
            seen.setdefault(attention_label(case), set()).add(case.language)
        for label, languages in seen.items():
            with self.subTest(label=label):
                self.assertEqual({"en", "zh", "mixed"}, languages)

    def test_low_signal_cases_never_forbid_an_action(self):
        for case in self.suite.cases:
            if attention_label(case) == "low_signal":
                with self.subTest(case=case.case_id):
                    self.assertEqual([], case.forbidden_actions)


if __name__ == "__main__":
    unittest.main()
