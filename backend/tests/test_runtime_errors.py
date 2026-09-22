import unittest
from types import SimpleNamespace

from pydantic import BaseModel, ValidationError


class _HttpStatusError(Exception):
    def __init__(self, status_code: int):
        super().__init__(f"HTTP {status_code}")
        self.status_code = status_code


class _ResponseStatusError(Exception):
    def __init__(self, status_code: int):
        super().__init__(f"HTTP {status_code}")
        self.response = SimpleNamespace(status_code=status_code)


class _APITimeoutError(Exception):
    pass


class _ReadTimeout(Exception):
    pass


class _IntegerInput(BaseModel):
    value: int


def _validation_error() -> ValidationError:
    try:
        _IntegerInput(value="not-an-integer")
    except ValidationError as exc:
        return exc
    raise AssertionError("validation fixture did not fail")


class RuntimeErrorClassificationTest(unittest.TestCase):
    def test_classifies_only_known_recoverable_failures_as_non_terminal(self):
        try:
            from app.agent.runtime_errors import classify_runtime_error
        except ModuleNotFoundError:
            self.fail("runtime error classification is not implemented")

        cases = [
            (TimeoutError("timed out"), "transient"),
            (_APITimeoutError("provider timed out"), "transient"),
            (_ReadTimeout("transport timed out"), "transient"),
            (_HttpStatusError(429), "transient"),
            (_HttpStatusError(500), "transient"),
            (_HttpStatusError(502), "transient"),
            (_ResponseStatusError(503), "transient"),
            (_HttpStatusError(504), "transient"),
            (_validation_error(), "llm_tool_repairable"),
            (PermissionError("permission denied"), "user_repairable"),
            (_HttpStatusError(401), "user_repairable"),
            (_ResponseStatusError(403), "user_repairable"),
            (_HttpStatusError(400), "terminal"),
            (_HttpStatusError(501), "terminal"),
            (ValueError("business validation failed"), "terminal"),
            (RuntimeError("unknown failure"), "terminal"),
        ]

        for error, expected in cases:
            with self.subTest(error=type(error).__name__, expected=expected):
                self.assertEqual(expected, classify_runtime_error(error).value)


if __name__ == "__main__":
    unittest.main()
