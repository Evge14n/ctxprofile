"""Attribute a rebuilt prompt cache to the component that invalidated it.

Prompt caching is a prefix match, and the API renders a request in a fixed order:
tools, then the system prompt, then the messages. Change anything in that prefix and
everything after it is re-billed at the write rate instead of the read rate. A usage
block tells you a rebuild happened; it cannot tell you what caused it.

Two consecutive captures can, because the cause is the first place their prefixes
differ — and appending a message, which is what a conversation does constantly, is not
a difference. Where nothing differs, this refuses to invent a reason: an unchanged
prefix after a long gap is a TTL expiry, and an unchanged prefix inside the TTL is
reported as unexplained rather than blamed on something.

Whether a rebuild happened at all is read from `cache_read_input_tokens`, not from
the presence of a write. Every turn writes its newly appended suffix; those tokens
were never cacheable before and cost the write rate regardless. Only previously
cached tokens that were not served again were rebuilt, and only those are charged.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import UTC, datetime
from itertools import pairwise
from typing import Any

from ctxprofile import pricing
from ctxprofile.models import KIND_HISTORY, KIND_SYSTEM, KIND_TOOL_DEF

# The default cache lifetime. A rebuild after longer than this, with nothing changed,
# is the cache expiring rather than anything the caller did.
DEFAULT_TTL_SECONDS = 300.0

Signature = list[tuple[str, str, str]]


@dataclass(frozen=True)
class ChurnEvent:
    at: str
    cause: str
    kind: str
    rebuilt_tokens: int
    excess_usd: float
    gap_seconds: float | None


@dataclass(frozen=True)
class ChurnReport:
    model: str
    captures: int
    events: list[ChurnEvent]
    total_excess_usd: float
    ttl_seconds: float


def _without_cache_control(value: Any) -> Any:
    """`cache_control` marks where the prefix breaks; it is not prefix content.

    The documented multi-turn pattern rotates the ephemeral marker onto the newest
    turn and drops it from the older one. The text does not change, so digesting the
    marker would report every ordinary turn as an edit to a message nobody touched.
    """
    if isinstance(value, dict):
        return {k: _without_cache_control(v) for k, v in value.items() if k != "cache_control"}
    if isinstance(value, list):
        return [_without_cache_control(item) for item in value]
    return value


def _digest(value: Any) -> str:
    text = json.dumps(
        _without_cache_control(value), sort_keys=True, ensure_ascii=False, default=str
    )
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]


def prefix_signature(request: dict[str, Any]) -> Signature:
    """The request's cacheable prefix, in the order the API renders it."""
    entries: Signature = []
    for index, tool in enumerate(request.get("tools") or []):
        name = tool.get("name", "<unnamed>") if isinstance(tool, dict) else f"<tool>[{index}]"
        entries.append((KIND_TOOL_DEF, str(name), _digest(tool)))
    system = request.get("system")
    if system:
        entries.append((KIND_SYSTEM, "system", _digest(system)))
    for index, message in enumerate(request.get("messages") or []):
        role = message.get("role") if isinstance(message, dict) else "?"
        entries.append((KIND_HISTORY, f"{role}[{index}]", _digest(message)))
    return entries


def first_divergence(before: Signature, after: Signature) -> tuple[str, str] | None:
    """The first place the cacheable prefix stops matching, named from the side that moved.

    Walking past the end of `after` is not a match failure in the other direction: a
    conversation that only grew keeps its cached prefix, so running out of `before`
    means nothing diverged. But a component *removed* from the prefix shifts everything
    after it, and naming whatever slid into that slot would point at a component that is
    byte-identical in both captures.
    """
    present_before = {(kind, name) for kind, name, _ in before}
    present_after = {(kind, name) for kind, name, _ in after}
    for index, old in enumerate(before):
        if index >= len(after):
            return old[0], f"{old[1]} removed"
        new = after[index]
        if old == new:
            continue
        old_key, new_key = (old[0], old[1]), (new[0], new[1])
        if old_key == new_key:
            return old[0], old[1]  # same component, different content
        left, arrived = old_key not in present_after, new_key not in present_before
        if left and arrived:
            return old[0], f"{old[1]} -> {new[1]}"  # this slot holds something else now
        if left:
            return old[0], f"{old[1]} removed"
        if arrived:
            return new[0], f"{new[1]} inserted"
        return new[0], new[1]  # both still present: the prefix was reordered
    return None


