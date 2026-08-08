from __future__ import annotations

from typing import Any

from ctxprofile.mcp_audit import AuditReport
from ctxprofile.models import CostReport


def report_to_dict(report: CostReport) -> dict[str, Any]:
    return {
        "model": report.model,
        "total_tokens": report.total_tokens,
        "reconciled": report.reconciled,
        "cached": report.cached,
        "total_usd_cold": report.total_usd_cold,
        "total_usd_effective": report.total_usd_effective,
        "dead_tools": report.dead_tools,
        "wasted_usd_cold": report.wasted_usd_cold,
        "measured_calls": report.measured_calls,
        "components": [
            {
                "name": r.name,
                "kind": r.kind,
                "tokens": r.tokens,
                "pct": r.pct,
                "usd_cold": r.usd_cold,
                "usd_cached": r.usd_cached,
                "usd_effective": r.usd_effective,
                "unused": r.unused,
                "exact": r.exact,
            }
            for r in report.components
        ],
    }


def audit_to_dict(report: AuditReport) -> dict[str, Any]:
    return {
        "model": report.model,
        "window_api_calls": report.window_api_calls,
        "measured_calls": report.measured_calls,
        "servers": [
            {
                "server": s.server,
                "tool_count": s.tool_count,
                "tokens_per_request": s.tokens_per_request,
                "calls": s.calls,
                "usd_per_request_cold": s.usd_per_request_cold,
                "uncalled_tools": s.uncalled_tools,
            }
            for s in report.servers
        ],
    }
