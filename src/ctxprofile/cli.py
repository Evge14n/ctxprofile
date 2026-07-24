from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

from ctxprofile.budget import check, load_budget
from ctxprofile.capture import serve
from ctxprofile.compare import compare_payloads
from ctxprofile.cost import analyze
from ctxprofile.ingest_sdk import parse_trace_file
from ctxprofile.lockfile import build_lock, diff_lock, load_lock, static_summary, write_lock
from ctxprofile.mcp_audit import AuditReport, audit
from ctxprofile.models import CostReport, ReportDiff


def format_report(report: CostReport) -> str:
    basis = "exact $ (reconciled to usage)" if report.reconciled else "estimated (no usage block)"
    lines = [
        f"model: {report.model}   input tokens: {report.total_tokens}   {basis}",
        "",
        f"  {'component':<22}{'kind':<14}{'tokens':>8}{'%':>7}{'$ cold':>10}{'$ cached':>10}",
    ]
    for row in report.components:
        flag = "  [UNUSED]" if row.unused else ""
        lines.append(
            f"  {row.name[:21]:<22}{row.kind:<14}{row.tokens:>8}{row.pct:>6.1f}%"
            f"{row.usd_cold:>10.5f}{row.usd_cached:>10.5f}{flag}"
        )
    lines.append("")
    lines.append(f"  total $ (cold, uncached upper bound): {report.total_usd_cold:.5f}")
    if report.cached:
        lines.append(f"  total $ (measured, blended cache):    {report.total_usd_effective:.5f}")
    if report.dead_tools:
        names = ", ".join(report.dead_tools)
        lines.append(
            f"  dead tools (shipped, never called): {names} "
            f"— ${report.wasted_usd_cold:.5f} wasted every request"
        )
    else:
        lines.append("  dead tools: none")
    return "\n".join(lines)


def _pct(a: float, b: float) -> str:
    if a == 0:
        return "n/a"
    return f"{(b - a) / a * 100:+.0f}%"


def format_diff(diff: ReportDiff) -> str:
    delta = diff.total_usd_cold_b - diff.total_usd_cold_a
    lines = [
        f"compare (A -> B)   model: {diff.model}",
        (
            f"  total $ (cold): {diff.total_usd_cold_a:.5f} -> {diff.total_usd_cold_b:.5f}"
            f"   ({delta:+.5f}, {_pct(diff.total_usd_cold_a, diff.total_usd_cold_b)})"
        ),
        "",
        f"  {'component':<22}{'Δ tokens':>10}{'Δ $ cold':>12}  note",
    ]
    for row in diff.rows:
        if row.delta_tokens == 0 and abs(row.delta_usd_cold) < 1e-9:
            continue
        lines.append(
            f"  {row.name[:21]:<22}{row.delta_tokens:>+10}{row.delta_usd_cold:>+12.5f}  {row.status}"
        )
    if diff.dead_tools_a != diff.dead_tools_b:
        lines.append("")
        lines.append(
            f"  dead tools: {diff.dead_tools_a or 'none'} -> {diff.dead_tools_b or 'none'}"
        )
    return "\n".join(lines)


def _cmd_compare(args: argparse.Namespace) -> int:
    payload_a = json.loads(Path(args.before).read_text(encoding="utf-8"))
    payload_b = json.loads(Path(args.after).read_text(encoding="utf-8"))
    diff = compare_payloads(payload_a, payload_b, model_override=args.model)
    print(format_diff(diff))
    return 0


def format_audit(report: AuditReport, min_calls: int = 1) -> str:
    lines = [
        f"mcp-audit   model: {report.model}   window: {report.window_api_calls} model calls",
        "",
        f"  {'server':<26}{'tools':>6}{'tok/req':>9}{'calls':>7}{'$/req cold':>12}",
    ]
    for server in report.servers:
        lines.append(
            f"  {server.server[:25]:<26}{server.tool_count:>6}{server.tokens_per_request:>9}"
            f"{server.calls:>7}{server.usd_per_request_cold:>12.5f}"
        )
    unused = [s for s in report.servers if s.calls < min_calls]
    if unused:
        lines.append("")
        for server in unused:
            lines.append(
                f"  {server.server}: {server.tokens_per_request} tok/req, {server.calls} calls "
                f"in {report.window_api_calls} model calls — ${server.usd_per_request_cold:.5f} "
                f"shipped every request"
            )
        lines.append(
            f"  [window: {report.window_api_calls} model calls — a rarely used tool can look "
            f"unused in a short window]"
        )
    return "\n".join(lines)


def _cmd_capture(args: argparse.Namespace) -> int:
    server = serve(args.port, args.out, args.upstream_host, args.upstream_port, not args.no_tls)
    print(f"ctxprofile capture on http://127.0.0.1:{args.port} -> {args.upstream_host}")
    print(f"  point your client at it:  ANTHROPIC_BASE_URL=http://127.0.0.1:{args.port}")
    print(f"  captures -> {args.out}   (streaming responses are stored raw)")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.shutdown()
    return 0


def _cmd_mcp_audit(args: argparse.Namespace) -> int:
    defs = json.loads(Path(args.defs).read_text(encoding="utf-8"))
    request = defs.get("request", defs)
    traces = [parse_trace_file(path) for path in args.traces]
    report = audit(request, traces, model_override=args.model)
    print(format_audit(report, min_calls=args.min_calls))
    return 0


