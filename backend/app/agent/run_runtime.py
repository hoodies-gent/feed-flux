import asyncio
from dataclasses import asdict

from app.agent.runtime_errors import classify_runtime_error
from app.agent.stream import stream_agent
from app.agent.usage import TokenPricing, estimate_cost
from app.services.agent_run_store import AgentRunStore, ErrorCategory, RunStatus

DEFAULT_RUN_TIMEOUT_SECONDS = 120.0


class RunTimeoutError(TimeoutError):
    pass


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
        self.store.transition_run(run_id, RunStatus.RUNNING)
        interrupted = False
        finished = False
        done_event = None
        try:
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

                if event.get("type") == "trace" and event.get("step") == "tool_start":
                    self.store.append_event(
                        run_id,
                        event_type="tool_call",
                        tool_name=event.get("tool"),
                        tool_call_id=event.get("tool_call_id"),
                    )

                if event.get("type") == "interrupt":
                    self.store.transition_run(run_id, RunStatus.INTERRUPTED)
                    interrupted = True

                if event.get("type") == "done":
                    done_event = event
                    continue

                yield event
                if event.get("type") == "interrupt":
                    yield self._run_event(run_id, RunStatus.INTERRUPTED)

            if not interrupted:
                self.store.transition_run(run_id, RunStatus.COMPLETED)
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
                error_category=error_category,
            )
            yield self._run_event(
                run_id,
                RunStatus.FAILED,
                error_category=error_category,
            )
            raise

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