def _excess_usd(tokens: int, model: str, usage: dict[str, Any]) -> float:
    """What re-writing `tokens` cost above what reading them from cache would have."""
    split = usage.get("cache_creation") or {}
    write_1h = min(int(split.get("ephemeral_1h_input_tokens", 0) or 0), tokens)
    write_5m = max(0, tokens - write_1h)
    written = pricing.billed_input_usd(model, 0, write_5m, write_1h, 0)
    if_it_had_hit = pricing.billed_input_usd(model, 0, 0, 0, tokens)
    return written - if_it_had_hit


def _cached(usage: dict[str, Any]) -> tuple[int, int]:
    """(served from cache, written to cache) for one request."""
    return (
        int(usage.get("cache_read_input_tokens", 0) or 0),
        int(usage.get("cache_creation_input_tokens", 0) or 0),
    )


def declared_ttl(request: dict[str, Any], default: float) -> float:
    """The longest cache lifetime the request itself asks for.

    A one-hour breakpoint that rebuilds after six minutes has not expired, and calling
    it a TTL expiry excuses a real invalidation.
    """
    longest = default

    def visit(node: Any) -> None:
        nonlocal longest
        if isinstance(node, dict):
            control = node.get("cache_control")
            if isinstance(control, dict) and str(control.get("ttl", "")).strip() == "1h":
                longest = max(longest, 3600.0)
            for value in node.values():
                visit(value)
        elif isinstance(node, list):
            for item in node:
                visit(item)

    visit(request)
    return longest


def _usable(captures: list[dict[str, Any]]) -> list[tuple[datetime, str, dict[str, Any], dict[str, Any]]]:
    rows = []
    for capture in captures:
        at = capture.get("captured_at")
        request = capture.get("request")
        response = capture.get("response")
        usage = response.get("usage") if isinstance(response, dict) else None
        if not at or not isinstance(request, dict) or not isinstance(usage, dict):
            continue
        try:
            when = datetime.fromisoformat(str(at))
        except ValueError:
            continue
        if when.tzinfo is None:
            # A hand-written capture next to proxy-written ones would otherwise make
            # the sort compare naive against aware and fail on the whole corpus.
            when = when.replace(tzinfo=UTC)
        rows.append((when, str(at), request, usage))
    rows.sort(key=lambda row: row[0])
    return rows


def analyze_churn(
    captures: list[dict[str, Any]], ttl_seconds: float = DEFAULT_TTL_SECONDS
) -> ChurnReport:
    rows = _usable(captures)
    model = str(rows[-1][2].get("model")) if rows else ""

    events: list[ChurnEvent] = []
    total = 0.0
    for (previous_when, _, previous, previous_usage), (when, at, request, usage) in pairwise(rows):
        read, created = _cached(usage)
        previously_cached = sum(_cached(previous_usage))
        # Tokens appended past the old prefix were never cacheable, so writing them is
        # the cache working. Only previously cached tokens that were NOT served again
        # were rebuilt — and if none were, the prefix held and nothing churned.
        rebuilt = min(max(0, previously_cached - read), created)
        if rebuilt <= 0:
            continue
        gap = (when - previous_when).total_seconds()
        ttl = declared_ttl(request, ttl_seconds)
        this_model = str(request.get("model") or model)
        kind = ""
        if str(previous.get("model") or this_model) != this_model:
            cause = "model change"
        else:
            divergence = first_divergence(prefix_signature(previous), prefix_signature(request))
            if divergence:
                kind, cause = divergence
            elif gap > ttl:
                cause = "ttl expiry"
            else:
                cause = "unexplained"
        excess = _excess_usd(rebuilt, this_model, usage)
        total += excess
        events.append(ChurnEvent(at, cause, kind, rebuilt, excess, gap))

    return ChurnReport(model, len(rows), events, total, ttl_seconds)
