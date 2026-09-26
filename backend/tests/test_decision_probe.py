import unittest

from app.decisions.contract import ReasonCode, TriageDecision, TriageDecisionBatch
from app.decisions.fake import FakeDecisionProvider
from app.decisions.settings import DecisionSettings
from app.evals.decision_cases import attention_label, is_important, load_case_suite, triage_items
from app.evals.decision_probe import (
    determinism,
    dry_run_probe_provider,
    gate_report,
    planned_requests,
    probe_provider,
    run_probe,
    summarise_provider,
)

SUITE = load_case_suite()
ITEMS = [triage_items(SUITE)[c.case_id] for c in sorted(SUITE.cases, key=lambda c: c.case_id)]
LOW_SIGNAL = next(c.case_id for c in sorted(SUITE.cases, key=lambda c: c.case_id) if not is_important(c))
ATTENTION = next(c.case_id for c in sorted(SUITE.cases, key=lambda c: c.case_id) if is_important(c))
FORBIDDEN = next(c for c in sorted(SUITE.cases, key=lambda c: c.case_id) if c.forbidden_actions)


def _one(item_id):
    return [i for i in ITEMS if i.item_id == item_id]


class _FlipProvider:
    """Answers differently on each call, so instability is detectable."""

    name = "flip"

    def __init__(self):
        self.calls = 0

    def decide_triage(self, items):
        self.calls += 1
        important = self.calls % 2 == 0
        return TriageDecisionBatch(
            provider=self.name,
            model="flip-1",
            decisions=[
                TriageDecision(
                    item_id=i.item_id,
                    status="ok",
                    action="mark_read",
                    confidence=0.9,
                    important=important,
                    importance_confidence=0.9,
                )
                for i in items
            ],
            requests=1,
        )


class ProbeShapeTest(unittest.TestCase):
    def test_records_one_answer_per_email_per_rep(self):
        observations, batches = probe_provider(FakeDecisionProvider({}), ITEMS, reps=2)

        self.assertEqual(2 * len(ITEMS), len(observations))
        self.assertEqual([1, 2], sorted({o.rep for o in batches and observations}))
        self.assertEqual(2, len(batches))

    def test_results_carry_no_email_content(self):
        observations, _ = probe_provider(FakeDecisionProvider({}), ITEMS[:1], reps=1)
        recorded = observations[0].__dict__

        self.assertNotIn("subject", recorded)
        self.assertNotIn("body_preview", recorded)
        self.assertNotIn("sender_email", recorded)

    def test_a_provider_reporting_no_usage_does_not_read_as_free(self):
        observations, batches = probe_provider(FakeDecisionProvider({}), ITEMS[:1], reps=1)

        self.assertFalse(batches[0]["usage_reported"])
        self.assertFalse(summarise_provider(observations, batches, SUITE)["usage_reported"])


class GateCeilingTest(unittest.TestCase):
    def test_a_confident_dismissal_of_noise_counts_toward_the_ceiling(self):
        provider = FakeDecisionProvider(
            {LOW_SIGNAL: ("mark_read", ReasonCode.NEWSLETTER, 0.9)},
            importance={LOW_SIGNAL: (False, 0.76)},
        )
        observations, _ = probe_provider(provider, _one(LOW_SIGNAL), reps=1)
        report = gate_report(observations, SUITE)

        self.assertEqual([], report["gate_blocked_low_signal"])
        self.assertAlmostEqual(1 / report["low_signal_cases"], report["dismissal_ceiling_median"], places=3)

    def test_an_unsure_importance_answer_is_blocked_by_the_shipped_gate(self):
        provider = FakeDecisionProvider(
            {LOW_SIGNAL: ("mark_read", ReasonCode.NEWSLETTER, 0.99)},
            importance={LOW_SIGNAL: (False, 0.3)},
        )
        observations, _ = probe_provider(provider, _one(LOW_SIGNAL), reps=1)
        report = gate_report(observations, SUITE)

        self.assertEqual([LOW_SIGNAL], report["gate_blocked_low_signal"])
        self.assertEqual(0.0, report["dismissal_ceiling_median"])

    def test_attention_worthy_mail_it_would_dismiss_is_named_not_counted_as_coverage(self):
        provider = FakeDecisionProvider(
            {ATTENTION: ("mark_read", ReasonCode.NEWSLETTER, 0.9)},
            importance={ATTENTION: (False, 0.9)},
        )
        observations, _ = probe_provider(provider, _one(ATTENTION), reps=1)
        report = gate_report(observations, SUITE)

        self.assertEqual([ATTENTION], report["dismissable_attention_items"])
        self.assertEqual(0.0, report["dismissal_ceiling_median"])

    def test_a_forbidden_four_way_answer_is_recorded_even_though_it_is_never_executed(self):
        """The arm metrics cannot see this: the plan only ever proposes mark_read, so
        the projection reports zero regardless of what the provider actually chose."""
        forbidden = FORBIDDEN.forbidden_actions[0]
        provider = FakeDecisionProvider(
            {FORBIDDEN.case_id: (forbidden, ReasonCode.OTHER, 0.9)},
            importance={FORBIDDEN.case_id: (False, 0.9)},
        )
        observations, _ = probe_provider(provider, _one(FORBIDDEN.case_id), reps=1)
        report = gate_report(observations, SUITE)

        self.assertEqual(1, report["forbidden_action_proposal_count"])
        self.assertEqual(forbidden, report["forbidden_action_proposals"][0]["action"])

    def test_a_failing_provider_produces_no_ceiling_credit(self):
        provider = FakeDecisionProvider({LOW_SIGNAL: None})
        observations, _ = probe_provider(provider, _one(LOW_SIGNAL), reps=1)
        report = gate_report(observations, SUITE)

        self.assertEqual(1, report["failed_observations"])
        self.assertEqual(0.0, report["dismissal_ceiling_median"])


