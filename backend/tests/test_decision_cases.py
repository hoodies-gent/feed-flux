import json
import re
import unittest

from pydantic import ValidationError

from app.evals.decision_cases import (
    DecisionCase,
    DecisionCaseSuite,
    language_groups,
    load_case_suite,
    triage_items,
)


def _case(**overrides) -> dict:
    base = {
        "case_id": "c1",
        "language": "en",
        "source": {"kind": "inline", "email": {
            "subject": "s", "sender_email": "a@b.test", "received": "2026-09-01T00:00:00Z", "body_preview": "p",
        }},
        "reply_needed": False,
        "default_policy_action": "archive",
        "acceptable_actions": ["archive"],
        "forbidden_actions": [],
        "note": "n",
    }
    return {**base, **overrides}


class LabelInvariantTest(unittest.TestCase):
    def test_default_action_must_be_acceptable(self):
        with self.assertRaisesRegex(ValidationError, "must be acceptable"):
            DecisionCase.model_validate(_case(default_policy_action="delete", acceptable_actions=["archive"]))

    def test_an_action_cannot_be_acceptable_and_forbidden(self):
        with self.assertRaisesRegex(ValidationError, "both acceptable and forbidden"):
            DecisionCase.model_validate(_case(acceptable_actions=["archive"], forbidden_actions=["archive"]))

    def test_reply_needed_must_agree_with_the_default_action(self):
        with self.assertRaisesRegex(ValidationError, "reply_needed disagrees"):
            DecisionCase.model_validate(_case(reply_needed=True))
        with self.assertRaisesRegex(ValidationError, "reply_needed disagrees"):
            DecisionCase.model_validate(
                _case(default_policy_action="needs_reply", acceptable_actions=["needs_reply"])
            )

    def test_rejects_actions_outside_the_contract(self):
        with self.assertRaises(ValidationError):
            DecisionCase.model_validate(_case(default_policy_action="snooze", acceptable_actions=["snooze"]))

    def test_rejects_duplicate_case_ids(self):
        with self.assertRaisesRegex(ValidationError, "duplicate case_id"):
            DecisionCaseSuite.model_validate(
                {"schema_version": 1, "suite_id": "s", "label_policy": {}, "cases": [_case(), _case()]}
            )


class ShippedSuiteTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.suite = load_case_suite()
        cls.items = triage_items(cls.suite)

    def test_loads_and_validates(self):
        self.assertEqual(1, self.suite.schema_version)
        self.assertGreaterEqual(len(self.suite.cases), 30)

    def test_every_language_group_is_large_enough_to_report_separately(self):
        groups = language_groups(self.suite)
        self.assertEqual({"en", "zh", "mixed"}, set(groups))
        for language, case_ids in groups.items():
            with self.subTest(language=language):
                self.assertGreaterEqual(len(case_ids), 6)

    def test_every_case_resolves_to_a_provider_input(self):
        self.assertEqual(len(self.suite.cases), len(self.items))
        for case in self.suite.cases:
            with self.subTest(case=case.case_id):
                item = self.items[case.case_id]
                self.assertTrue(item.subject)
                self.assertTrue(item.sender_email)
                self.assertTrue(item.body_preview)

    def test_covers_every_action_as_a_default_at_least_twice(self):
        counts = {}
        for case in self.suite.cases:
            counts[case.default_policy_action] = counts.get(case.default_policy_action, 0) + 1
        for action in ("mark_read", "archive", "delete", "needs_reply"):
            with self.subTest(action=action):
                self.assertGreaterEqual(counts.get(action, 0), 2)

    def test_has_adversarial_cases_where_delete_is_forbidden_in_every_language(self):
        by_language = {}
        for case in self.suite.cases:
            if "delete" in case.forbidden_actions:
                by_language.setdefault(case.language, []).append(case.case_id)
        for language in ("en", "zh", "mixed"):
            with self.subTest(language=language):
                self.assertTrue(by_language.get(language))

    def test_reply_needed_cases_never_allow_a_bulk_action(self):
        for case in self.suite.cases:
            if case.reply_needed:
                with self.subTest(case=case.case_id):
                    self.assertEqual(["needs_reply"], case.acceptable_actions)

    def test_case_ids_are_stable_slugs(self):
        for case in self.suite.cases:
            with self.subTest(case=case.case_id):
                self.assertRegex(case.case_id, r"^[a-z]+(-[a-z0-9]+)+$")
                self.assertTrue(case.case_id.startswith(case.language if case.language != "en" else "en"))

    def test_fixture_references_point_at_real_seed_rows(self):
        referenced = [c for c in self.suite.cases if c.source.kind == "fixture"]
        self.assertGreaterEqual(len(referenced), 6)
        for case in referenced:
            with self.subTest(case=case.case_id):
                self.assertTrue(self.items[case.case_id].subject)

    def test_inline_previews_stay_within_what_providers_receive(self):
        for case in self.suite.cases:
            if case.source.kind == "inline":
                with self.subTest(case=case.case_id):
                    self.assertLessEqual(len(case.source.email.body_preview), 400)

    def test_every_case_carries_a_reviewable_note(self):
        for case in self.suite.cases:
            with self.subTest(case=case.case_id):
                self.assertGreaterEqual(len(case.note), 20)

    def test_chinese_cases_actually_contain_chinese_text(self):
        han = re.compile(r"[一-鿿]")
        for case in self.suite.cases:
            item = self.items[case.case_id]
            text = f"{item.subject}{item.body_preview}"
            with self.subTest(case=case.case_id):
                if case.language == "en":
                    self.assertIsNone(han.search(text))
                else:
                    self.assertIsNotNone(han.search(text))

    def test_mixed_cases_contain_both_scripts(self):
        han = re.compile(r"[一-鿿]")
        latin = re.compile(r"[A-Za-z]{3,}")
        for case in self.suite.cases:
            if case.language == "mixed":
                item = self.items[case.case_id]
                text = f"{item.subject} {item.body_preview}"
                with self.subTest(case=case.case_id):
                    self.assertIsNotNone(han.search(text))
                    self.assertIsNotNone(latin.search(text))

    def test_file_is_utf8_json_without_escaped_chinese(self):
        from app.evals.decision_cases import DEFAULT_CASES_PATH

        raw = DEFAULT_CASES_PATH.read_text(encoding="utf-8")
        json.loads(raw)
        self.assertNotIn("\\u4e", raw)


if __name__ == "__main__":
    unittest.main()
