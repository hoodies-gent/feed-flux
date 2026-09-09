from typing import Any, AsyncIterator

from langchain_core.messages import HumanMessage
from langgraph.types import Command

from app.agent.graph import build_agent

_agent = None


def get_agent():
    global _agent
    if _agent is None:
        _agent = build_agent()
    return _agent


def _enrich_interrupt(payload: dict, recent_tool_results: list[dict]) -> dict:
    """Extract fields the frontend needs for the review card."""
    event = {"type": "interrupt", **payload}
    if payload.get("tool") == "send_reply":
        args = payload.get("args") or {}
        event["draft_preview"] = {
            "recipient": args.get("recipient"),
            "subject": args.get("subject"),
            "body": args.get("body"),
            "original_email_id": args.get("original_email_id"),
        }
        event["references"] = recent_tool_results
    return event


async def _emit_interrupts(agent, config, recent_tool_results) -> AsyncIterator[dict]:
    state = agent.get_state(config)
    for task in state.tasks:
        for iv in task.interrupts:
            payload = iv.value if isinstance(iv.value, dict) else {"value": iv.value}
            yield _enrich_interrupt(payload, recent_tool_results)


async def stream_agent(
    graph_input: Any, thread_id: str
) -> AsyncIterator[dict]:
    """Yield NDJSON-friendly events for a single agent invocation.

    graph_input is either {"messages": [...]} for a new turn or a Command(resume=...) for post-interrupt.
    """
    agent = get_agent()
    config = {"configurable": {"thread_id": thread_id}}
    recent_tool_results: list[dict] = []

    async for ev in agent.astream_events(graph_input, config, version="v2"):
        kind = ev["event"]
        name = ev.get("name", "")
        data = ev.get("data", {})

        if kind == "on_chat_model_stream":
            chunk = data.get("chunk")
            text = ""
            if chunk is not None:
                content = chunk.content
                if isinstance(content, str):
                    text = content
                elif isinstance(content, list):
                    text = "".join(
                        part.get("text", "") if isinstance(part, dict) else str(part)
                        for part in content
                    )
            if text:
                yield {"type": "token", "content": text}

        elif kind == "on_tool_start":
            yield {"type": "trace", "step": "tool_start", "tool": name, "args": data.get("input")}

        elif kind == "on_tool_end":
            output = data.get("output")
            output_text = output.content if hasattr(output, "content") else str(output)
            recent_tool_results.append({"tool": name, "output": output_text[:500]})
            yield {"type": "trace", "step": "tool_end", "tool": name, "output": output_text[:500]}

    async for ev in _emit_interrupts(agent, config, recent_tool_results):
        yield ev

    yield {"type": "done"}


def new_turn_input(message: str) -> dict:
    return {"messages": [HumanMessage(content=message)]}


def resume_input(
    approve: bool,
    note: str | None = None,
    edited_body: str | None = None,
    decisions: list[dict] | None = None,
) -> Command:
    payload: dict = {"approve": approve, "note": note}
    if edited_body is not None:
        payload["edited_body"] = edited_body
    if decisions is not None:
        payload["decisions"] = decisions
    return Command(resume=payload)
