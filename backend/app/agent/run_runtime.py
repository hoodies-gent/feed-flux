import asyncio

from app.agent.stream import stream_agent
from app.services.agent_run_store import AgentRunStore, RunStatus


class AgentRunRuntime:
    def __init__(self, store: AgentRunStore, provider: str, stream=None):
        self.store = store
        self.provider = provider
        self.stream = stream or stream_agent

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
            yield self._run_event(run_id, RunStatus.RUNNING)
            async for event in self.stream(graph_input, thread_id):
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
        except Exception:
            self.store.transition_run(run_id, RunStatus.FAILED)
            yield self._run_event(run_id, RunStatus.FAILED)
            raise

    @staticmethod
    def _run_event(run_id: str, status: RunStatus) -> dict:
        return {"type": "run", "run_id": run_id, "status": status.value}
