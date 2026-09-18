from langgraph.types import RetryPolicy

from app.agent.runtime_errors import classify_runtime_error
from app.services.agent_run_store import ErrorCategory


def should_retry_provider_error(error: Exception) -> bool:
    return classify_runtime_error(error) is ErrorCategory.TRANSIENT


PROVIDER_RETRY_POLICY = RetryPolicy(
    initial_interval=0.5,
    backoff_factor=2.0,
    max_interval=4.0,
    max_attempts=3,
    jitter=True,
    retry_on=should_retry_provider_error,
)
