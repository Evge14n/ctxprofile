from __future__ import annotations

from dataclasses import dataclass

# Kinds a request's input can be split into.
KIND_SYSTEM = "system"
KIND_TOOL_DEF = "tool_def"
KIND_HISTORY = "history"
KIND_TOOL_RESULT = "tool_result"
KIND_CURRENT_USER = "current_user"


@dataclass(frozen=True)
class Component:
    kind: str
    name: str
    text: str


@dataclass(frozen=True)
class ComponentCost:
    kind: str
    name: str
    tokens: int
    pct: float
    usd_cold: float
    usd_cached: float
    usd_effective: float
    unused: bool = False
    # True when this row's tokens were measured against the count_tokens endpoint
    # rather than estimated by the offline heuristic.
    exact: bool = False


@dataclass(frozen=True)
class CostReport:
    model: str
    total_tokens: int
    reconciled: bool
    components: list[ComponentCost]
    dead_tools: list[str]
    wasted_usd_cold: float
    total_usd_cold: float
    total_usd_effective: float
    cached: bool
    measured_calls: int = 0


@dataclass(frozen=True)
class ComponentDelta:
    name: str
    kind: str
    delta_tokens: int
    delta_usd_cold: float
    status: str


STATIC_KINDS = frozenset({KIND_SYSTEM, KIND_TOOL_DEF})


@dataclass(frozen=True)
class ReportDiff:
    model: str
    total_usd_cold_a: float
    total_usd_cold_b: float
    rows: list[ComponentDelta]
    dead_tools_a: list[str]
    dead_tools_b: list[str]
