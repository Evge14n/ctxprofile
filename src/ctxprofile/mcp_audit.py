from __future__ import annotations

from collections import Counter
from collections.abc import Iterable
from dataclasses import dataclass
from typing import Any

from ctxprofile import pricing
from ctxprofile.ingest import parse_request
from ctxprofile.ingest_sdk import Trace
from ctxprofile.lockfile import server_prefix
from ctxprofile.models import KIND_TOOL_DEF
from ctxprofile.online import TokenCounter, measure
from ctxprofile.tokenizer import estimate_tokens

LOCAL = "(local)"


@dataclass(frozen=True)
class ServerAudit:
    server: str
    tool_count: int
    tokens_per_request: int
    calls: int
    usd_per_request_cold: float
    uncalled_tools: list[str]


@dataclass(frozen=True)
class AuditReport:
    model: str
    window_api_calls: int
    servers: list[ServerAudit]
    measured_calls: int = 0


def _group(tool: str) -> str:
    return server_prefix(tool) or LOCAL


def audit(
    defs_request: dict[str, Any],
    traces: Iterable[Trace],
    model_override: str | None = None,
    counter: TokenCounter | None = None,
    max_calls: int | None = None,
) -> AuditReport:
    """Join tool schema tokens (from a raw request) with call counts (from a trace
    corpus), grouped by MCP server, so shipped-but-unused tools surface by server."""
    components, defined, _ = parse_request(defs_request)
    model = model_override or defs_request.get("model") or "claude-opus-4-8"
    tool_tokens = {c.name: estimate_tokens(c.text) for c in components if c.kind == KIND_TOOL_DEF}

    measured_calls = 0
    if counter:
        # Only the tool schemas matter here, so don't spend a call on the system prompt.
        split = measure({"tools": defs_request.get("tools")}, model, counter, max_calls=max_calls)
        tool_tokens.update(split.tools)
        measured_calls = split.calls

    calls: Counter[str] = Counter()
    window = 0
    for trace in traces:
        calls.update(trace.tools_called)
        window += trace.api_calls

    by_server: dict[str, list[str]] = {}
    for tool in sorted(defined):
        by_server.setdefault(_group(tool), []).append(tool)

    servers: list[ServerAudit] = []
    for server, tools in by_server.items():
        tokens = sum(tool_tokens.get(t, 0) for t in tools)
        server_calls = sum(calls.get(t, 0) for t in tools)
        uncalled = [t for t in tools if calls.get(t, 0) == 0]
        servers.append(
            ServerAudit(
                server=server,
                tool_count=len(tools),
                tokens_per_request=tokens,
                calls=server_calls,
                usd_per_request_cold=pricing.usd(tokens, model, "input"),
                uncalled_tools=uncalled,
            )
        )

    # Fully unused servers first, then by token weight, so the worst waste is on top.
    servers.sort(key=lambda s: (s.calls > 0, -s.tokens_per_request))
    return AuditReport(
        model=model, window_api_calls=window, servers=servers, measured_calls=measured_calls
    )
