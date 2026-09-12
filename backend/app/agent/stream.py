import re
from typing import Any, AsyncIterator

from langchain_core.messages import HumanMessage
from langgraph.types import Command

from app.agent.graph import build_agent
from app.services.database import DatabaseService

_agent = None


def get_agent():
    global _agent
    if _agent is None:
        _agent = build_agent()
    return _agent


def _enrich_interrupt(payload: dict, recent_tool_results: list[dict]) -> dict:
    """Extract fields the frontend needs for the review card."""
    event = {"type": "interrupt", **payload}
    tool = payload.get("tool")
    if tool == "send_reply":
        args = payload.get("args") or {}
        event["draft_preview"] = {
            "recipient": args.get("recipient"),
            "subject": args.get("subject"),
            "body": args.get("body"),
            "original_email_id": args.get("original_email_id"),
        }
        event["references"] = recent_tool_results
    return event


def _build_plan_event(args: dict) -> dict:
    """Assemble a `plan` stream event for apply_triage_batch: enrich each
    classified email with subject/sender/preview so the card renders directly.
    """
    actions = args.get("actions") or []
    needs_reply = args.get("needs_reply") or []
    all_ids = {a.get("email_id") for a in actions if a.get("email_id")}
    all_ids.update(n.get("email_id") for n in needs_reply if n.get("email_id"))
    meta = _load_email_meta(all_ids)
    return {
        "type": "plan",
        "bulk": [
            {
                "index": i,
                "email_id": a.get("email_id"),
                "action": a.get("action"),
                "reason": a.get("reason"),
                **meta.get(a.get("email_id"), {}),
            }
            for i, a in enumerate(actions)
        ],
        "needs_reply": [
            {
                "email_id": n.get("email_id"),
                "reason": n.get("reason"),
                **meta.get(n.get("email_id"), {}),
            }
            for n in needs_reply
        ],
    }


def _build_draft_event(args: dict, output_text: str) -> dict | None:
    match = re.match(r"DRAFT (?:READY|UPDATED) \(id=(\d+)\)\.", output_text)
    email_id = args.get("original_email_id")
    if not match or not email_id:
        return None
    return {
        "type": "draft",
        "draft_id": int(match.group(1)),
        "email_id": email_id,
    }


def _count_list_result(output: Any, output_text: str) -> int | None:
    """Return list length if the tool returned a list — accurate count for the
    frontend's tool-line summary. Handles both raw list returns and
    ToolMessage-wrapped stringified lists (LangChain path).
    """
    if isinstance(output, list):
        return len(output)
    stripped = output_text.strip()
    if stripped.startswith("[") and stripped.endswith("]"):
        try:
            import ast
            parsed = ast.literal_eval(stripped)
            if isinstance(parsed, list):
                return len(parsed)
        except Exception:
            pass
    return None


def _load_email_meta(email_ids: set[str]) -> dict[str, dict]:
    """Fetch subject/sender/body_preview for the given email ids in one query."""
    if not email_ids:
        return {}
    from app.models.email import Email

    db = DatabaseService()
    session = db.Session()
    try:
        rows = session.query(Email).filter(Email.id.in_(list(email_ids))).all()
        return {
            r.id: {
                "subject": r.subject,
                "sender": r.sender_name or r.sender_email,
                "sender_email": r.sender_email,
                "body_preview": r.body_preview,
            }
            for r in rows
        }
    finally:
        session.close()


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
            truncated = output_text[:2000]
            recent_tool_results.append({"tool": name, "output": truncated})
            event = {"type": "trace", "step": "tool_end", "tool": name, "output": truncated}
            count = _count_list_result(output, output_text)
            if count is not None:
                event["result_count"] = count
            yield event

            if name == "apply_triage_batch":
                tool_input = data.get("input") or {}
                yield _build_plan_event(tool_input)
            elif name in {"send_reply", "apply_draft_patch"}:
                draft_event = _build_draft_event(data.get("input") or {}, output_text)
                if draft_event:
                    yield draft_event

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
