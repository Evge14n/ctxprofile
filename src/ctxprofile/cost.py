from __future__ import annotations

from typing import Any

from ctxprofile import pricing
from ctxprofile.ingest import parse_request
from ctxprofile.models import KIND_TOOL_DEF, Component, ComponentCost, CostReport
from ctxprofile.tokenizer import estimate_tokens


def _billed_input(usage: dict[str, Any] | None) -> int | None:
    if not usage or not isinstance(usage.get("input_tokens"), int):
        return None
    return (
        int(usage["input_tokens"])
        + int(usage.get("cache_creation_input_tokens", 0))
        + int(usage.get("cache_read_input_tokens", 0))
    )


def _effective_rate(usage: dict[str, Any] | None, model: str) -> tuple[float, bool]:
    """Blended $/token for the input side, from the actual cold/cache-write/cache-read
    split in usage. Returns (rate_per_token, cached). Falls back to the cold rate."""
    cold = pricing.rate_per_mtok(model, "input") / 1_000_000
    if not usage:
        return cold, False
    fresh = int(usage.get("input_tokens", 0))
    created = int(usage.get("cache_creation_input_tokens", 0))
    read = int(usage.get("cache_read_input_tokens", 0))
    split = usage.get("cache_creation") or {}
    if split:
        write_5m = int(split.get("ephemeral_5m_input_tokens", 0))
        write_1h = int(split.get("ephemeral_1h_input_tokens", 0))
    else:
        write_5m, write_1h = created, 0
    total = fresh + created + read
    if total <= 0:
        return cold, False
    billed = pricing.billed_input_usd(model, fresh, write_5m, write_1h, read)
    return billed / total, created > 0 or read > 0


def build_report(
    components: list[Component],
    defined: set[str],
    called: set[str],
    model: str,
    usage: dict[str, Any] | None = None,
) -> CostReport:
    tokens = [estimate_tokens(c.text) for c in components]
    est_total = sum(tokens)

    billed = _billed_input(usage)
    reconciled = billed is not None

    # When reconciled, the billed total is exact; apportion it across components by
    # estimate weight with the largest-remainder (Hamilton) method, which keeps
    # every part non-negative and makes the parts sum to the billed total exactly.
    if reconciled and billed and est_total > 0:
        exact = [t * billed / est_total for t in tokens]
        scaled = [int(x) for x in exact]
        shortfall = billed - sum(scaled)
        for i in sorted(range(len(tokens)), key=lambda i: exact[i] - scaled[i], reverse=True)[
            :shortfall
        ]:
            scaled[i] += 1
    else:
        scaled = list(tokens)
    grand = sum(scaled) or 1

    eff_rate, cached = _effective_rate(usage, model)
    dead = sorted(defined - called)
    rows: list[ComponentCost] = []
    wasted = 0.0
    total_cold = 0.0
    total_effective = 0.0
    for component, token_count in zip(components, scaled, strict=True):
        usd_cold = pricing.usd(token_count, model, "input")
        usd_cached = pricing.usd(token_count, model, "cache_read")
        usd_effective = token_count * eff_rate
        unused = component.kind == KIND_TOOL_DEF and component.name in dead
        total_cold += usd_cold
        total_effective += usd_effective
        if unused:
            wasted += usd_cold
        rows.append(
            ComponentCost(
                kind=component.kind,
                name=component.name,
                tokens=token_count,
                pct=100.0 * token_count / grand,
                usd_cold=usd_cold,
                usd_cached=usd_cached,
                usd_effective=usd_effective,
                unused=unused,
            )
        )

    rows.sort(key=lambda r: r.usd_cold, reverse=True)
    return CostReport(
        model, grand, reconciled, rows, dead, wasted, total_cold, total_effective, cached
    )


def analyze(payload: dict[str, Any], model_override: str | None = None) -> CostReport:
    request = payload.get("request", payload)
    usage = None
    response = payload.get("response")
    if isinstance(response, dict):
        usage = response.get("usage")
    if usage is None and isinstance(payload.get("usage"), dict):
        usage = payload["usage"]

    model = model_override or request.get("model")
    if not model:
        raise ValueError("model not found in request; pass model_override / --model")

    components, defined, called = parse_request(request)
    return build_report(components, defined, called, model, usage)
