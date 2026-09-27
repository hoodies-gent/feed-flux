import unittest

from app.agent.triage_tools import PlanItem, TriagePlan
from app.evals.decision_cases import DecisionCase, load_case_suite
from app.evals.decision_metrics import (
    ArmBuckets,
    buckets_from_plan,
    buckets_from_triage_batch,
    score_arm,
    score_buckets,
)


def _case(case_id, *, reply=False, forbidden=None, default="mark_read", language="en") -> DecisionCase:
    acceptable = [default] if not reply else ["needs_reply"]
    return DecisionCase.model_validate(
        {
            "case_id": case_id,
            "language": language,
            "source": {"kind": "inline", "email": {
                "subject": "s", "sender_email": "a@b.test",
                "received": "2026-09-01T00:00:00Z", "body_preview": "p",
            }},
            "reply_needed": reply,
            "default_policy_action": "needs_reply" if reply else default,
            "acceptable_actions": acceptable,
            "forbidden_actions": forbidden or [],
            "note": "a note long enough to pass validation",
        }
    )


# reply=needs attention, forbidden=important, neither=low signal
CASES = [
    _case("ask", reply=True),
    _case("invoice", forbidden=["delete"]),
    _case("news"),
    _case("promo"),
]


def _plan(needs_reply=(), important=(), bulk=(), unresolved=()) -> TriagePlan:
    make = lambda ids: [PlanItem(email_id=i, reason="r") for i in ids]
    return TriagePlan(
        needs_reply=make(needs_reply),
        important=make(important),
        bulk=make(bulk),
        unresolved=make(unresolved),
    )


class BucketMappingTest(unittest.TestCase):
    def test_reads_a_plan_into_arm_buckets(self):
        buckets = buckets_from_plan(_plan(["ask"], ["invoice"], ["news"], ["promo"]))

        self.assertEqual({"ask", "invoice"}, buckets.surfaced)
        self.assertEqual({"news"}, buckets.dismissed)
        self.assertEqual({"promo"}, buckets.untouched)
        self.assertEqual({}, buckets.destructive)

    def test_baseline_bulk_actions_all_count_as_dismissals(self):
        args = {
            "actions": [
                {"email_id": "news", "action": "mark_read"},
                {"email_id": "invoice", "action": "archive"},
                {"email_id": "promo", "action": "delete"},
            ],
            "needs_reply": [{"email_id": "ask"}],
        }

        buckets = buckets_from_triage_batch(args, ["ask", "invoice", "news", "promo"])

        self.assertEqual({"ask"}, buckets.needs_reply)
        self.assertEqual({"news", "invoice", "promo"}, buckets.dismissed)
        self.assertEqual({"invoice": "archive", "promo": "delete"}, buckets.destructive)
        self.assertEqual(set(), buckets.untouched)

    def test_emails_the_baseline_never_mentions_count_as_untouched(self):
        buckets = buckets_from_triage_batch({"actions": [{"email_id": "news", "action": "mark_read"}]},
                                            ["news", "ask", "promo"])

        self.assertEqual({"ask", "promo"}, buckets.untouched)


