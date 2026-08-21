from __future__ import annotations

from typing import Any

from ctxprofile.churn import ChurnReport
from ctxprofile.lint import LintReport
from ctxprofile.mcp_audit import AuditReport
from ctxprofile.models import CostReport
from ctxprofile.pricing import usd


def report_to_dict(report: CostReport) -> dict[str, Any]:
    return {
        "model": report.model,
        "total_tokens": report.total_tokens,
        "reconciled": report.reconciled,
        "usage_seen": report.usage_seen,
        "cached": report.cached,
        "total_usd_cold": report.total_usd_cold,
        "total_usd_effective": report.total_usd_effective,
        "dead_tools": report.dead_tools,
        "wasted_usd_cold": report.wasted_usd_cold,
        "measured_calls": report.measured_calls,
        "estimator_error": report.estimator_error,
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
                "unreachable_tokens": s.unreachable_tokens,
            }
            for s in report.servers
        ],
    }


def churn_to_dict(report: ChurnReport) -> dict[str, Any]:
    return {
        "model": report.model,
        "captures": report.captures,
        "ttl_seconds": report.ttl_seconds,
        "total_excess_usd": report.total_excess_usd,
        "events": [
            {
                "at": e.at,
                "cause": e.cause,
                "kind": e.kind,
                "rebuilt_tokens": e.rebuilt_tokens,
                "excess_usd": e.excess_usd,
                "gap_seconds": e.gap_seconds,
            }
            for e in report.events
        ],
    }


def lint_to_dict(report: LintReport, model: str) -> dict[str, Any]:
    return {
        "model": model,
        "baseline_tokens": report.baseline_tokens,
        "removable_tokens": report.removable_tokens,
        "removable_pct": round(report.removable_pct, 2),
        "removable_usd_cold": usd(report.removable_tokens, model),
        "exact": report.exact,
        "findings": [
            {
                "tool": f.tool,
                "check": f.check,
                "names": f.names,
                "tokens": f.tokens,
            }
            for f in report.findings
        ],
    }
