import os
from dataclasses import dataclass
from typing import Literal

# Importing Config is what loads .env into the environment. Without it these
# settings silently read nothing and a configured provider looks unconfigured.
from app.core.config import Config as _Config  # noqa: F401


DecisionMode = Literal["off", "shadow"]
_MODES: tuple[DecisionMode, ...] = ("off", "shadow")
_PROVIDERS = ("jev", "llm", "fake")


@dataclass(frozen=True)
class DecisionSettings:
    mode: DecisionMode = "off"
    provider: str = "jev"
    api_key: str | None = None
    model: str = "jev-latest"
    base_url: str = "https://api.typesafe.ai"
    timeout_seconds: float = 10.0
    shadow_timeout_seconds: float = 3.0
    max_attempts: int = 3
    max_items_per_request: int = 20
    token_budget: int = 24000
    preview_chars: int = 400


def _env_float(name: str, default: float) -> float:
    value = os.getenv(name)
    try:
        return float(value) if value else default
    except ValueError:
        return default


def _env_int(name: str, default: int) -> int:
    value = os.getenv(name)
    try:
        return int(value) if value else default
    except ValueError:
        return default


def decision_settings() -> DecisionSettings:
    # Unknown values fail closed: an unrecognised mode must never enable the provider.
    mode = (os.getenv("DECISION_PROVIDER_MODE") or "off").strip().lower()
    provider = (os.getenv("DECISION_PROVIDER") or "jev").strip().lower()
    return DecisionSettings(
        mode=mode if mode in _MODES else "off",
        provider=provider if provider in _PROVIDERS else "jev",
        api_key=os.getenv("TYPESAFE_API_KEY") or None,
        model=os.getenv("TYPESAFE_MODEL_NAME") or "jev-latest",
        base_url=(os.getenv("TYPESAFE_BASE_URL") or "https://api.typesafe.ai").rstrip("/"),
        timeout_seconds=_env_float("DECISION_TIMEOUT_SECONDS", 10.0),
        shadow_timeout_seconds=_env_float("DECISION_SHADOW_TIMEOUT_SECONDS", 3.0),
        max_attempts=max(1, _env_int("DECISION_MAX_ATTEMPTS", 3)),
        max_items_per_request=max(1, _env_int("DECISION_MAX_ITEMS_PER_REQUEST", 20)),
        token_budget=max(1000, _env_int("DECISION_TOKEN_BUDGET", 24000)),
        preview_chars=max(0, _env_int("DECISION_PREVIEW_CHARS", 400)),
    )


def is_enabled(settings: DecisionSettings | None = None) -> bool:
    settings = settings or decision_settings()
    return settings.mode != "off"
