import json
import re
from typing import Any, AsyncIterator

from langchain_core.messages import HumanMessage
from langgraph.types import Command

from app.agent.graph import build_agent
from app.agent.memory_trace import (
    is_memory_tool,
    memory_trace_outcome,
    sanitize_memory_args,
    sanitize_memory_result,
)
from app.agent.usage import usage_event_from_message
from app.services.database import DatabaseService

_agent = None
_checkpointer = None
DEFAULT_MAX_GRAPH_STEPS = 25
REFERENCE_FOOTER_PREFIX = "<!--feedflux_refs:"
REFERENCE_FOOTER_SUFFIX = "-->"


class _ReferenceFooterTokenFilter:
    def __init__(self) -> None:
        self.buffer = ""
        self.in_footer = False

    def feed(self, text: str) -> str:
        self.buffer += text
        output = []
        while self.buffer:
            if self.in_footer:
                end = self.buffer.find(REFERENCE_FOOTER_SUFFIX)
                if end < 0:
                    break
                self.buffer = self.buffer[end + len(REFERENCE_FOOTER_SUFFIX):]
                self.in_footer = False
                continue

            start = self.buffer.find(REFERENCE_FOOTER_PREFIX)
            if start >= 0:
                output.append(self.buffer[:start])
                self.buffer = self.buffer[start + len(REFERENCE_FOOTER_PREFIX):]
                self.in_footer = True
                continue

            retained = 0
            max_prefix = min(len(self.buffer), len(REFERENCE_FOOTER_PREFIX) - 1)
            for length in range(max_prefix, 0, -1):
                if self.buffer.endswith(REFERENCE_FOOTER_PREFIX[:length]):
                    retained = length
                    break
            if retained:
                output.append(self.buffer[:-retained])
                self.buffer = self.buffer[-retained:]
            else:
                output.append(self.buffer)
                self.buffer = ""
            break
        return "".join(output)

    def finish(self) -> str:
        if self.in_footer:
            trailing = ""
        else:
            trailing = self.buffer
        self.buffer = ""
        self.in_footer = False
        return trailing


def set_agent_checkpointer(checkpointer) -> None:
    global _agent, _checkpointer
    _agent = None
    _checkpointer = checkpointer


def get_agent():
    global _agent
    if _agent is None:
        if _checkpointer is None:
            raise RuntimeError("Agent runtime is not initialized.")
        _agent = build_agent(checkpointer=_checkpointer)
    return _agent


def _enrich_interrupt(payload: dict, recent_tool_results: list[dict]) -> dict:
    """Extract fields the frontend needs for the review card."""
    event = {"type": "interrupt", **payload}
    tool = payload.get("tool")
    if tool == "save_reply_draft":
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
    if hasattr(agent, "aget_state"):
        state = await agent.aget_state(config)
    else:
        state = agent.get_state(config)
    for task in state.tasks:
        for iv in task.interrupts:
            payload = iv.value if isinstance(iv.value, dict) else {"value": iv.value}
            yield _enrich_interrupt(payload, recent_tool_results)


