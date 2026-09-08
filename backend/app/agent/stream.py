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


async def _emit_interrupts(agent, config) -> AsyncIterator[dict]:
    state = agent.get_state(config)
    for task in state.tasks:
        for iv in task.interrupts:
            payload = iv.value if isinstance(iv.value, dict) else {"value": iv.value}
            yield {"type": "interrupt", **payload}


async def stream_agent(
    graph_input: Any, thread_id: str
) -> AsyncIterator[dict]:
    """Yield NDJSON-friendly events for a single agent invocation.

    graph_input is either {"messages": [...]} for a new turn or a Command(resume=...) for post-interrupt.
    """
    agent = get_agent()
    config = {"configurable": {"thread_id": thread_id}}

    async for ev in agent.astream_events(graph_input, config, version="v2"):
        kind = ev["event"]
        name = ev.get("name", "")
        data = ev.get("data", {})

        if kind == "on_chain_start" and name in ("agent", "tools"):
            yield {"type": "trace", "step": f"{name}_start"}

        elif kind == "on_chat_model_stream":
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
            yield {"type": "trace", "step": "tool_end", "tool": name, "output": output_text[:500]}

    async for ev in _emit_interrupts(agent, config):
        yield ev

    yield {"type": "done"}


def new_turn_input(message: str) -> dict:
    return {"messages": [HumanMessage(content=message)]}


def resume_input(approve: bool, note: str | None = None) -> Command:
    return Command(resume={"approve": approve, "note": note})
