import asyncio
from dataclasses import asdict

from app.agent.execution_context import current_run_id
from app.agent.runtime_errors import classify_runtime_error
from app.agent.stream import stream_agent
from app.agent.usage import TokenPricing, estimate_cost
from app.services.agent_run_store import AgentRunStore, ErrorCategory, RunStatus

DEFAULT_RUN_TIMEOUT_SECONDS = 120.0


class RunTimeoutError(TimeoutError):
    pass


def _business_artifact(event: dict) -> dict | None:
    if event.get("type") == "draft":
        return {
            "type": "draft",
            "draft_id": event.get("draft_id"),
            "email_id": event.get("email_id"),
        }
    if event.get("type") == "plan":
        return {
            "type": "triage_plan",
            "bulk_email_ids": [
                item["email_id"]
                for item in event.get("bulk", [])
                if item.get("email_id")
            ],
            "needs_reply_email_ids": [
                item["email_id"]
                for item in event.get("needs_reply", [])
                if item.get("email_id")
            ],
        }
    return None


class AgentRunRuntime:
    def __init__(
        self,
        store: AgentRunStore,
        provider: str,
        stream=None,
        run_timeout_seconds: float = DEFAULT_RUN_TIMEOUT_SECONDS,
        pricing: TokenPricing | None = None,
    ):
        self.store = store
        self.provider = provider
        self.stream = stream or stream_agent
        self.run_timeout_seconds = run_timeout_seconds
        self.pricing = pricing

    def stream_new_run(self, graph_input, thread_id: str):
        run = self.store.create_run(thread_id=thread_id, provider=self.provider)
        return self._stream_run(graph_input, thread_id, run["run_id"])

    def stream_resumed_run(self, graph_input, thread_id: str):
        run = self.store.get_interrupted_run(thread_id)
        return self._stream_run(graph_input, thread_id, run["run_id"])

    async def _stream_run(self, graph_input, thread_id: str, run_id: str):
        run_token = current_run_id.set(run_id)
        interrupted = False
        finished = False
        done_event = None
        artifacts = []
        try:
            self.store.transition_run(run_id, RunStatus.RUNNING)
            loop = asyncio.get_running_loop()
            deadline = loop.time() + self.run_timeout_seconds
            event_stream = self.stream(graph_input, thread_id).__aiter__()
            yield self._run_event(run_id, RunStatus.RUNNING)
            while True:
                remaining = deadline - loop.time()
                if remaining <= 0:
                    raise self._timeout_error()

                next_event = asyncio.ensure_future(anext(event_stream))
                try:
                    event = await asyncio.wait_for(next_event, timeout=remaining)
                except StopAsyncIteration:
                    break
                except TimeoutError as error:
                    if next_event.cancelled():
                        raise self._timeout_error() from error
                    raise

                if event.get("type") == "usage":
                    usage = event["usage"]
                    self.store.append_event(
                        run_id,
                        event_type="provider_usage",
                        provider=self.provider,
                        outcome={
                            "schema_version": 1,
                            "model": event.get("model"),
                            "usage": usage,
                            "pricing": asdict(self.pricing) if self.pricing else None,
                            "estimated_cost_usd": estimate_cost(usage, self.pricing),
                        },
                    )
                    continue

                if event.get("type") == "trace":
                    if event.get("step") == "tool_start":
                        self.store.append_event(
                            run_id,
                            event_type="tool_call",
                            provider=self.provider,
                            tool_name=event.get("tool"),
                            tool_call_id=event.get("tool_call_id"),
                        )
                    elif event.get("step") == "tool_end":
                        self.store.append_event(
                            run_id,
                            event_type="tool_result",
                            provider=self.provider,
                            tool_name=event.get("tool"),
                            tool_call_id=event.get("tool_call_id"),
                            outcome=event.get("outcome"),
                        )
                    elif event.get("step") == "tool_error":
                        self.store.append_event(
                            run_id,
                            event_type="tool_result",
                            provider=self.provider,
                            tool_name=event.get("tool"),
                            tool_call_id=event.get("tool_call_id"),
                            outcome={
                                "schema_version": 1,
                                "kind": "error",
                                "result": "failed",
                            },
                            error_category=event.get("error_category"),
                        )
                    elif event.get("step") == "context_loaded":
                        self.store.append_event(
                            run_id,
                            event_type="context_loaded",
                            provider=self.provider,
                            outcome={
                                "schema_version": 1,
                                "context_email_ids": event.get("context_email_ids", []),
                                "context_email_count": event.get(
                                    "context_email_count", 0
                                ),
                                "context_chars": event.get("context_chars", 0),
                                "context_char_limit": event.get(
                                    "context_char_limit"
                                ),
                            },
                        )
                    elif event.get("step") == "memory_context_loaded":
                        self.store.append_event(
                            run_id,
                            event_type="memory_context_loaded",
                            provider=self.provider,
                            outcome={
                                "schema_version": 1,
                                "memory_ids": event.get("memory_ids", []),
                                "memory_count": event.get("memory_count", 0),
                                "workflow_scope": event.get("workflow_scope"),
                                "has_contact_scope": event.get(
                                    "has_contact_scope", False
                                ),
                                "context_chars": event.get("context_chars", 0),
                                "estimated_tokens": event.get(
                                    "estimated_tokens", 0
                                ),
                                "memory_limit": event.get("memory_limit"),
                                "context_char_limit": event.get(
                                    "context_char_limit"
                                ),
                                "value_char_limit": event.get("value_char_limit"),
                            },
                        )

                if event.get("type") == "interrupt":
                    self.store.transition_run(
                        run_id,
                        RunStatus.INTERRUPTED,
                        outcome={
                            "schema_version": 1,
                            "kind": "awaiting_approval",
                            "tool": event.get("tool"),
                            "tool_call_id": event.get("tool_call_id"),
                        },
                    )
                    interrupted = True

                if event.get("type") == "done":
                    done_event = event
                    continue

                artifact = _business_artifact(event)
                if artifact is not None:
                    artifacts.append(artifact)

                yield event
                if event.get("type") == "interrupt":
                    yield self._run_event(run_id, RunStatus.INTERRUPTED)

            if not interrupted:
                self.store.transition_run(
                    run_id,
                    RunStatus.COMPLETED,
                    outcome={
                        "schema_version": 1,
                        "kind": "completed",
                        "artifacts": artifacts,
                    },
                )
                finished = True
                yield self._run_event(run_id, RunStatus.COMPLETED)
            if done_event is not None:
                yield done_event
        except (asyncio.CancelledError, GeneratorExit):
            if not interrupted and not finished:
                self.store.transition_run(run_id, RunStatus.CANCELLED)
            raise
        except Exception as error:
            error_category = classify_runtime_error(error)
            self.store.transition_run(
                run_id,
                RunStatus.FAILED,
                outcome={
                    "schema_version": 1,
                    "kind": "failed",
                    "result": "stopped",
                    "error_category": error_category.value,
                },
                error_category=error_category,
            )
            yield self._run_event(
                run_id,
                RunStatus.FAILED,
                error_category=error_category,
            )
            raise
        finally:
            current_run_id.reset(run_token)

    def _timeout_error(self) -> RunTimeoutError:
        return RunTimeoutError(
            f"Agent run exceeded {self.run_timeout_seconds:g} seconds."
        )

    @staticmethod
    def _run_event(
        run_id: str,
        status: RunStatus,
        *,
        error_category: ErrorCategory | None = None,
    ) -> dict:
        event = {"type": "run", "run_id": run_id, "status": status.value}
        if error_category is not None:
            event["error_category"] = error_category.value
        return event
