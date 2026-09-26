import unittest
from unittest import mock

from langchain_core.messages import AIMessage

from app.agent.graph import SYSTEM_PROMPT, build_agent
from app.agent.stream import new_turn_input
from app.decisions.contract import ReasonCode, TriageItem
from app.decisions.fake import FakeDecisionProvider
from app.evals.decision_arms import (
    TRIAGE_TOOL_OVERRIDE,
    ArmResult,
    build_arm,
    build_arm_agent,
    recording_triage_tool,
)


def _items(*ids):
    return [TriageItem(item_id=i, subject=f"subject {i}", sender_email="a@b.test", body_preview="p") for i in ids]


class _ScriptedLLM:
    """Emits a scripted tool call on the first turn, then a plain reply."""

    def __init__(self, tool_name=None, tool_args=None):
        self.tool_name = tool_name
        self.tool_args = tool_args or {}
        self.bound_tools = []
        self.prompts = []
        self.turns = 0

    def bind_tools(self, tools, **kwargs):
        self.bound_tools = [t.name for t in tools]
        return self

    async def ainvoke(self, messages, **kwargs):
        self.prompts.append(messages[0].content if messages else "")
        self.turns += 1
        if self.turns == 1 and self.tool_name:
            return AIMessage(
                content="",
                tool_calls=[{"name": self.tool_name, "args": self.tool_args, "id": "call-1"}],
            )
        return AIMessage(content="done")


class ArmCompositionTest(unittest.TestCase):
    def test_baseline_arm_is_the_shipped_agent(self):
        arm = build_arm("baseline")

        self.assertEqual(SYSTEM_PROMPT, arm.system_prompt)
        names = {t.name for t in arm.tools}
        self.assertIn("apply_triage_batch", names)
        self.assertIn("list_unread_emails", names)
        self.assertNotIn("triage_unread", names)

    def test_provider_arm_swaps_the_hand_classification_path_for_one_tool(self):
        arm = build_arm("provider_tool", provider=FakeDecisionProvider({}))

        names = {t.name for t in arm.tools}
        self.assertIn("triage_unread", names)
        self.assertNotIn("apply_triage_batch", names)
        self.assertNotIn("list_unread_emails", names)
        self.assertEqual("fake", arm.provider_name)

    def test_provider_arm_keeps_every_unrelated_tool(self):
        baseline = {t.name for t in build_arm("baseline").tools}
        arm = {t.name for t in build_arm("provider_tool", provider=FakeDecisionProvider({})).tools}

        self.assertEqual(baseline - {"apply_triage_batch", "list_unread_emails"}, arm - {"triage_unread"})

    def test_the_prompt_differs_only_by_an_appended_override(self):
        arm = build_arm("provider_tool", provider=FakeDecisionProvider({}))

        self.assertTrue(arm.system_prompt.startswith(SYSTEM_PROMPT))
        self.assertEqual(TRIAGE_TOOL_OVERRIDE, arm.system_prompt[len(SYSTEM_PROMPT):])


class RecordingToolTest(unittest.TestCase):
    def test_captures_the_plan_the_agent_actually_produced(self):
        provider = FakeDecisionProvider(
            {"a": ("mark_read", ReasonCode.NEWSLETTER, 0.95)}, importance={"a": (False, 0.9)}
        )
        result = ArmResult()
        recorded = recording_triage_tool(provider, result)

        with mock.patch("app.evals.decision_arms._unread_items", return_value=_items("a")):
            output = recorded.invoke({"limit": 20})

        self.assertEqual(1, len(result.plans))
        self.assertEqual(["a"], [i.email_id for i in result.plans[0].bulk])
        self.assertIn("PLAN READY", output)

    def test_takes_no_per_email_arguments(self):
        recorded = recording_triage_tool(FakeDecisionProvider({}), ArmResult())

        self.assertEqual({"limit", "language"}, set(recorded.args_schema.model_json_schema()["properties"]))

    def test_shows_the_model_exactly_what_the_shipped_tool_shows(self):
        from app.agent.triage_tools import triage_unread as shipped

        recorded = recording_triage_tool(FakeDecisionProvider({}), ArmResult())

        self.assertEqual(shipped.description, recorded.description)
        self.assertEqual(shipped.args_schema, recorded.args_schema)

    def test_result_text_matches_the_shipped_tool_for_the_same_plan(self):
        from app.agent.triage_tools import build_triage_plan as build, plan_summary

        provider = FakeDecisionProvider({"a": ("mark_read", ReasonCode.NEWSLETTER, 0.95)})
        result = ArmResult()
        recorded = recording_triage_tool(provider, result)

        with mock.patch("app.evals.decision_arms._unread_items", return_value=_items("a")):
            output = recorded.invoke({"limit": 20})

        self.assertEqual(plan_summary(build(_items("a"), provider)), output)


class ArmAgentTest(unittest.IsolatedAsyncioTestCase):
    async def _run(self, arm, llm):
        agent = build_arm_agent(arm, llm)
        await agent.ainvoke(
            new_turn_input("triage my unread"),
            config={"configurable": {"thread_id": "arm-test"}},
        )

    async def test_provider_arm_binds_the_single_tool_and_runs_it(self):
        provider = FakeDecisionProvider(
            {"a": ("mark_read", ReasonCode.NEWSLETTER, 0.95)}, importance={"a": (False, 0.9)}
        )
        arm = build_arm("provider_tool", provider=provider)
        llm = _ScriptedLLM("triage_unread", {"limit": 20, "language": "en"})

        with mock.patch("app.evals.decision_arms._unread_items", return_value=_items("a")):
            await self._run(arm, llm)

        self.assertIn("triage_unread", llm.bound_tools)
        self.assertNotIn("apply_triage_batch", llm.bound_tools)
        self.assertEqual(1, len(arm.result.plans))
        self.assertTrue(llm.prompts[0].endswith(TRIAGE_TOOL_OVERRIDE))

    async def test_baseline_arm_still_binds_the_shipped_tool_set_and_prompt(self):
        arm = build_arm("baseline")
        llm = _ScriptedLLM()

        await self._run(arm, llm)

        self.assertIn("apply_triage_batch", llm.bound_tools)
        self.assertNotIn("triage_unread", llm.bound_tools)
        self.assertEqual(SYSTEM_PROMPT, llm.prompts[0])


class BuildAgentOverrideTest(unittest.IsolatedAsyncioTestCase):
    async def test_defaults_are_unchanged_when_no_override_is_passed(self):
        llm = _ScriptedLLM()
        agent = build_agent(llm=llm)

        await agent.ainvoke(
            new_turn_input("hello"), config={"configurable": {"thread_id": "default-test"}}
        )

        self.assertIn("apply_triage_batch", llm.bound_tools)
        self.assertEqual(SYSTEM_PROMPT, llm.prompts[0])


if __name__ == "__main__":
    unittest.main()
