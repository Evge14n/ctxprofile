from __future__ import annotations

from typing import Any

from ctxprofile.cost import analyze
from ctxprofile.models import ComponentDelta, CostReport, ReportDiff


def diff_reports(a: CostReport, b: CostReport) -> ReportDiff:
    a_by = {c.name: c for c in a.components}
    b_by = {c.name: c for c in b.components}
    rows: list[ComponentDelta] = []
    for name in a_by.keys() | b_by.keys():
        ca = a_by.get(name)
        cb = b_by.get(name)
        tokens_a = ca.tokens if ca else 0
        tokens_b = cb.tokens if cb else 0
        usd_a = ca.usd_cold if ca else 0.0
        usd_b = cb.usd_cold if cb else 0.0
        status = "removed" if ca and not cb else "added" if cb and not ca else ""
        present = cb or ca
        kind = present.kind if present is not None else ""
        rows.append(ComponentDelta(name, kind, tokens_b - tokens_a, usd_b - usd_a, status))
    rows.sort(key=lambda r: abs(r.delta_usd_cold), reverse=True)
    return ReportDiff(
        model=b.model,
        total_usd_cold_a=a.total_usd_cold,
        total_usd_cold_b=b.total_usd_cold,
        rows=rows,
        dead_tools_a=a.dead_tools,
        dead_tools_b=b.dead_tools,
    )


def compare_payloads(
    payload_a: dict[str, Any], payload_b: dict[str, Any], model_override: str | None = None
) -> ReportDiff:
    return diff_reports(
        analyze(payload_a, model_override=model_override),
        analyze(payload_b, model_override=model_override),
    )
