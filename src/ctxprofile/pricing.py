from __future__ import annotations

# Standard-tier Claude API prices, USD per million tokens (base input, output).
# Verified 2026-07-24. Cache rates are derived from the base input rate via the
# multipliers below, so a price change is a one-line edit.
PRICES: dict[str, tuple[float, float]] = {
    "claude-opus-4-8": (5.0, 25.0),
    "claude-sonnet-5": (3.0, 15.0),
    "claude-haiku-4-5": (1.0, 5.0),
}

# Cache multipliers relative to the base input rate (uniform across models).
CACHE_WRITE_5M = 1.25
CACHE_WRITE_1H = 2.0
CACHE_READ = 0.1

_MODES = {"input", "output", "cache_write_5m", "cache_write_1h", "cache_read"}


def _base_rates(model: str) -> tuple[float, float]:
    try:
        return PRICES[model]
    except KeyError:
        known = ", ".join(sorted(PRICES))
        raise KeyError(f"unknown model {model!r}; known: {known}") from None


def rate_per_mtok(model: str, mode: str = "input") -> float:
    if mode not in _MODES:
        raise ValueError(f"unknown pricing mode {mode!r}")
    base_in, base_out = _base_rates(model)
    if mode == "output":
        return base_out
    if mode == "input":
        return base_in
    if mode == "cache_write_5m":
        return base_in * CACHE_WRITE_5M
    if mode == "cache_write_1h":
        return base_in * CACHE_WRITE_1H
    return base_in * CACHE_READ  # cache_read


def usd(tokens: float, model: str, mode: str = "input") -> float:
    return tokens / 1_000_000 * rate_per_mtok(model, mode)


def billed_input_usd(
    model: str,
    input_tokens: int,
    cache_write_5m: int = 0,
    cache_write_1h: int = 0,
    cache_read: int = 0,
) -> float:
    """Actual dollars for the input side of a request, given how many tokens were
    freshly read, written to cache, and read from cache."""
    return (
        usd(input_tokens, model, "input")
        + usd(cache_write_5m, model, "cache_write_5m")
        + usd(cache_write_1h, model, "cache_write_1h")
        + usd(cache_read, model, "cache_read")
    )