def _cmd_lock(args: argparse.Namespace) -> int:
    payload = json.loads(Path(args.from_capture).read_text(encoding="utf-8"))
    request = payload.get("request", payload)
    lock = build_lock(request, args.model or request.get("model"))
    write_lock(lock, args.output)
    print(
        f"wrote {args.output}: {lock['static_input_tokens']} static tokens, "
        f"{len(lock['tools'])} tools, {len(lock['servers'])} servers"
    )
    return 0


def _cmd_ci(args: argparse.Namespace) -> int:
    if not args.budget and not args.lock:
        print("ci needs --budget and/or --lock")
        return 2
    budget = load_budget(args.budget) if args.budget else None
    lock = load_lock(args.lock) if args.lock else None
    failures = 0
    for path in args.captures:
        payload = json.loads(Path(path).read_text(encoding="utf-8"))
        request = payload.get("request", payload)
        report = analyze(payload, model_override=budget.model if budget else None)
        violations: list[str] = []
        if budget:
            violations += check(report, budget)
        if lock:
            violations += diff_lock(
                static_summary(request), lock, args.max_regression, not args.allow_new_tools
            )
        if violations:
            failures += 1
            print(f"FAIL {path}")
            for violation in violations:
                print(f"  - {violation}")
        else:
            print(f"ok   {path}")
    return 1 if failures else 0


def _cmd_analyze(args: argparse.Namespace) -> int:
    payload = json.loads(Path(args.payload).read_text(encoding="utf-8"))
    report = analyze(payload, model_override=args.model)
    if args.json:
        print(json.dumps(_report_dict(report), ensure_ascii=False, indent=2))
    else:
        print(format_report(report))
    return 0


def _report_dict(report: CostReport) -> dict[str, Any]:
    return {
        "model": report.model,
        "total_tokens": report.total_tokens,
        "reconciled": report.reconciled,
        "cached": report.cached,
        "total_usd_cold": report.total_usd_cold,
        "total_usd_effective": report.total_usd_effective,
        "dead_tools": report.dead_tools,
        "wasted_usd_cold": report.wasted_usd_cold,
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
            }
            for r in report.components
        ],
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="ctxprofile")
    sub = parser.add_subparsers(dest="command", required=True)
    an = sub.add_parser("analyze", help="cost-attribute one captured request")
    an.add_argument("payload", help="path to a captured request JSON (or {request, response})")
    an.add_argument("--model", help="override the model id")
    an.add_argument("--json", action="store_true", help="emit JSON instead of a table")
    an.set_defaults(func=_cmd_analyze)

    cmp = sub.add_parser("compare", help="show the token/$ delta between two captured requests")
    cmp.add_argument("before", help="path to the baseline capture JSON")
    cmp.add_argument("after", help="path to the changed capture JSON")
    cmp.add_argument("--model", help="override the model id")
    cmp.set_defaults(func=_cmd_compare)

    ci = sub.add_parser("ci", help="fail (exit 1) when a capture breaches a budget or lock")
    ci.add_argument("captures", nargs="+", help="capture JSON files to check")
    ci.add_argument("--budget", help="path to a TOML budget file")
    ci.add_argument("--lock", help="path to a .ctxprofile.lock to check for regressions")
    ci.add_argument(
        "--max-regression",
        type=int,
        default=0,
        dest="max_regression",
        help="tokens the static floor may rise before failing",
    )
    ci.add_argument(
        "--allow-new-tools", action="store_true", help="do not fail when a new tool appears"
    )
    ci.set_defaults(func=_cmd_ci)

    lk = sub.add_parser("lock", help="write a static-context-floor lock file")
    lk.add_argument("--from", dest="from_capture", required=True, help="reference capture JSON")
    lk.add_argument("-o", "--output", default=".ctxprofile.lock", help="lock file path")
    lk.add_argument("--model", help="model id to record")
    lk.set_defaults(func=_cmd_lock)

    ma = sub.add_parser("mcp-audit", help="dead-tool cost by MCP server over a trace corpus")
    ma.add_argument("--defs", required=True, help="a raw request that declares the tools")
    ma.add_argument("--traces", nargs="+", required=True, help="SDK / claude -p JSONL trace files")
    ma.add_argument(
        "--min-calls",
        type=int,
        default=1,
        dest="min_calls",
        help="a server with fewer calls than this is flagged as unused",
    )
    ma.add_argument("--model", help="override the model id")
    ma.set_defaults(func=_cmd_mcp_audit)

    cap = sub.add_parser("capture", help="forward-proxy that tees /v1/messages to capture files")
    cap.add_argument("--port", type=int, default=8787)
    cap.add_argument("--out", default="captures", help="directory for capture files")
    cap.add_argument("--upstream-host", default="api.anthropic.com", dest="upstream_host")
    cap.add_argument("--upstream-port", type=int, default=443, dest="upstream_port")
    cap.add_argument(
        "--no-tls", action="store_true", dest="no_tls", help="plain HTTP upstream (for testing)"
    )
    cap.set_defaults(func=_cmd_capture)
    return parser


def main(argv: list[str] | None = None) -> int:
    if hasattr(sys.stdout, "reconfigure"):
        try:
            sys.stdout.reconfigure(encoding="utf-8")
        except (ValueError, OSError):
            pass
    args = build_parser().parse_args(argv)
    return int(args.func(args))


if __name__ == "__main__":
    raise SystemExit(main())
