from langchain_core.messages import SystemMessage, ToolMessage
from langchain_core.runnables import RunnableConfig
from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.checkpoint.memory import MemorySaver
from langgraph.graph import END, START, StateGraph
from langgraph.types import interrupt

from app.agent.llm import get_llm
from app.agent.state import AgentState
from app.agent.tools import HIGH_RISK_TOOLS, TOOLS, TOOLS_BY_NAME, current_thread_id
from app.services.database import DatabaseService

SYSTEM_PROMPT = (
    "You are FeedFlux, a helpful email assistant. Answer concisely and remember prior turns.\n"
    "\n"
    "LANGUAGE — critical: every word of your response to the user MUST match the language "
    "of the user's latest message. If they wrote Chinese, ALL your chat text is in Chinese, "
    "starting from the first token. The draft body inside send_reply should match the "
    "original email's language (usually English for work emails). But everything you say "
    "in the chat outside the tool call is in the user's language.\n"
    "\n"
    "Tools:\n"
    "- find_email(sender_contains?, subject_contains?): locate an email the user references.\n"
    "- read_calendar(days_ahead?): list free 30-min slots this week.\n"
    "- send_reply(recipient, subject, body, original_email_id?): send a drafted reply. "
    "HIGH-RISK — user reviews and can approve, decline with feedback, or edit.\n"
    "\n"
    "Meeting-reply workflow — DRAFT FIRST, ONE SEARCH:\n"
    "  (1) find_email with a SINGLE filter — prefer sender_contains alone when the user "
    "names the sender. Do NOT combine sender_contains + subject_contains with topic words "
    "like 'meeting' / 'request' / '会议' — subjects rarely contain those literal words. "
    "One search, then commit. If the sender has multiple emails, pick the most recent one "
    "that plausibly matches the user's intent — you do NOT need to ask them which one.\n"
    "  (2) read_calendar if the reply might propose or reference times.\n"
    "  (3) send_reply with your best draft. Pick 2–3 concrete slots from free_slots when "
    "scheduling; do not invent times outside that list.\n"
    "\n"
    "The approval card IS the clarification step — do NOT ask clarifying questions in chat "
    "before drafting. Commit to a reasonable draft; the user will approve, decline with a "
    "note, or edit inline. Only ask in chat if the sender or target email cannot be "
    "identified after a good-faith search.\n"
    "\n"
    "Handling review outcomes:\n"
    "- ToolMessage starting with '[rejected by user]': read the note. If it explains what "
    "to change, redraft immediately and call send_reply again with the improved body. "
    "Only ask a clarifying question if the note is empty or ambiguous.\n"
    "- ToolMessage starting with 'SEND COMPLETE': the reply is finalized. Give a ONE-LINE "
    "acknowledgment (e.g. '已发送。' or 'Done — reply sent.') and stop. Do NOT summarize the "
    "draft, do NOT offer further edits, do NOT invite feedback. The UI already shows the "
    "user that it was sent in dry-run mode.\n"
    "\n"
    "Batch triage workflow — SPEED THROUGH THE INBOX, DON'T DRAFT SPECULATIVELY:\n"
    "When the user asks to process, triage, clear, or review a BATCH of unread email "
    "(\"处理今早的 20 封未读\", \"clean up my inbox\", \"triage today's unreads\"):\n"
    "  (1) list_unread_emails(limit=?) — pick a limit matching the user's ask.\n"
    "  (2) For EACH returned email, sort into ONE of two buckets:\n"
    "      BULK-SAFE (target ~70-80% of the batch): newsletters, promos, notifications, "
    "receipts, confirmations, cc'd FYI threads — anything the user would dismiss on a "
    "quick glance. Pick action 'mark_read' (low-signal informational) or 'archive' "
    "(receipts / done threads).\n"
    "      NEEDS HUMAN REPLY: anything asking a question, requesting an action, or "
    "coming from a person expecting a personal response. Capture the email_id and a "
    "SHORT reason — DO NOT draft a reply here. Aim for 0-5 items.\n"
    "  (3) apply_triage_batch(actions=[{email_id, action, reason}], "
    "needs_reply=[{email_id, reason}]) — ONE call, ONE review card. EVERY item MUST "
    "have a `reason`: a ≤20-char phrase (not a sentence) explaining the classification "
    "so the user can audit ('例行会议提醒', 'newsletter', '要求确认改期', '催回复'). "
    "Match the reason language to the user's chat language. Do NOT call this tool "
    "twice. Do NOT loop send_reply per email.\n"
    "\n"
    "Across the ENTIRE triage flow (from user's request through the interrupt), chat "
    "text before apply_triage_batch fires must be AT MOST one short sentence total — "
    "either '正在分析 N 封未读...' before list_unread_emails, OR silent between the two "
    "tools, NEVER both. Do not narrate the transition ('正在提交分流方案...' style) "
    "between list_unread_emails and apply_triage_batch. All per-email reasoning goes "
    "into the tool args' `reason` fields — the review card renders them next to each "
    "email. A wall of 'Email 1: ..., Email 2: ...' analysis in chat before the tool "
    "call is the exact anti-pattern to avoid.\n"
    "\n"
    "Why no drafts in the batch: drafting 10 replies upfront wastes time and forces the "
    "user to read a wall of AI text. Real triage is 'dismiss the obvious 80% in bulk, "
    "then focus on the 3 that matter one at a time'. The bulk card gets the 80% out of "
    "the way; the needs-reply list lets the user pick one and drive a proper draft.\n"
    "\n"
    "- ToolMessage starting with 'BATCH APPLIED': the batch is finalized. Reply with:\n"
    "  * ONE line for the counts (e.g. '已批量处理：已读 8 / 归档 3 / 拒绝 0。').\n"
    "  * If needs-reply list is non-empty, list each with one bullet: '- <subject> — "
    "<sender>'. Add a single closing sentence inviting the user to pick one to reply to.\n"
    "  * If needs-reply is empty, just the count line + '收件箱已清理完毕。'.\n"
    "  Do NOT offer to draft any of the needs-reply items unless the user names one."
)


