import contextvars


current_thread_id: contextvars.ContextVar[str] = contextvars.ContextVar(
    "current_thread_id", default="unknown"
)
current_run_id: contextvars.ContextVar[str | None] = contextvars.ContextVar(
    "current_run_id", default=None
)
current_tool_call_id: contextvars.ContextVar[str | None] = contextvars.ContextVar(
    "current_tool_call_id", default=None
)
