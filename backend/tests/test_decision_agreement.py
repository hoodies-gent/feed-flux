import unittest

from app.evals.decision_agreement import (
    AnnotationSet,
    agreement_report,
    fleiss_kappa,
    load_annotations,
    majority_labels,
    shipped_label_mismatches,
)
from app.evals.decision_cases import load_case_suite


def _annotation(case_id, reply=False, default="archive", acceptable=None, forbidden=None):
    return {
        "case_id": case_id,
        "reply_needed": reply,
        "default_policy_action": default,
        "acceptable_actions": acceptable or [default],
        "forbidden_actions": forbidden or [],
    }


def _annotations(*per_annotator) -> AnnotationSet:
    return AnnotationSet.model_validate(
        {
            "schema_version": 1,
            "suite_id": "t",
            "protocol": "p",
            "caveat": "c",
            "annotators": [
                {"annotator_id": chr(65 + i), "model_family": "m", "cases": cases}
                for i, cases in enumerate(per_annotator)
            ],
        }
    )


class FleissKappaTest(unittest.TestCase):
    def test_perfect_agreement_on_a_mixed_population(self):
        observed, kappa = fleiss_kappa([["a", "a", "a"], ["b", "b", "b"], ["a", "a", "a"], ["b", "b", "b"]])
        self.assertEqual(1.0, observed)
        self.assertEqual(1.0, kappa)

    def test_unanimity_on_a_single_category_has_no_room_for_chance(self):
        _, kappa = fleiss_kappa([["a", "a", "a"], ["a", "a", "a"]])
        self.assertEqual(1.0, kappa)

    def test_chance_level_agreement_lands_near_zero(self):
        ratings = [["a", "a", "b"], ["b", "b", "a"], ["a", "b", "b"], ["b", "a", "a"]]
        _, kappa = fleiss_kappa(ratings)
        self.assertLess(abs(kappa), 0.4)

    def test_partial_agreement_falls_between(self):
        ratings = [["a", "a", "a"], ["b", "b", "b"], ["a", "a", "b"], ["b", "b", "a"]]
        observed, kappa = fleiss_kappa(ratings)
        self.assertAlmostEqual(0.6667, observed, places=3)
        self.assertGreater(kappa, 0.3)
        self.assertLess(kappa, 1.0)

    def test_rejects_ragged_or_single_rater_input(self):
        with self.assertRaises(ValueError):
            fleiss_kappa([["a", "a"], ["a"]])
        with self.assertRaises(ValueError):
            fleiss_kappa([["a"], ["b"]])


class MajorityResolutionTest(unittest.TestCase):
    def test_two_of_three_wins_each_field(self):
        labels, unresolved = majority_labels(
            _annotations(
                [_annotation("c1", default="archive", acceptable=["archive", "delete"], forbidden=["delete"])],
                [_annotation("c1", default="archive", acceptable=["archive"], forbidden=[])],
                [_annotation("c1", default="mark_read", acceptable=["mark_read", "archive"], forbidden=[])],
            )
        )
        self.assertEqual([], unresolved)
        label = labels["c1"]
        self.assertEqual("archive", label.default_policy_action)
        self.assertEqual(["archive"], label.acceptable_actions)
        self.assertEqual([], label.forbidden_actions)

    def test_a_three_way_split_is_left_unresolved_rather_than_tie_broken(self):
        labels, unresolved = majority_labels(
            _annotations(
                [_annotation("c1", default="archive")],
                [_annotation("c1", default="delete")],
                [_annotation("c1", default="mark_read")],
            )
        )
        self.assertEqual(["c1"], unresolved)
        self.assertNotIn("c1", labels)

    def test_forbidden_needs_two_votes_not_one(self):
        labels, _ = majority_labels(
            _annotations(
                [_annotation("c1", forbidden=["delete"])],
                [_annotation("c1", forbidden=["delete"])],
                [_annotation("c1", forbidden=[])],
            )
        )
        self.assertEqual(["delete"], labels["c1"].forbidden_actions)


class ShippedSuiteAgreementTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.annotations = load_annotations()
        cls.suite = load_case_suite()
        cls.report = agreement_report(cls.annotations)

    def test_three_annotators_cover_every_case(self):
        self.assertEqual(3, len(self.annotations.annotators))
        case_ids = {case.case_id for case in self.suite.cases}
        for annotator in self.annotations.annotators:
            with self.subTest(annotator=annotator.annotator_id):
                self.assertEqual(case_ids, {case.case_id for case in annotator.cases})

    def test_annotators_are_not_all_the_same_model_family(self):
        families = {annotator.model_family for annotator in self.annotations.annotators}
        self.assertGreater(len(families), 1)

    def test_shipped_labels_are_exactly_the_majority_of_the_annotators(self):
        self.assertEqual([], shipped_label_mismatches(self.suite, self.annotations))

    def test_every_case_has_a_majority(self):
        self.assertEqual([], self.report["unresolved_cases"])

    def test_the_gate_metric_labels_are_reproducible_across_annotators(self):
        self.assertGreaterEqual(self.report["labels"]["reply_needed"]["fleiss_kappa"], 0.8)
        self.assertGreaterEqual(self.report["labels"]["forbids_delete"]["fleiss_kappa"], 0.8)

    def test_delete_is_forbidden_unanimously_wherever_it_is_forbidden(self):
        forbidding = {
            annotator.annotator_id: {
                case.case_id for case in annotator.cases if "delete" in case.forbidden_actions
            }
            for annotator in self.annotations.annotators
        }
        unanimous = set.intersection(*forbidding.values())
        shipped = {case.case_id for case in self.suite.cases if "delete" in case.forbidden_actions}
        self.assertEqual(unanimous, shipped)
        self.assertGreaterEqual(len(shipped), 20)

    def test_report_flags_labels_whose_kappa_is_unstable(self):
        self.assertTrue(self.report["labels"]["forbids_mark_read"]["rare_label"])
        self.assertNotIn("rare_label", self.report["labels"]["default_policy_action"])


if __name__ == "__main__":
    unittest.main()