class BuryingTest(unittest.TestCase):
    def test_perfect_arm_surfaces_everything_that_matters(self):
        score = score_buckets(buckets_from_plan(_plan(["ask"], ["invoice"], ["news", "promo"])), CASES)

        self.assertEqual(1.0, score["attention_recall"])
        self.assertEqual([], score["buried"])
        self.assertEqual(0, score["false_surfacing"])
        self.assertEqual(2, score["one_click_dismissals"])

    def test_dismissing_something_that_matters_is_counted_as_buried(self):
        score = score_buckets(buckets_from_plan(_plan(["ask"], [], ["invoice", "news", "promo"])), CASES)

        self.assertEqual(0.5, score["attention_recall"])
        self.assertEqual(["invoice"], score["buried"])

    def test_leaving_something_untouched_is_not_burying_it(self):
        score = score_buckets(buckets_from_plan(_plan(["ask"], [], ["news"], ["invoice", "promo"])), CASES)

        self.assertEqual([], score["buried"])
        self.assertEqual(0.5, score["attention_recall"])
        self.assertEqual(2, score["untouched"])
        self.assertEqual(0.5, score["coverage"])

    def test_destructive_dismissal_of_important_mail_is_reported_separately(self):
        args = {
            "actions": [
                {"email_id": "invoice", "action": "delete"},
                {"email_id": "news", "action": "mark_read"},
            ],
            "needs_reply": [{"email_id": "ask"}],
        }

        score = score_buckets(buckets_from_triage_batch(args, [c.case_id for c in CASES]), CASES)

        self.assertEqual(["invoice"], score["buried"])
        self.assertEqual(["invoice"], score["buried_destructively"])
        self.assertEqual(["invoice"], score["forbidden_action_violations"])

    def test_an_arm_that_only_marks_read_can_never_violate_a_forbidden_action(self):
        score = score_buckets(buckets_from_plan(_plan([], [], ["ask", "invoice", "news", "promo"])), CASES)

        self.assertEqual([], score["buried_destructively"])
        self.assertEqual([], score["forbidden_action_violations"])
        self.assertEqual(["ask", "invoice"], score["buried"])


class ReviewEffortTest(unittest.TestCase):
    def test_effort_counts_everything_the_user_still_handles(self):
        score = score_buckets(buckets_from_plan(_plan(["ask"], ["invoice"], ["news"], ["promo"])), CASES)

        self.assertEqual(3, score["review_effort"])
        self.assertEqual(1, score["one_click_dismissals"])

    def test_an_arm_that_touches_nothing_saves_no_effort(self):
        score = score_buckets(ArmBuckets(untouched={c.case_id for c in CASES}), CASES)

        self.assertEqual(4, score["review_effort"])
        self.assertEqual(0.0, score["coverage"])
        self.assertEqual(0.0, score["attention_recall"])
        self.assertEqual([], score["buried"])

    def test_surfacing_low_signal_mail_is_counted_as_noise(self):
        score = score_buckets(buckets_from_plan(_plan(["ask"], ["invoice", "news", "promo"])), CASES)

        self.assertEqual(1.0, score["attention_recall"])
        self.assertEqual(2, score["false_surfacing"])
        self.assertEqual(4, score["review_effort"])


class NeedsReplyTest(unittest.TestCase):
    def test_precision_and_recall_of_the_reply_bucket(self):
        score = score_buckets(buckets_from_plan(_plan(["ask", "news"], [], ["invoice", "promo"])), CASES)

        self.assertEqual(1.0, score["needs_reply"]["recall"])
        self.assertEqual(0.5, score["needs_reply"]["precision"])

    def test_missing_the_reply_entirely(self):
        score = score_buckets(buckets_from_plan(_plan([], ["ask"], ["invoice", "news", "promo"])), CASES)

        self.assertEqual(0.0, score["needs_reply"]["recall"])
        self.assertEqual(0.0, score["needs_reply"]["f1"])


class GroupingTest(unittest.TestCase):
    def test_reports_each_language_attention_level_and_origin_separately(self):
        suite = load_case_suite()
        buckets = ArmBuckets(dismissed={c.case_id for c in suite.cases})

        report = score_arm(buckets, suite.cases)

        self.assertEqual({"en", "zh", "mixed"}, set(report["by_language"]))
        self.assertEqual({"needs_reply", "important", "low_signal"}, set(report["by_attention"]))
        self.assertEqual({"fixture", "inline"}, set(report["by_origin"]))
        self.assertEqual(0.0, report["overall"]["attention_recall"])

    def test_group_scores_only_count_their_own_cases(self):
        cases = [_case("en-1", language="en"), _case("zh-1", reply=True, language="zh")]
        buckets = ArmBuckets(needs_reply={"zh-1"}, dismissed={"en-1"})

        report = score_arm(buckets, cases)

        self.assertIsNone(report["by_language"]["en"]["attention_recall"])
        self.assertEqual(1.0, report["by_language"]["zh"]["attention_recall"])
        self.assertEqual(1, report["by_language"]["en"]["one_click_dismissals"])
        self.assertEqual(0, report["by_language"]["zh"]["one_click_dismissals"])


if __name__ == "__main__":
    unittest.main()
