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
    unused: bool = False


@dataclass(frozen=True)
class CostReport:
    model: str
    total_tokens: int
    reconciled: bool
    components: list[ComponentCost]
    dead_tools: list[str]
    wasted_usd_cold: float
    total_usd_cold: float
