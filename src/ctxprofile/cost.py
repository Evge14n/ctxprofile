from __future__ import annotations

from collections import Counter
from typing import Any

from ctxprofile import pricing
from ctxprofile.apportion import apportion
from ctxprofile.ingest import parse_request
from ctxprofile.models import KIND_TOOL_DEF, Component, ComponentCost, CostReport
from ctxprofile.online import ExactSplit, TokenCounter, measure
from ctxprofile.tokenizer import estimate_tokens

# The shared cost of having any tools at all: the preamble the API adds once, plus
# tokenizer effects at the joins between schemas. Named, not hidden, because it is the
# one part of the tool block that removing a tool does not recover.
TOOL_BLOCK_OVERHEAD = "(tool block overhead)"

_TOOL_USE_BLOCKS = frozenset({"tool_use", "server_tool_use", "mcp_tool_use"})


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


def called_in_response(response: Any) -> set[str]:
    """Tool names the assistant invoked in this turn's response.

    A capture holds the request's history plus the response it produced. The tool call
    this turn made lives in the response and has not been echoed back into `messages`
    yet, so reading only the request marks the tool the model just used as never called.
    """
    if not isinstance(response, dict):
        return set()
    called: set[str] = set()
    for block in response.get("content") or []:
        if not isinstance(block, dict):
            continue
        if block.get("type") in _TOOL_USE_BLOCKS and block.get("name"):
            called.add(str(block["name"]))
    return called


def _apply_measurement(
    components: list[Component], exact: ExactSplit | None
) -> tuple[list[Component], dict[tuple[str, str], int]]:
    """Attach measured token counts to the components they belong to.

    Two things need care. A measurement is keyed by name, so if two tools share a name
    there is no way to tell which row owns which count — those fall back to the estimate
    rather than being counted twice. And the tool block costs more than the sum of the
    per-tool marginals; that residual gets its own row instead of being folded into the
    tools, because deleting a tool does not remove it.
    """
    if exact is None:
        return components, {}

    measured = exact.by_component()
    seen = Counter((c.kind, c.name) for c in components)
    measured = {key: value for key, value in measured.items() if seen[key] == 1}

    if exact.tool_overhead > 0:
        components = [*components, Component(KIND_TOOL_DEF, TOOL_BLOCK_OVERHEAD, "")]
        measured[(KIND_TOOL_DEF, TOOL_BLOCK_OVERHEAD)] = exact.tool_overhead
    return components, measured


def _estimator_error(
    components: list[Component], measured: dict[tuple[str, str], int]
) -> float | None:
    """How far the offline heuristic was from the measured value, where both exist.

    Measuring some components tells you something about the ones you didn't measure:
    if the ~4 chars/token rule ran 20% low on this request's tool schemas, the history
    rows it also produced are probably off in the same direction. Reporting that is the
    difference between an estimate and an estimate you know the size of.
    """
    heuristic = 0
    real = 0
    for component in components:
        # The overhead row is measured but has no text, so it has no estimate to compare.
        if component.name == TOOL_BLOCK_OVERHEAD:
            continue
        value = measured.get((component.kind, component.name))
        if value is None:
            continue
        heuristic += estimate_tokens(component.text)
        real += value
    if real <= 0 or heuristic <= 0:
        return None
    # Relative to the measured value, so -0.7 reads as "the estimate was 70% below it".
    return heuristic / real - 1.0


def _split_tokens(
    components: list[Component],
    measured: dict[tuple[str, str], int],
    billed: int | None,
) -> tuple[list[int], bool]:
    """Token count per component, and whether it reconciles to the billed total.

    Measured components keep their real counts. Whatever the billed total has left
    over is apportioned across the remaining components by heuristic weight, so the
    rows still sum to the exact billed input.
    """
    heuristic = [estimate_tokens(c.text) for c in components]
    fixed = [measured.get((c.kind, c.name)) for c in components]
    free = [i for i, value in enumerate(fixed) if value is None]
    tokens = [value if value is not None else heuristic[i] for i, value in enumerate(fixed)]

    # A billed total of zero describes no real request; scaling to it would zero every
    # component while still calling the result exact.
    if billed is None or billed <= 0:
        return tokens, False

    remainder = billed - sum(value for value in fixed if value is not None)
    if remainder < 0 or (not free and remainder != 0):
        # The usage block doesn't describe this request shape (a measurement larger
        # than the whole billed input, or nothing left to absorb the difference).
        # Report what was measured rather than forcing a reconciliation.
        return tokens, False

    for i, share in zip(free, apportion([heuristic[i] for i in free], remainder), strict=True):
        tokens[i] = share
    return tokens, True


def build_report(
    components: list[Component],
    defined: set[str],
    called: set[str],
    model: str,
    usage: dict[str, Any] | None = None,
    exact: ExactSplit | None = None,
) -> CostReport:
    components, measured = _apply_measurement(components, exact)
    billed = _billed_input(usage)
    scaled, reconciled = _split_tokens(components, measured, billed)
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
                exact=(component.kind, component.name) in measured,
            )
        )

    rows.sort(key=lambda r: r.usd_cold, reverse=True)
    return CostReport(
        model,
        grand,
        reconciled,
        rows,
        dead,
        wasted,
        total_cold,
        total_effective,
        cached,
        measured_calls=exact.calls if exact else 0,
        usage_seen=billed is not None,
        estimator_error=_estimator_error(components, measured),
    )


def analyze(
    payload: dict[str, Any],
    model_override: str | None = None,
    counter: TokenCounter | None = None,
    max_calls: int | None = None,
) -> CostReport:
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

    exact = measure(request, model, counter, max_calls=max_calls) if counter else None
    components, defined, called = parse_request(request)
    return build_report(
        components, defined, called | called_in_response(response), model, usage, exact
    )
