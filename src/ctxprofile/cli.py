from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from importlib import resources
from pathlib import Path

from ctxprofile import __version__
from ctxprofile.budget import check, load_budget
from ctxprofile.capture import serve
from ctxprofile.churn import DEFAULT_TTL_SECONDS, ChurnReport, analyze_churn
from ctxprofile.compare import compare_payloads
from ctxprofile.cost import TOOL_BLOCK_OVERHEAD, analyze
from ctxprofile.ingest_sdk import parse_trace_file
from ctxprofile.lint import LintReport, lint_tools, slim_tools
from ctxprofile.lockfile import build_lock, diff_lock, load_lock, static_summary, write_lock
from ctxprofile.mcp_audit import AuditReport, audit
from ctxprofile.models import STATIC_KINDS, CostReport, ReportDiff
from ctxprofile.online import TOOL_BLOCK_CALLS, default_counter, tool_block_counter
from ctxprofile.pricing import usd
from ctxprofile.serialize import churn_to_dict, lint_to_dict, report_to_dict

DEFAULT_LINT_TOP = 20


def _fit(name: str, width: int) -> str:
    """Trim a name to width, keeping both ends.

    MCP names are `mcp__server__tool`, so cutting the tail throws away the only part
    that identifies the tool. Taking it out of the middle keeps both the server and
    the tool readable.
    """
    if len(name) <= width:
        return name
    head = (width - 1) // 2
    return f"{name[:head]}…{name[len(name) - (width - head - 1):]}"


def format_report(report: CostReport, top: int | None = None) -> str:
    if report.reconciled:
        basis = "exact $ (reconciled to usage)"
    elif report.usage_seen:
        basis = "usage block inconsistent with this request — not reconciled"
    elif report.measured_calls:
        basis = "system + tools measured, rest estimated (no usage block)"
    else:
        basis = "estimated (no usage block)"
    lines = [
        f"model: {report.model}   input tokens: {report.total_tokens}   {basis}",
        "",
        f"  {'component':<31}{'kind':<14}{'tokens':>8}{'%':>7}{'$ cold':>10}{'$ cached':>10}",
    ]
    shown = report.components[:top] if top else report.components
    for row in shown:
        flag = "  [UNUSED]" if row.unused else ""
        mark = "*" if row.exact else " "
        lines.append(
            f"  {_fit(row.name, 30):<31}{row.kind:<14}{row.tokens:>7}{mark}{row.pct:>6.1f}%"
            f"{row.usd_cold:>10.5f}{row.usd_cached:>10.5f}{flag}"
        )
    hidden = len(report.components) - len(shown)
    if hidden:
        rest = sum(r.tokens for r in report.components[len(shown):])
        lines.append(f"  ... and {hidden} more components totalling {rest:,} tokens")
    lines.append("")
    if report.measured_calls:
        lines.append(
            f"  * tokens measured with count_tokens ({report.measured_calls} calls); "
            f"unmarked rows are the ~4 chars/token estimate"
        )
        if any(row.name == TOOL_BLOCK_OVERHEAD for row in report.components):
            lines.append(
                f"  {TOOL_BLOCK_OVERHEAD} is what having any tools at all costs; "
                f"removing one tool does not recover it"
            )
        if report.estimator_error is not None:
            direction = "above" if report.estimator_error > 0 else "below"
            lines.append(
                f"  on the rows it could check, the estimate ran {abs(report.estimator_error):.0%} "
                f"{direction} the measured value — the unmarked rows carry that kind of error"
            )
    lines.append(f"  total $ (cold, uncached upper bound): {report.total_usd_cold:.5f}")
    if report.cached:
        lines.append(f"  total $ (measured, blended cache):    {report.total_usd_effective:.5f}")
    if report.dead_tools:
        # A request carrying an MCP server has dozens of these; listing them all turns
        # the headline number into a wall of names nobody reads.
        shown_names = report.dead_tools[:3]
        names = ", ".join(shown_names)
        if len(report.dead_tools) > len(shown_names):
            names += f" and {len(report.dead_tools) - len(shown_names)} more"
        lines.append(
            f"  {len(report.dead_tools)} tools not called in this capture "
            f"(${report.wasted_usd_cold:.5f} at the cold rate if they are never called):"
        )
        lines.append(f"    {names}")
    else:
        lines.append("  tools not called in this capture: none")
    return "\n".join(lines)


def _pct(a: float, b: float) -> str:
    if a == 0:
        return "n/a"
    return f"{(b - a) / a * 100:+.0f}%"


