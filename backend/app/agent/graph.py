from langchain_core.messages import SystemMessage, ToolMessage
from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.checkpoint.memory import MemorySaver
from langgraph.graph import END, START, StateGraph
from langgraph.types import interrupt

from app.agent.llm import get_llm
from app.agent.state import AgentState
from app.agent.tools import HIGH_RISK_TOOLS, TOOLS, TOOLS_BY_NAME

SYSTEM_PROMPT = (
    "You are FeedFlux, a helpful email assistant. "
    "Answer concisely and remember prior turns in the conversation. "
    "You have tools available; call them when the user's intent matches."
)


def build_agent(checkpointer: BaseCheckpointSaver | None = None):
    llm = get_llm().bind_tools(TOOLS)

    def agent_node(state: AgentState) -> dict:
        messages = state["messages"]
        if not messages or not isinstance(messages[0], SystemMessage):
            messages = [SystemMessage(content=SYSTEM_PROMPT), *messages]
        return {"messages": [llm.invoke(messages)]}

    def tools_node(state: AgentState) -> dict:
        last = state["messages"][-1]
        results = []
        for tc in last.tool_calls:
            name, args, call_id = tc["name"], tc["args"], tc["id"]

            if name in HIGH_RISK_TOOLS:
                decision = interrupt({"tool": name, "args": args, "tool_call_id": call_id})
                if not decision.get("approve"):
                    note = decision.get("note") or "User rejected the action."
                    results.append(ToolMessage(f"[rejected by user] {note}", tool_call_id=call_id))
                    continue

            output = TOOLS_BY_NAME[name].invoke(args)
            results.append(ToolMessage(str(output), tool_call_id=call_id))
        return {"messages": results}

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
