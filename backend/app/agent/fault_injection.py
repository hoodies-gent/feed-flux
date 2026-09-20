from enum import StrEnum
from typing import Sequence

from langchain_core.messages import AIMessage
from pydantic import BaseModel


class ProviderFault(StrEnum):
    TIMEOUT = "timeout"
    RATE_LIMIT = "rate_limit"
    SERVER_ERROR = "server_error"
    VALIDATION = "validation"


class InjectedProviderError(RuntimeError):
    def __init__(self, status_code: int):
        super().__init__(f"injected provider HTTP {status_code}")
        self.status_code = status_code


class FaultInjectingTool:
    def __init__(self, error: Exception):
        self.error = error
        self.attempt_count = 0

    def invoke(self, tool_input):
        self.attempt_count += 1
        raise self.error


class _InvalidFaultPayload(BaseModel):
    fault_value: int


class FaultInjectingChatModel:
    def __init__(
        self,
        faults: Sequence[ProviderFault],
        *,
        response: str = "completed",
    ):
        self._faults = list(faults)
        self._response = response
        self.attempt_count = 0

    def bind_tools(self, tools, **kwargs):
        return self

    async def ainvoke(self, messages, **kwargs):
        self.attempt_count += 1
        if self._faults:
            self._raise_fault(self._faults.pop(0))
        return AIMessage(content=self._response)

    @staticmethod
    def _raise_fault(fault: ProviderFault) -> None:
        if fault is ProviderFault.TIMEOUT:
            raise TimeoutError("injected provider timeout")
        if fault is ProviderFault.RATE_LIMIT:
            raise InjectedProviderError(429)
        if fault is ProviderFault.SERVER_ERROR:
            raise InjectedProviderError(503)
        _InvalidFaultPayload.model_validate({"fault_value": "not-an-integer"})