async def stream_agent(
    graph_input: Any,
    thread_id: str,
    *,
    callbacks: list[Any] | None = None,
    tool_output_limit: int | None = 2000,
    agent: Any | None = None,
    max_graph_steps: int = DEFAULT_MAX_GRAPH_STEPS,
) -> AsyncIterator[dict]:
    """Yield NDJSON-friendly events for a single agent invocation.

    graph_input is either {"messages": [...]} for a new turn or a Command(resume=...) for post-interrupt.
    """
    runtime_agent = agent if agent is not None else get_agent()
    config = {
        "configurable": {"thread_id": thread_id},
        "recursion_limit": max_graph_steps,
    }
    if callbacks:
        config["callbacks"] = callbacks
    recent_tool_results: list[dict] = []
    reference_footer_filter = _ReferenceFooterTokenFilter()

    async for ev in runtime_agent.astream_events(graph_input, config, version="v2"):
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
                visible_text = reference_footer_filter.feed(text)
                if visible_text:
                    yield {"type": "token", "content": visible_text}

        elif kind == "on_chat_model_end":
            trailing_text = reference_footer_filter.finish()
            if trailing_text:
                yield {"type": "token", "content": trailing_text}
            usage_event = usage_event_from_message(data.get("output"))
            if usage_event is not None:
                yield usage_event

        elif kind == "on_custom_event" and name == "email_context_loaded":
            context_email_ids = list(data.get("context_email_ids") or [])
            yield {
                "type": "trace",
                "step": "context_loaded",
                "context_email_ids": context_email_ids,
                "context_email_count": len(context_email_ids),
                "context_chars": data.get("context_chars", 0),
                "context_char_limit": data.get("context_char_limit"),
            }

        elif kind == "on_custom_event" and name == "memory_context_loaded":
            memory_ids = list(data.get("memory_ids") or [])
            yield {
                "type": "trace",
                "step": "memory_context_loaded",
                "memory_ids": memory_ids,
                "memory_count": len(memory_ids),
                "workflow_scope": data.get("workflow_scope"),
                "has_contact_scope": bool(data.get("has_contact_scope")),
                "context_chars": data.get("context_chars", 0),
                "estimated_tokens": data.get("estimated_tokens", 0),
                "memory_limit": data.get("memory_limit"),
                "context_char_limit": data.get("context_char_limit"),
                "value_char_limit": data.get("value_char_limit"),
            }

        elif kind == "on_custom_event" and name == "email_context_references":
            references = [
                {
                    "email_id": reference.get("email_id"),
                    "subject": reference.get("subject") or "",
                    "sender": reference.get("sender") or "",
                }
                for reference in data.get("references") or []
                if reference.get("email_id")
            ]
            if references:
                yield {"type": "references", "references": references}

        elif kind == "on_tool_start":
            tool_call_id = (ev.get("metadata") or {}).get("tool_call_id")
            tool_input = data.get("input")
            event = {
                "type": "trace",
                "step": "tool_start",
                "tool": name,
                "args": (
                    sanitize_memory_args(name, tool_input)
                    if is_memory_tool(name)
                    else tool_input
                ),
            }
            if tool_call_id is not None:
                event["tool_call_id"] = tool_call_id
            yield event

        elif kind == "on_chain_end" and name == "tools":
            tool_error = (data.get("output") or {}).get("tool_error")
            if not tool_error:
                continue
            event = {
                "type": "trace",
                "step": "tool_error",
                "tool": tool_error["tool"],
                "output": tool_error["message"],
                "error_category": tool_error["error_category"],
                "tool_call_id": tool_error["tool_call_id"],
            }
            yield event

        elif kind == "on_tool_end":
            output = data.get("output")
            output_text = output.content if hasattr(output, "content") else str(output)
            tool_call_id = (ev.get("metadata") or {}).get("tool_call_id")
            if tool_call_id is None:
                tool_call_id = getattr(output, "tool_call_id", None)
            if is_memory_tool(name):
                safe_output = sanitize_memory_result(name, output)
                output_text = json.dumps(safe_output, sort_keys=True)
            truncated = (
                output_text
                if tool_output_limit is None
                else output_text[:tool_output_limit]
            )
            recent_tool_results.append({"tool": name, "output": truncated})
            event = {"type": "trace", "step": "tool_end", "tool": name, "output": truncated}
            if tool_call_id is not None:
                event["tool_call_id"] = tool_call_id
            count = (
                safe_output.get("result_count")
                if is_memory_tool(name)
                else _count_list_result(output, output_text)
            )
            if count is not None:
                event["result_count"] = count

            if is_memory_tool(name):
                event["outcome"] = memory_trace_outcome(name, output)

            draft_event = None
            if name == "apply_triage_batch":
                tool_input = data.get("input") or {}
            elif name in {"save_reply_draft", "apply_draft_patch"}:
                draft_event = _build_draft_event(data.get("input") or {}, output_text)
                if draft_event is not None:
                    event["outcome"] = {
                        "schema_version": 1,
                        "kind": "draft",
                        "draft_id": draft_event["draft_id"],
                        "email_id": draft_event["email_id"],
                        "result": (
                            "created"
                            if output_text.startswith("DRAFT READY")
                            else "updated"
                        ),
                    }
            yield event

            if name == "apply_triage_batch":
                yield _build_plan_event(tool_input)
            elif draft_event is not None:
                yield draft_event

    async for ev in _emit_interrupts(runtime_agent, config, recent_tool_results):
        yield ev

    yield {"type": "done"}


def new_turn_input(
    message: str,
    context_email_ids: list[str] | None = None,
) -> dict:
    return {
        "messages": [HumanMessage(content=message)],
        "context_email_ids": list(context_email_ids or []),
        "tool_calls_used": 0,
        "total_tokens_used": 0,
        "tool_error": None,
    }


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
