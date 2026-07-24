from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

from ctxprofile.cost import analyze
from ctxprofile.models import CostReport


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
    lines.append(f"  total $ (cold input): {report.total_usd_cold:.5f}")
    if report.dead_tools:
        names = ", ".join(report.dead_tools)
        lines.append(
            f"  dead tools (shipped, never called): {names} "
            f"— ${report.wasted_usd_cold:.5f} wasted every request"
        )
    else:
        lines.append("  dead tools: none")
    return "\n".join(lines)


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
        "total_usd_cold": report.total_usd_cold,
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
