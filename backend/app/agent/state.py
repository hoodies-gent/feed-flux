from typing import Annotated, NotRequired, TypedDict

from langgraph.graph.message import add_messages


class AgentState(TypedDict):
    messages: Annotated[list, add_messages]
    tool_calls_used: NotRequired[int]
    total_tokens_used: NotRequired[int]
    tool_error: NotRequired[dict[str, str] | None]
