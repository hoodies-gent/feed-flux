from langchain_core.messages import SystemMessage, ToolMessage
from langchain_core.runnables import RunnableConfig
from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.checkpoint.memory import MemorySaver
from langgraph.graph import END, START, StateGraph
from langgraph.types import interrupt

from app.agent.llm import get_llm
from app.agent.state import AgentState
from app.agent.tools import HIGH_RISK_TOOLS, TOOLS, TOOLS_BY_NAME, current_thread_id

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
    "user that it was sent in dry-run mode."
)


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
