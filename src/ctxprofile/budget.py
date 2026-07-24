from __future__ import annotations

import tomllib
from dataclasses import dataclass
from pathlib import Path

from ctxprofile.models import CostReport


@dataclass(frozen=True)
class Budget:
    model: str | None = None
    max_input_tokens: int | None = None
    max_total_usd_cold: float | None = None
    max_dead_tools: int | None = None
    max_wasted_usd_cold: float | None = None


def load_budget(path: str | Path) -> Budget:
    data = tomllib.loads(Path(path).read_text(encoding="utf-8"))
    return Budget(
        model=data.get("model"),
        max_input_tokens=data.get("max_input_tokens"),
        max_total_usd_cold=data.get("max_total_usd_cold"),
        max_dead_tools=data.get("max_dead_tools"),
        max_wasted_usd_cold=data.get("max_wasted_usd_cold"),
    )


def check(report: CostReport, budget: Budget) -> list[str]:
    """Return a list of budget violations (empty means the request is within budget)."""
    violations: list[str] = []
    if budget.max_input_tokens is not None and report.total_tokens > budget.max_input_tokens:
        violations.append(f"input tokens {report.total_tokens} > {budget.max_input_tokens}")
    if budget.max_total_usd_cold is not None and report.total_usd_cold > budget.max_total_usd_cold:
        violations.append(
            f"cost ${report.total_usd_cold:.5f} > ${budget.max_total_usd_cold:.5f}"
        )
    if budget.max_dead_tools is not None and len(report.dead_tools) > budget.max_dead_tools:
        names = ", ".join(report.dead_tools)
        violations.append(f"dead tools {len(report.dead_tools)} > {budget.max_dead_tools} ({names})")
    if budget.max_wasted_usd_cold is not None and report.wasted_usd_cold > budget.max_wasted_usd_cold:
        violations.append(
            f"wasted ${report.wasted_usd_cold:.5f} > ${budget.max_wasted_usd_cold:.5f}"
        )
    return violations
