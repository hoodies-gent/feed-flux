from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class TokenPricing:
    input_usd_per_million: float
    output_usd_per_million: float
    cached_input_usd_per_million: float | None = None


def usage_totals(
    usage_by_model: Mapping[str, Mapping[str, Any]],
) -> dict[str, int]:
    input_tokens = sum(
        int(item.get("input_tokens", 0)) for item in usage_by_model.values()
    )
    output_tokens = sum(
        int(item.get("output_tokens", 0)) for item in usage_by_model.values()
    )
    cached_input_tokens = sum(
        int((item.get("input_token_details") or {}).get("cache_read", 0))
        for item in usage_by_model.values()
    )
    total_tokens = sum(
        int(item.get("total_tokens", 0)) for item in usage_by_model.values()
    )
    if total_tokens == 0:
        total_tokens = input_tokens + output_tokens
    return {
        "input_tokens": input_tokens,
        "cached_input_tokens": cached_input_tokens,
        "output_tokens": output_tokens,
        "total_tokens": total_tokens,
    }


def estimate_cost(
    usage: Mapping[str, int],
    pricing: TokenPricing | None,
) -> float | None:
    if pricing is None:
        return None
    cached_input_tokens = min(
        usage["input_tokens"], usage.get("cached_input_tokens", 0)
    )
    uncached_input_tokens = usage["input_tokens"] - cached_input_tokens
    cached_input_rate = (
        pricing.cached_input_usd_per_million
        if pricing.cached_input_usd_per_million is not None
        else pricing.input_usd_per_million
    )
    cost = (
        uncached_input_tokens * pricing.input_usd_per_million
        + cached_input_tokens * cached_input_rate
        + usage["output_tokens"] * pricing.output_usd_per_million
    ) / 1_000_000
    return round(cost, 10)
