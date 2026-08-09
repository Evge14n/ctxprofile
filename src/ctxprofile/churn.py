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


def _digest(value: Any) -> str:
    text = json.dumps(value, sort_keys=True, ensure_ascii=False, default=str)
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
    """The first entry that changed, named as it stands now.

    Zipping stops at the shorter side on purpose: a conversation that only grew has no
    divergence, and its cached prefix is still valid.
    """
    for old, new in zip(before, after, strict=False):
        if old != new:
            return new[0], new[1]
    return None


def _excess_usd(tokens: int, model: str, usage: dict[str, Any]) -> float:
    """What the rebuild cost above what a cache hit would have cost."""
    split = usage.get("cache_creation") or {}
    write_1h = int(split.get("ephemeral_1h_input_tokens", 0) or 0)
    write_5m = int(split.get("ephemeral_5m_input_tokens", 0) or 0) or max(0, tokens - write_1h)
    written = pricing.billed_input_usd(model, 0, write_5m, write_1h, 0)
    if_it_had_hit = pricing.billed_input_usd(model, 0, 0, 0, tokens)
    return written - if_it_had_hit


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
    for (previous_when, _, previous, _), (when, at, request, usage) in pairwise(rows):
        rebuilt = int(usage.get("cache_creation_input_tokens", 0) or 0)
        if rebuilt <= 0:
            continue
        gap = (when - previous_when).total_seconds()
        this_model = str(request.get("model") or model)
        kind = ""
        if str(previous.get("model") or this_model) != this_model:
            cause = "model change"
        else:
            divergence = first_divergence(prefix_signature(previous), prefix_signature(request))
            if divergence:
                kind, cause = divergence
            elif gap > ttl_seconds:
                cause = "ttl expiry"
            else:
                cause = "unexplained"
        excess = _excess_usd(rebuilt, this_model, usage)
        total += excess
        events.append(ChurnEvent(at, cause, kind, rebuilt, excess, gap))

    return ChurnReport(model, len(rows), events, total, ttl_seconds)