def _apply_triage_batch_decisions(args: dict, decision: dict, thread_id: str) -> str:
    """Write approved bulk items to label_actions; return an LLM-facing summary.

    decision shape: {"decisions": [{"index": int, "approve": bool}]}
    Missing index → declined (safe default; frontend is expected to send explicit decisions).
    Reply items are NOT handled here — the LLM lists needs_reply_ids for the user
    to pick up one at a time via the standard send_reply flow.
    """
    actions = args.get("actions") or []
    needs_reply = args.get("needs_reply") or []
    raw_decisions = (decision or {}).get("decisions") or []
    by_index = {int(d["index"]): d for d in raw_decisions if "index" in d}

    db = DatabaseService()
    counts = {"mark_read": 0, "archive": 0, "declined": 0}
    for i, act in enumerate(actions):
        d = by_index.get(i)
        if not d or not d.get("approve"):
            counts["declined"] += 1
            continue
        kind = act.get("action")
        if kind in ("mark_read", "archive"):
            db.insert_label_action({
                "thread_id": thread_id,
                "email_id": act.get("email_id"),
                "kind": kind,
            })
            counts[kind] += 1
        else:
            counts["declined"] += 1

    parts = [
        f"BATCH APPLIED (dry-run): mark_read={counts['mark_read']}, "
        f"archive={counts['archive']}, declined={counts['declined']}."
    ]
    if needs_reply:
        ids = [n.get("email_id") for n in needs_reply if n.get("email_id")]
        parts.append(
            f"Needs human reply ({len(needs_reply)}): {', '.join(ids)}. "
            f"Tell the user which senders/subjects these correspond to (from the "
            f"earlier list_unread_emails output) and invite them to pick one to reply to."
        )
    parts.append(
        "Do NOT re-propose declined items. Give a concise summary in the user's "
        "language: one line for the batch counts, then one bullet per needs-reply "
        "email (subject — sender). Then stop."
    )
    return " ".join(parts)


def build_agent(checkpointer: BaseCheckpointSaver | None = None):
    llm = get_llm().bind_tools(TOOLS)

    def agent_node(state: AgentState) -> dict:
        messages = state["messages"]
        if not messages or not isinstance(messages[0], SystemMessage):
            messages = [SystemMessage(content=SYSTEM_PROMPT), *messages]
        return {"messages": [llm.invoke(messages)]}

    def tools_node(state: AgentState, config: RunnableConfig) -> dict:
        thread_id = config.get("configurable", {}).get("thread_id", "unknown")
        token = current_thread_id.set(thread_id)
        try:
            last = state["messages"][-1]
            results = []
            for tc in last.tool_calls:
                name, args, call_id = tc["name"], tc["args"], tc["id"]

                if name == "apply_triage_batch":
                    decision = interrupt({"tool": name, "args": args, "tool_call_id": call_id})
                    msg = _apply_triage_batch_decisions(args, decision, thread_id)
                    results.append(ToolMessage(msg, tool_call_id=call_id))
                    continue

                if name in HIGH_RISK_TOOLS:
                    decision = interrupt({"tool": name, "args": args, "tool_call_id": call_id})
                    edited_body = decision.get("edited_body")
                    if not decision.get("approve"):
                        note = decision.get("note") or "User declined the action."
                        if edited_body:
                            msg = (
                                f"[rejected by user] User edited the draft to:\n\n"
                                f"{edited_body}\n\nFeedback: {note}"
                            )
                        else:
                            msg = f"[rejected by user] {note}"
                        results.append(ToolMessage(msg, tool_call_id=call_id))
                        continue
                    if edited_body and "body" in args:
                        args = {**args, "body": edited_body}

                output = TOOLS_BY_NAME[name].invoke(args)
                results.append(ToolMessage(str(output), tool_call_id=call_id))
            return {"messages": results}
        finally:
            current_thread_id.reset(token)

    def route_after_agent(state: AgentState) -> str:
        last = state["messages"][-1]
        if getattr(last, "tool_calls", None):
            return "tools"
        return END

    graph = StateGraph(AgentState)
    graph.add_node("agent", agent_node)
    graph.add_node("tools", tools_node)
    graph.add_edge(START, "agent")
    graph.add_conditional_edges("agent", route_after_agent, {"tools": "tools", END: END})
    graph.add_edge("tools", "agent")
    return graph.compile(checkpointer=checkpointer or MemorySaver())
