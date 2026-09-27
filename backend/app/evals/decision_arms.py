from dataclasses import dataclass, field, replace
from typing import Callable, Literal

from langchain_core.tools import tool

from app.agent.graph import SYSTEM_PROMPT, build_agent
from app.agent.tools import TOOLS
from app.agent.triage_tools import (
    TriagePlan,
    TriageUnreadInput,
    _unread_items,
    build_provider,
    build_triage_plan,
    plan_summary,
)
from app.agent.triage_tools import triage_unread as production_triage_unread
from app.decisions.contract import DecisionProvider
from app.decisions.settings import DecisionSettings, decision_settings

ArmId = Literal["baseline", "provider_tool"]

# Appended rather than surgically replacing the batch-triage section: keeping the
# prompt otherwise identical means a measured difference comes from the tool call
# itself, not from a shorter prompt. Deleting the old section would save more
# input tokens, which this experiment deliberately does not claim.
TRIAGE_TOOL_OVERRIDE = (
    "\n\nBATCH TRIAGE OVERRIDE (supersedes the batch triage workflow above):\n"
    "To process, triage, clear or review a batch of unread email, make exactly ONE "
    "call to triage_unread(limit, language) and nothing else. Do NOT call "
    "list_unread_emails, do NOT call apply_triage_batch, and do NOT classify the "
    "emails yourself. Pick `limit` from the user's ask the same way, and set "
    "`language` to the language of their message. After the tool returns, reply with "
    "ONE short line in the user's language and STOP."
)


@dataclass
class ArmResult:
    plans: list[TriagePlan] = field(default_factory=list)


def recording_triage_tool(provider: DecisionProvider, result: ArmResult):
    """The production tool with its provider injected and its plan captured. It
    reuses the shipped builder, description and result text, so the only thing the
    model sees differently between arms is which tool exists."""

    def triage_unread(limit: int = 20, language: str = "en") -> str:
        plan = build_triage_plan(
            _unread_items(limit), provider, language="zh" if language == "zh" else "en"
        )
        result.plans.append(plan)
        return plan_summary(plan)

    triage_unread.__doc__ = production_triage_unread.description
    return tool("triage_unread", args_schema=TriageUnreadInput)(triage_unread)


def baseline_tools() -> list:
    return list(TOOLS)


def provider_tools(provider: DecisionProvider, result: ArmResult) -> list:
    """Everything the agent normally has, minus the hand-classification path."""
    kept = [t for t in TOOLS if t.name not in {"apply_triage_batch", "list_unread_emails"}]
    return [*kept, recording_triage_tool(provider, result)]


@dataclass
class Arm:
    arm_id: str
    tools: list
    system_prompt: str
    result: ArmResult
    provider_name: str | None = None


# "jev" and "llm" are the same architecture with a different decision provider,
# so comparing them separates the provider from the change in how the agent works.
ARM_PROVIDERS = {"jev": "jev", "llm": "llm", "fake": "fake", "provider_tool": None}


def build_arm(
    arm_id: str,
    *,
    provider: DecisionProvider | None = None,
    settings: DecisionSettings | None = None,
) -> Arm:
    if arm_id == "baseline":
        return Arm("baseline", baseline_tools(), SYSTEM_PROMPT, ArmResult())
    if arm_id not in ARM_PROVIDERS:
        raise ValueError(f"unknown arm {arm_id!r}; expected baseline or one of {sorted(ARM_PROVIDERS)}")
    if provider is None:
        named = ARM_PROVIDERS[arm_id]
        base = settings or decision_settings()
        settings = replace(base, provider=named) if named else base
    resolved = provider or build_provider(settings)
    result = ArmResult()
    return Arm(
        arm_id,
        provider_tools(resolved, result),
        SYSTEM_PROMPT + TRIAGE_TOOL_OVERRIDE,
        result,
        provider_name=resolved.name,
    )


def build_arm_agent(arm: Arm, llm, **kwargs) -> Callable:
    return build_agent(llm=llm, tools=arm.tools, system_prompt=arm.system_prompt, **kwargs)
