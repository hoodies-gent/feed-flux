import unittest

from app.decisions.contract import ReasonCode
from app.decisions.fake import FakeDecisionProvider
from app.evals.decision_ab import (
    ScriptedTriageLLM,
    case_database,
    run_arm,
    run_suite,
    scripted_llm_factory,
    select_cases,
    summarise,
)
from app.evals.decision_cases import attention_label, load_case_suite
from app.services.database import DatabaseService

SUITE = load_case_suite()


class CaseSelectionTest(unittest.TestCase):
    def test_selection_is_deterministic(self):
        self.assertEqual(
            [c.case_id for c in select_cases(SUITE, 12)],
            [c.case_id for c in select_cases(SUITE, 12)],
        )

    def test_every_size_keeps_all_three_attention_levels(self):
        for size in (6, 12, 20, 38):
            with self.subTest(size=size):
                cases = select_cases(SUITE, size)
                self.assertEqual(size, len(cases))
                self.assertEqual(
                    {"needs_reply", "important", "low_signal"},
                    {attention_label(c) for c in cases},
                )

    def test_small_sizes_are_balanced_rather_than_all_noise(self):
        labels = [attention_label(c) for c in select_cases(SUITE, 6)]

        self.assertEqual(2, labels.count("needs_reply"))
        self.assertEqual(2, labels.count("important"))
        self.assertEqual(2, labels.count("low_signal"))

    def test_asking_for_everything_returns_the_whole_suite(self):
        self.assertEqual(len(SUITE.cases), len(select_cases(SUITE, len(SUITE.cases))))

    def test_larger_sizes_contain_the_smaller_ones(self):
        small = {c.case_id for c in select_cases(SUITE, 6)}
        large = {c.case_id for c in select_cases(SUITE, 20)}

        self.assertTrue(small <= large)


class CaseDatabaseTest(unittest.TestCase):
    def test_cases_become_unread_mail_the_agent_can_read(self):
        cases = select_cases(SUITE, 6)

        with case_database(cases, SUITE) as path:
            rows = DatabaseService(path).get_unread_emails(limit=50)

        self.assertEqual({c.case_id for c in cases}, {r["id"] for r in rows})
        self.assertTrue(all(r["subject"] and r["body_preview"] for r in rows))

    def test_the_temporary_database_does_not_outlive_the_run(self):
        import os

        before = os.environ.get("FEEDFLUX_DB_PATH")
        with case_database(select_cases(SUITE, 6), SUITE) as path:
            inside = os.environ["FEEDFLUX_DB_PATH"]
        self.assertEqual(path, inside)
        self.assertEqual(before, os.environ.get("FEEDFLUX_DB_PATH"))


class ScriptedLlmTest(unittest.IsolatedAsyncioTestCase):
    async def test_baseline_output_tokens_grow_with_the_batch(self):
        sizes = {}
        for size in (6, 20):
            llm = ScriptedTriageLLM("baseline", select_cases(SUITE, size))
            llm.bind_tools([type("T", (), {"name": "apply_triage_batch"})()])
            message = await llm.ainvoke([])
            sizes[size] = message.usage_metadata["output_tokens"]

        self.assertGreater(sizes[20], sizes[6])
        self.assertEqual(109 * 14, sizes[20] - sizes[6])

    async def test_provider_arm_output_tokens_do_not_grow_with_the_batch(self):
        outputs = set()
        for size in (6, 20):
            llm = ScriptedTriageLLM("provider_tool", select_cases(SUITE, size))
            llm.bind_tools([type("T", (), {"name": "triage_unread"})()])
            outputs.add((await llm.ainvoke([])).usage_metadata["output_tokens"])

        self.assertEqual(1, len(outputs))


class RunArmTest(unittest.IsolatedAsyncioTestCase):
    async def test_baseline_run_produces_scored_buckets(self):
        cases = select_cases(SUITE, 6)

        record = await run_arm("baseline", cases, SUITE, scripted_llm_factory())

        self.assertEqual("baseline", record["arm"])
        self.assertEqual("dry_run", record["mode"])
        self.assertEqual(6, record["size"])
        self.assertEqual(6, len(record["buckets"]["dismissed"]))
        self.assertEqual(6, record["scores"]["overall"]["items"])
        self.assertGreater(record["agent_usage"]["output_tokens"], 0)
        self.assertGreater(record["latency_ms"], 0)

    async def test_provider_run_captures_the_plan_and_provider_stats(self):
        cases = select_cases(SUITE, 6)
        provider = FakeDecisionProvider(
            {c.case_id: ("mark_read", ReasonCode.NEWSLETTER, 0.95) for c in cases},
            importance={c.case_id: (False, 0.95) for c in cases},
        )

        record = await run_arm("provider_tool", cases, SUITE, scripted_llm_factory(), provider=provider)

        self.assertEqual("fake", record["provider"])
        self.assertEqual(1, record["provider_stats"]["requests"])
        self.assertEqual(6, len(record["buckets"]["dismissed"]))

    async def test_an_arm_that_never_calls_a_tool_scores_as_touching_nothing(self):
        class _Silent(ScriptedTriageLLM):
            async def ainvoke(self, messages, **kwargs):
                from langchain_core.messages import AIMessage

                return AIMessage(content="I would rather not.")

        cases = select_cases(SUITE, 6)
        record = await run_arm("baseline", cases, SUITE, lambda arm, cs: _Silent(arm, cs))

        self.assertEqual(6, len(record["buckets"]["untouched"]))
        self.assertEqual(0.0, record["scores"]["overall"]["coverage"])

    async def test_runs_never_touch_the_real_database(self):
        import os

        cases = select_cases(SUITE, 6)
        await run_arm("baseline", cases, SUITE, scripted_llm_factory())

        self.assertIsNone(os.environ.get("FEEDFLUX_DB_PATH"))


class RunSuiteTest(unittest.IsolatedAsyncioTestCase):
    async def test_covers_every_size_and_arm(self):
        records = await run_suite(
            [6, 12],
            ["baseline", "provider_tool"],
            scripted_llm_factory(),
            provider_factory=lambda arm_id: FakeDecisionProvider({}),
        )

        self.assertEqual(4, len(records))
        self.assertEqual({(6, "baseline"), (6, "provider_tool"), (12, "baseline"), (12, "provider_tool")},
                         {(r["size"], r["arm"]) for r in records})

    async def test_summary_reports_each_run_and_is_marked_as_a_dry_run(self):
        records = await run_suite([6], ["baseline"], scripted_llm_factory())

        summary = summarise(records)

        self.assertEqual(["dry_run"], summary["modes"])
        self.assertEqual(1, len(summary["runs"]))
        self.assertIn("attention_recall", summary["runs"][0])
        self.assertIn("agent_output_tokens", summary["runs"][0])


if __name__ == "__main__":
    unittest.main()
