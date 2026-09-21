from pydantic import ValidationError

from app.services.agent_run_store import ErrorCategory


_TRANSIENT_STATUS_CODES = {429, 500, 502, 503, 504}
_USER_REPAIRABLE_STATUS_CODES = {401, 403}


def _is_timeout_error(error: Exception) -> bool:
    suffixes = ("timeout", "timeouterror", "timeoutexception")
    return any(cls.__name__.lower().endswith(suffixes) for cls in type(error).__mro__)


def _http_status_code(error: Exception) -> int | None:
    status_code = getattr(error, "status_code", None)
    if isinstance(status_code, int):
        return status_code
    response = getattr(error, "response", None)
    status_code = getattr(response, "status_code", None)
    return status_code if isinstance(status_code, int) else None


def classify_runtime_error(error: Exception) -> ErrorCategory:
    stored_category = getattr(error, "error_category", None)
    if stored_category is not None:
        return ErrorCategory(stored_category)
    if _is_timeout_error(error):
        return ErrorCategory.TRANSIENT
    if isinstance(error, ValidationError):
        return ErrorCategory.LLM_TOOL_REPAIRABLE
    if isinstance(error, PermissionError):
        return ErrorCategory.USER_REPAIRABLE

    status_code = _http_status_code(error)
    if status_code in _TRANSIENT_STATUS_CODES:
        return ErrorCategory.TRANSIENT
    if status_code in _USER_REPAIRABLE_STATUS_CODES:
        return ErrorCategory.USER_REPAIRABLE
    return ErrorCategory.TERMINAL