def format_diff(diff: ReportDiff, monthly_requests: int | None = None) -> str:
    delta = diff.total_usd_cold_b - diff.total_usd_cold_a
    static_delta = sum(r.delta_usd_cold for r in diff.rows if r.kind in STATIC_KINDS)
    dynamic_delta = sum(r.delta_usd_cold for r in diff.rows if r.kind not in STATIC_KINDS)
    lines = [
        f"compare (A -> B)   model: {diff.model}",
        (
            f"  total $ (cold): {diff.total_usd_cold_a:.5f} -> {diff.total_usd_cold_b:.5f}"
            f"   ({delta:+.5f}, {_pct(diff.total_usd_cold_a, diff.total_usd_cold_b)})"
        ),
    ]
    if monthly_requests:
        lines.append(f"  at {monthly_requests:,} requests/mo: {delta * monthly_requests:+,.2f} $/mo")
    lines += ["", f"  {'component':<22}{'Δ tokens':>10}{'Δ $ cold':>12}  note"]
    for row in diff.rows:
        if row.delta_tokens == 0 and abs(row.delta_usd_cold) < 1e-9:
            continue
        lines.append(
            f"  {row.name[:21]:<22}{row.delta_tokens:>+10}{row.delta_usd_cold:>+12.5f}  {row.status}"
        )
    if abs(dynamic_delta) > 1e-9:
        lines.append("")
        lines.append(
            f"  note: {dynamic_delta:+.5f} of this delta is dynamic (history/tool_result), which "
            f"moves per request; the static (system + tools) delta is {static_delta:+.5f}"
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
    print(format_diff(diff, monthly_requests=args.monthly_requests))
    return 0


def format_audit(report: AuditReport, min_calls: int = 1) -> str:
    basis = (
        f"   tokens: measured ({report.measured_calls} count_tokens calls)"
        if report.measured_calls
        else ""
    )
    lines = [
        f"mcp-audit   model: {report.model}   window: {report.window_api_calls} model calls{basis}",
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


def format_lint(report: LintReport, model: str, top: int = DEFAULT_LINT_TOP) -> str:
    basis = f"measured ({TOOL_BLOCK_CALLS} count_tokens calls)" if report.exact else "estimated"
    lines = [f"lint   model: {model}   tool definitions: {report.baseline_tokens} tok ({basis})", ""]
    if not report.findings:
        lines.append("  no removable weight found")
        return "\n".join(lines)
    shown = report.findings[:top] if top > 0 else report.findings
    lines.append(f"  {'tool':<32}{'check':<20}{'tok':>7}  what")
    for finding in shown:
        lines.append(
            f"  {_fit(finding.tool, 31):<32}{finding.check:<20}{finding.tokens:>7}"
            f"  {_fit(finding.detail, 40)}"
        )
    hidden = len(report.findings) - len(shown)
    if hidden:
        lines.append(f"  ... and {hidden} more (--top 0 for all, --json for the full list)")
    by_check: Counter[str] = Counter(finding.check for finding in report.findings)
    rollup = ", ".join(f"{check} in {count} tools" for check, count in by_check.most_common())
    saved = usd(report.removable_tokens, model)
    lines += [
        "",
        f"  {rollup}",
        (
            f"  removable: {report.removable_tokens} tok of {report.baseline_tokens} "
            f"({report.removable_pct:.1f}%) — ${saved:.5f} per request, cold"
        ),
        "  nothing a tool call can reference: schema metadata and definitions no $ref reaches",
    ]
    if not report.exact:
        lines.append("  [per-finding tokens are individual deltas; they need not sum to the total]")
    return "\n".join(lines)


def _humanize(seconds: float | None) -> str:
    if seconds is None:
        return ""
    if seconds < 90:
        return f"{seconds:.0f}s gap"
    if seconds < 5400:
        return f"{seconds / 60:.0f}m gap"
    return f"{seconds / 3600:.1f}h gap"


def format_churn(report: ChurnReport) -> str:
    ttl = f"{report.ttl_seconds / 60:.0f}m"
    lines = [
        (
            f"churn   model: {report.model or '?'}   {report.captures} captures   "
            f"{len(report.events)} rebuilds   ttl assumed {ttl}"
        )
    ]
    if report.captures < 2:
        lines.append("")
        lines.append(
            "  needs at least two captures with a captured_at stamp and a usage block; "
            "the built-in proxy writes both"
        )
        return "\n".join(lines)
    if not report.events:
        lines.append("")
        lines.append("  no cache rebuilds in this window — the prefix held")
        return "\n".join(lines)

    lines += ["", f"  {'when':<21}{'cause':<34}{'rebuilt':>9}{'excess $':>11}"]
    for event in report.events:
        label = f"{event.cause} ({event.kind})" if event.kind else event.cause
        note = _humanize(event.gap_seconds)
        if event.cause in ("ttl expiry", "unexplained") and note:
            label = f"{event.cause}, {note}"
        # Seconds are enough to line the events up; microseconds only cost column width.
        lines.append(
            f"  {event.at[:19]:<21}{label[:33]:<34}{event.rebuilt_tokens:>9}{event.excess_usd:>11.5f}"
        )

    lines.append("")
    lines.append(f"  total excess over cache reads: {report.total_excess_usd:.5f}")
    worst: Counter[str] = Counter()
    for event in report.events:
        worst[event.cause] += 1
    cause, count = worst.most_common(1)[0]
    if count > 1:
        spent = sum(e.excess_usd for e in report.events if e.cause == cause)
        lines.append(f"  most frequent cause: {cause} ({count} rebuilds, {spent:.5f})")
    lines.append(
        "  captures are compared in timestamp order and assumed to be one lineage; "
        "an unexplained rebuild is one this data cannot account for"
    )
    return "\n".join(lines)


def _cmd_churn(args: argparse.Namespace) -> int:
    captures = [json.loads(Path(path).read_text(encoding="utf-8")) for path in args.captures]
    report = analyze_churn(captures, ttl_seconds=args.cache_ttl)
    if args.json:
        print(json.dumps(churn_to_dict(report), ensure_ascii=False, indent=2))
    else:
        print(format_churn(report))
    return 0


def _cmd_demo(args: argparse.Namespace) -> int:
    """Run the tool on a bundled sample so a fresh install has something to show."""
    sample = resources.files("ctxprofile") / "data" / "demo.json"
    payload = json.loads(sample.read_text(encoding="utf-8"))
    print("sample: four public MCP servers, as their tool schemas were actually published")
    print()
    print(format_report(analyze(payload, model_override=args.model), top=8))
    print()
    print("that is what those servers cost on every request, before anyone types a word.")
    lint = lint_tools(payload.get("tools") or [])
    if lint.removable_tokens:
        print(
            f"{lint.removable_tokens} of those tokens ({lint.removable_pct:.0f}%) are schema "
            f"no tool call can reference — ctxprofile lint says which"
        )
    print("point it at your own: ctxprofile capture --out captures/   then analyze a capture")
    return 0


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
    report = audit(
        request,
        traces,
        model_override=args.model,
        counter=default_counter() if args.online else None,
        max_calls=args.online_max_calls,
    )
    print(format_audit(report, min_calls=args.min_calls))
    return 0


def _cmd_lint(args: argparse.Namespace) -> int:
    payload = json.loads(Path(args.payload).read_text(encoding="utf-8"))
    request = payload.get("request", payload)
    tools = request.get("tools") or []
    model = args.model or request.get("model")
    if not model:
        raise ValueError("model not found in request; pass --model")
    measure = None
    if args.online:
        if TOOL_BLOCK_CALLS > args.online_max_calls:
            raise ValueError(
                f"--online needs {TOOL_BLOCK_CALLS} count_tokens calls, over the "
                f"--online-max-calls limit of {args.online_max_calls}"
            )
        measure = tool_block_counter(default_counter(), model)
    report = lint_tools(tools, measure=measure)
    if args.write_slim:
        slim = {**request, "tools": slim_tools(tools)}
        Path(args.write_slim).write_text(
            json.dumps(slim, ensure_ascii=False, indent=2), encoding="utf-8"
        )
    if args.json:
        print(json.dumps(lint_to_dict(report, model), ensure_ascii=False, indent=2))
    else:
        print(format_lint(report, model, top=args.top))
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


def _ci_github(results: list[tuple[str, list[str]]], failures: int) -> str:
    lines = ["### ctxprofile"]
    if not failures:
        lines.append(f"\n✅ all {len(results)} capture(s) within budget.")
        return "\n".join(lines)
    for path, violations in results:
        if violations:
            lines.append(f"\n**❌ `{path}`**")
            lines += [f"- {v}" for v in violations]
        else:
            lines.append(f"\n✅ `{path}`")
    return "\n".join(lines)


def _cmd_ci(args: argparse.Namespace) -> int:
    if not args.budget and not args.lock:
        print("ci needs --budget and/or --lock")
        return 2
    budget = load_budget(args.budget) if args.budget else None
    lock = load_lock(args.lock) if args.lock else None
    results: list[tuple[str, list[str]]] = []
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
        results.append((path, violations))
    failures = sum(1 for _, violations in results if violations)
    if args.format == "github":
        print(_ci_github(results, failures))
    else:
        for path, violations in results:
            if violations:
                print(f"FAIL {path}")
                for violation in violations:
                    print(f"  - {violation}")
            else:
                print(f"ok   {path}")
    return 1 if failures else 0


def _cmd_analyze(args: argparse.Namespace) -> int:
    payload = json.loads(Path(args.payload).read_text(encoding="utf-8"))
    counter = default_counter() if args.online else None
    report = analyze(
        payload,
        model_override=args.model,
        counter=counter,
        max_calls=args.online_max_calls,
    )
    if args.json:
        print(json.dumps(report_to_dict(report), ensure_ascii=False, indent=2))
    else:
        print(format_report(report))
    return 0


def _add_online_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--online",
        action="store_true",
        help="measure tokens with count_tokens instead of estimating them "
        '(needs an API key and pip install "ctxprofile[online]")',
    )
    parser.add_argument(
        "--online-max-calls",
        type=int,
        default=64,
        dest="online_max_calls",
        help="refuse to run --online if it would need more count_tokens calls than this",
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="ctxprofile")
    parser.add_argument("--version", action="version", version=f"ctxprofile {__version__}")
    sub = parser.add_subparsers(dest="command", required=True)
    an = sub.add_parser("analyze", help="cost-attribute one captured request")
    an.add_argument("payload", help="path to a captured request JSON (or {request, response})")
    an.add_argument("--model", help="override the model id")
    an.add_argument("--json", action="store_true", help="emit JSON instead of a table")
    an.add_argument(
        "--top", type=int, help="show only the N priciest components (all of them by default)"
    )
    _add_online_args(an)
    an.set_defaults(func=_cmd_analyze)

    cmp = sub.add_parser("compare", help="show the token/$ delta between two captured requests")
    cmp.add_argument("before", help="path to the baseline capture JSON")
    cmp.add_argument("after", help="path to the changed capture JSON")
    cmp.add_argument("--model", help="override the model id")
    cmp.add_argument(
        "--monthly-requests",
        type=int,
        dest="monthly_requests",
        help="project the $ delta to this monthly request volume",
    )
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
    ci.add_argument(
        "--format", choices=["text", "github"], default="text", help="output format"
    )
    ci.set_defaults(func=_cmd_ci)

    lt = sub.add_parser("lint", help="find tool-schema weight no tool call can reference")
    lt.add_argument("payload", help="path to a captured request JSON (or {request, response})")
    lt.add_argument("--model", help="override the model id")
    lt.add_argument("--json", action="store_true", help="emit JSON instead of a table")
    lt.add_argument(
        "--top",
        type=int,
        default=DEFAULT_LINT_TOP,
        help=f"show only the N biggest findings (default {DEFAULT_LINT_TOP}, 0 for all)",
    )
    lt.add_argument(
        "--write-slim",
        dest="write_slim",
        metavar="PATH",
        help="write the request with the removable weight stripped, to check the saving",
    )
    _add_online_args(lt)
    lt.set_defaults(func=_cmd_lint)

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
    _add_online_args(ma)
    ma.set_defaults(func=_cmd_mcp_audit)

    ch = sub.add_parser("churn", help="attribute cache rebuilds to what invalidated the prefix")
    ch.add_argument("captures", nargs="+", help="capture JSON files from the same run")
    ch.add_argument(
        "--cache-ttl",
        type=float,
        default=DEFAULT_TTL_SECONDS,
        dest="cache_ttl",
        help="cache lifetime in seconds; a rebuild after longer with nothing changed is an expiry",
    )
    ch.add_argument("--json", action="store_true", help="emit JSON instead of a table")
    ch.set_defaults(func=_cmd_churn)

    dm = sub.add_parser("demo", help="run on a bundled sample of four real MCP servers")
    dm.add_argument("--model", help="override the model id")
    dm.set_defaults(func=_cmd_demo)

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


def _force_utf8(stream: object) -> None:
    """Windows consoles still default to cp437/cp1252, which cannot encode `—` or `…`."""
    reconfigure = getattr(stream, "reconfigure", None)
    if reconfigure is None:
        return
    try:
        reconfigure(encoding="utf-8")
    except (ValueError, OSError):
        pass


def main(argv: list[str] | None = None) -> int:
    _force_utf8(sys.stdout)
    _force_utf8(sys.stderr)
    args = build_parser().parse_args(argv)
    try:
        return int(args.func(args))
    except (
        AttributeError,
        FileNotFoundError,
        KeyError,
        ValueError,
        TypeError,
        RuntimeError,
        json.JSONDecodeError,
    ) as error:
        message = error.args[0] if error.args else error
        print(f"error: {message}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
