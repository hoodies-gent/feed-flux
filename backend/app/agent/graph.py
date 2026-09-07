from langchain_core.messages import SystemMessage
from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.checkpoint.memory import MemorySaver
from langgraph.graph import END, START, StateGraph

from app.agent.llm import get_llm
from app.agent.state import AgentState

SYSTEM_PROMPT = (
    "You are FeedFlux, a helpful email assistant. "
    "Answer concisely and remember prior turns in the conversation."
)


def build_agent(checkpointer: BaseCheckpointSaver | None = None):
    llm = get_llm()

    def agent_node(state: AgentState) -> dict:
        messages = state["messages"]
        if not messages or not isinstance(messages[0], SystemMessage):
            messages = [SystemMessage(content=SYSTEM_PROMPT), *messages]
        response = llm.invoke(messages)
        return {"messages": [response]}

    graph = StateGraph(AgentState)
    graph.add_node("agent", agent_node)
    graph.add_edge(START, "agent")
    graph.add_edge("agent", END)
    return graph.compile(checkpointer=checkpointer or MemorySaver())
