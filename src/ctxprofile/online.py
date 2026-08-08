"""Exact per-component token counts via Anthropic's `count_tokens` endpoint.

The offline split is a ~4 chars/token heuristic. This module replaces it for the
two parts the API serializes independently of everything else — the system prompt
and each individual tool definition — with a measured count: the difference
between a request that carries the component and one that does not. That is the
number the dead-tool report is built on, so it is the one worth measuring rather
than estimating.

Optional. The core CLI stays dependency-free; this path needs the `anthropic`
SDK (`pip install "ctxprofile[online]"`) and an API key. Counting tokens is free
but rate-limited, so the call count is bounded and reported.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol

from ctxprofile.apportion import apportion
from ctxprofile.models import KIND_SYSTEM, KIND_TOOL_DEF

# `count_tokens` rejects an empty `messages` array, so every probe carries this
# constant and it cancels out of each marginal by subtraction.
PROBE_MESSAGES: list[dict[str, Any]] = [{"role": "user", "content": "."}]


class TokenCounter(Protocol):
    def __call__(
        self,
        *,
        model: str,
        messages: list[dict[str, Any]],
        system: Any = None,
        tools: list[dict[str, Any]] | None = None,
    ) -> int: ...


@dataclass(frozen=True)
class ExactSplit:
    """Measured tokens for the components the API counts independently.

    `tools` sums to `tools_total`: the shared tool-block overhead (the preamble
    the API adds once when any tool is present, plus tokenizer boundary effects)
    is distributed across the tool rows rather than left dangling, and reported
    separately in `tool_overhead` so the distribution stays visible.
    """

    system: int | None
    tools: dict[str, int]
    tools_total: int
    tool_overhead: int
    calls: int

    def by_component(self) -> dict[tuple[str, str], int]:
        measured: dict[tuple[str, str], int] = {}
        if self.system is not None:
            measured[(KIND_SYSTEM, "system")] = self.system
        for name, tokens in self.tools.items():
            measured[(KIND_TOOL_DEF, name)] = tokens
        return measured


def anthropic_counter(client: Any) -> TokenCounter:
    """Adapt an `anthropic.Anthropic` client to the counter protocol."""

    def count(
        *,
        model: str,
        messages: list[dict[str, Any]],
        system: Any = None,
        tools: list[dict[str, Any]] | None = None,
    ) -> int:
        kwargs: dict[str, Any] = {"model": model, "messages": messages}
        if system:
            kwargs["system"] = system
        if tools:
            kwargs["tools"] = tools
        return int(client.messages.count_tokens(**kwargs).input_tokens)

    return count


def default_counter() -> TokenCounter:
    try:
        import anthropic
    except ImportError as error:
        raise RuntimeError(
            'online counting needs the Anthropic SDK: pip install "ctxprofile[online]"'
        ) from error
    return anthropic_counter(anthropic.Anthropic())


def planned_calls(request: dict[str, Any]) -> int:
    """How many `count_tokens` calls `measure` will make for this request."""
    tools = _tools(request)
    has_system = bool(request.get("system"))
    if not tools and not has_system:
        return 0
    calls = 1  # the floor every marginal is measured against
    if has_system:
        calls += 1
    if tools:
        calls += 1 + (len(tools) if len(tools) > 1 else 0)
    return calls


def _tools(request: dict[str, Any]) -> list[dict[str, Any]]:
    return [t for t in (request.get("tools") or []) if isinstance(t, dict)]


def measure(
    request: dict[str, Any],
    model: str,
    counter: TokenCounter,
    *,
    max_calls: int | None = None,
) -> ExactSplit:
    """Measure system and per-tool tokens by differencing `count_tokens` probes."""
    needed = planned_calls(request)
    if not needed:
        return ExactSplit(system=None, tools={}, tools_total=0, tool_overhead=0, calls=0)
    if max_calls is not None and needed > max_calls:
        raise ValueError(
            f"this request needs {needed} count_tokens calls, limit is {max_calls}; "
            f"raise --online-max-calls to measure it"
        )

    calls = 0

    def count(**kwargs: Any) -> int:
        nonlocal calls
        calls += 1
        return counter(model=model, messages=PROBE_MESSAGES, **kwargs)

    floor = count()

    system = request.get("system")
    system_tokens = count(system=system) - floor if system else None

    tools = _tools(request)
    names = [str(tool.get("name", f"<unnamed>[{i}]")) for i, tool in enumerate(tools)]
    tool_tokens: dict[str, int] = {}
    tools_total = 0
    overhead = 0

    if tools:
        tools_total = count(tools=tools) - floor
        if len(tools) == 1:
            # Nothing to difference against; the single tool carries the whole block.
            tool_tokens[names[0]] = tools_total
        else:
            # Leave-one-out: the shared preamble is present in both terms and cancels,
            # so each marginal is that tool's own serialized cost.
            marginals = [
                max(0, tools_total - (count(tools=tools[:i] + tools[i + 1 :]) - floor))
                for i in range(len(tools))
            ]
            overhead = tools_total - sum(marginals)
            spread = apportion(marginals, tools_total)
            for name, tokens in zip(names, spread, strict=True):
                tool_tokens[name] = tool_tokens.get(name, 0) + tokens

    return ExactSplit(
        system=system_tokens,
        tools=tool_tokens,
        tools_total=tools_total,
        tool_overhead=overhead,
        calls=calls,
    )