class _JitterProvider:
    """Same answer every time, confidence wobbling slightly — what both real
    providers actually did."""

    name = "jitter"

    def __init__(self):
        self.calls = 0

    def decide_triage(self, items):
        self.calls += 1
        return TriageDecisionBatch(
            provider=self.name,
            model="jitter-1",
            decisions=[
                TriageDecision(
                    item_id=i.item_id,
                    status="ok",
                    action="mark_read",
                    confidence=0.9,
                    important=False,
                    importance_confidence=0.8 + 0.02 * self.calls,
                )
                for i in items
            ],
            requests=1,
        )


class DeterminismTest(unittest.TestCase):
    def test_a_stable_provider_is_reported_stable(self):
        observations, _ = probe_provider(
            FakeDecisionProvider(
                {LOW_SIGNAL: ("mark_read", ReasonCode.NEWSLETTER, 0.9)},
                importance={LOW_SIGNAL: (False, 0.8)},
            ),
            _one(LOW_SIGNAL),
            reps=2,
        )

        self.assertTrue(determinism(observations)["answers_stable"])

    def test_a_flipping_provider_is_caught_rather_than_assumed_stable(self):
        observations, _ = probe_provider(_FlipProvider(), _one(LOW_SIGNAL), reps=2)
        report = determinism(observations)

        self.assertFalse(report["answers_stable"])
        self.assertEqual(1, report["answer_flip_cases"])

    def test_confidence_jitter_is_not_counted_as_the_provider_changing_its_mind(self):
        """Both live providers jittered by <=0.1 while never once changing an answer.
        One combined number called that unstable, which is the opposite of true."""
        observations, _ = probe_provider(_JitterProvider(), _one(LOW_SIGNAL), reps=3)
        report = determinism(observations)

        self.assertTrue(report["answers_stable"])
        self.assertEqual(0, report["answer_flip_cases"])
        self.assertEqual(1, report["confidence_jitter_cases"])
        self.assertAlmostEqual(0.04, report["max_importance_confidence_spread"], places=3)

    def test_one_rep_is_never_called_stable(self):
        observations, _ = probe_provider(FakeDecisionProvider({}), _one(LOW_SIGNAL), reps=1)

        self.assertFalse(determinism(observations)["answers_stable"])


class InterleavingTest(unittest.TestCase):
    def test_providers_alternate_rep_by_rep_rather_than_running_in_blocks(self):
        """Two providers measured in separate blocks are measured under separate
        network conditions, so comparing their latency compares the blocks too."""
        order: list[str] = []

        class _Recording(FakeDecisionProvider):
            def __init__(self, name):
                super().__init__({})
                self.name = name

            def decide_triage(self, items):
                order.append(self.name)
                return super().decide_triage(items)

        _, _, batches = run_probe(
            ["jev", "llm"], reps=3, provider_factory=_Recording, suite=SUITE
        )

        self.assertEqual(["jev", "llm"] * 3, order)
        self.assertEqual(
            [("jev", 1), ("llm", 1), ("jev", 2), ("llm", 2), ("jev", 3), ("llm", 3)],
            [(b["provider"], b["rep"]) for b in batches],
        )

    def test_each_provider_is_built_once_not_once_per_rep(self):
        built: list[str] = []

        def _factory(name):
            built.append(name)
            provider = FakeDecisionProvider({})
            provider.name = name
            return provider

        run_probe(["jev", "llm"], reps=3, provider_factory=_factory, suite=SUITE)

        self.assertEqual(["jev", "llm"], built)

    def test_every_provider_still_gets_its_own_summary(self):
        summary, observations, _ = run_probe(
            ["jev", "llm"], reps=2, provider_factory=dry_run_probe_provider_named(), suite=SUITE
        )

        self.assertEqual({"jev", "llm"}, set(summary))
        self.assertEqual(2, summary["jev"]["reps"])
        self.assertEqual(2 * 2 * len(SUITE.cases), len(observations))


def dry_run_probe_provider_named():
    return lambda name: dry_run_probe_provider(SUITE.cases, name)


class BudgetTest(unittest.TestCase):
    def test_request_count_follows_chunking_reps_and_providers(self):
        settings = DecisionSettings(max_items_per_request=20)

        self.assertEqual(2, planned_requests(38, 1, 1, settings))
        self.assertEqual(12, planned_requests(38, 2, 3, settings))
        self.assertEqual(3, planned_requests(38, 1, 3, DecisionSettings(max_items_per_request=50)))


class DryRunTest(unittest.TestCase):
    def test_stand_in_answers_come_from_position_not_from_labels(self):
        provider = dry_run_probe_provider(SUITE.cases, "jev")
        summary, observations, batches = run_probe(
            ["jev"], reps=1, provider_factory=lambda name: provider, suite=SUITE
        )

        self.assertEqual("jev", summary["jev"]["provider"])
        self.assertEqual(len(SUITE.cases), len(observations))
        self.assertEqual(1, len(batches))
        # Position-derived answers cannot agree with the labels by construction.
        gold = {c.case_id: attention_label(c) for c in SUITE.cases}
        self.assertGreater(len({o.important for o in observations}), 1)
        self.assertGreater(len(set(gold.values())), 1)


if __name__ == "__main__":
    unittest.main()
