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
    # Total and dollars are exact when a usage block is present; the per-component
    # split stays a proportional estimate. When absent, everything is estimated.
    scale = billed / est_total if billed and est_total else 1.0
    reconciled = billed is not None

    scaled = [round(t * scale) for t in tokens]
    # When reconciled, the billed total is exact; push the rounding remainder onto
    # the largest component so the per-component tokens sum to the billed total.
    if reconciled and billed and scaled:
        remainder = billed - sum(scaled)
        biggest = max(range(len(scaled)), key=lambda i: scaled[i])
        scaled[biggest] = max(0, scaled[biggest] + remainder)
    grand = sum(scaled) or 1

    dead = sorted(defined - called)
    rows: list[ComponentCost] = []
    wasted = 0.0
    total_cold = 0.0
    for component, token_count in zip(components, scaled, strict=True):
        usd_cold = pricing.usd(token_count, model, "input")
        usd_cached = pricing.usd(token_count, model, "cache_read")
        unused = component.kind == KIND_TOOL_DEF and component.name in dead
        total_cold += usd_cold
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
                unused=unused,
            )
        )

    rows.sort(key=lambda r: r.usd_cold, reverse=True)
    return CostReport(model, grand, reconciled, rows, dead, wasted, total_cold)


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
