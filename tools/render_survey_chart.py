"""Draw the tool-definition bill as an SVG, straight from the committed corpus.

The figure in the survey has to come out of the same data the numbers do, or it is
a drawing of a claim rather than the claim itself. This reads a request, groups the
tool definitions by MCP server, and splits each bar into the part a tool call can
reference and the part it cannot.

    python tools/render_survey_chart.py examples/mcp-survey.json -o docs/survey-dark.svg --theme dark
"""

from __future__ import annotations

import argparse
import html
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from ctxprofile.lint import lint_tools, slim_tools
from ctxprofile.tokenizer import estimate_tokens

THEMES = {
    "dark": {
        "bg": "#0d1117",
        "grid": "#21262d",
        "fg": "#c9d1d9",
        "dim": "#8b949e",
        "strong": "#e6edf3",
        "bar": "#3f6b9e",
        "dead": "#d29922",
    },
    "light": {
        "bg": "#ffffff",
        "grid": "#d8dee4",
        "fg": "#1f2328",
        "dim": "#656d76",
        "strong": "#010409",
        "bar": "#7aa2cc",
        "dead": "#bf8700",
    },
}

FONT = "ui-monospace, SFMono-Regular, 'SF Mono', Menlo, Consolas, 'Liberation Mono', monospace"
WIDTH = 880.0
PAD = 20.0
CHAR_W = 7.22
ROW_H = 21.0
BAR_H = 13.0
HEADER_H = 74.0
LEGEND_H = 30.0


def server_of(name: str) -> str:
    return name.split("__")[1] if name.startswith("mcp__") and "__" in name[5:] else "(local)"


def group(tools: list[dict]) -> list[tuple[str, int, int, int]]:
    """(server, tool count, tokens, of which unreachable), biggest bill first."""
    lean = {id(tool): slim for tool, slim in zip(tools, slim_tools(tools), strict=True)}
    rows: dict[str, list[int]] = {}
    for tool in tools:
        row = rows.setdefault(server_of(str(tool.get("name", "<unnamed>"))), [0, 0, 0])
        full = estimate_tokens(json.dumps(tool, ensure_ascii=False))
        slim = estimate_tokens(json.dumps(lean[id(tool)], ensure_ascii=False))
        row[0] += 1
        row[1] += full
        row[2] += full - slim
    return sorted(
        ((name, count, tokens, dead) for name, (count, tokens, dead) in rows.items()),
        key=lambda item: -item[2],
    )


def value_label(count: int, tokens: int, unreachable: int) -> str:
    share = f"  {unreachable / tokens * 100:.0f}% unreachable" if unreachable else ""
    noun = "tool" if count == 1 else "tools"
    return f"{tokens:,} tok / {count} {noun}{share}"


def render(rows: list[tuple[str, int, int, int]], total: int, dead: int, theme: str) -> str:
    color = THEMES[theme]
    # The value column is sized from the widest label it has to hold, so a long one
    # pushes the bars in rather than running off the edge of the image.
    value_w = max(len(value_label(c, tk, d)) for _, c, tk, d in rows) * CHAR_W + 16
    label_w = max(len(name) for name, *_ in rows) * CHAR_W + 12
    plot_w = WIDTH - PAD * 2 - label_w - value_w
    scale = plot_w / max(tokens for _, _, tokens, _ in rows)
    height = round(HEADER_H + len(rows) * ROW_H + LEGEND_H + PAD)

    out = [
        (
            f'<svg xmlns="http://www.w3.org/2000/svg" width="{WIDTH:.0f}" '
            f'height="{height}" viewBox="0 0 {WIDTH:.0f} {height}" '
            f'font-family="{FONT}" font-size="12">'
        ),
        f'<rect width="{WIDTH:.0f}" height="{height}" rx="8" fill="{color["bg"]}"/>',
        (
            f'<text x="{PAD:.0f}" y="34" fill="{color["strong"]}" font-size="15">'
            f"{len(rows)} MCP servers, {sum(r[1] for r in rows)} tool definitions, "
            f"{total:,} tokens on every request</text>"
        ),
        (
            f'<text x="{PAD:.0f}" y="55" fill="{color["dim"]}">'
            f"{dead:,} of them ({dead / total * 100:.0f}%) are schema "
            f"no tool call can reference</text>"
        ),
    ]
    for index, (name, count, tokens, unreachable) in enumerate(rows):
        y = HEADER_H + index * ROW_H
        bar_x = PAD + label_w
        live_w = (tokens - unreachable) * scale
        dead_w = unreachable * scale
        out += [
            (
                f'<text x="{PAD:.0f}" y="{y + BAR_H - 2:.0f}" fill="{color["fg"]}">'
                f"{html.escape(name)}</text>"
            ),
            (
                f'<rect x="{bar_x:.1f}" y="{y:.0f}" width="{live_w:.1f}" '
                f'height="{BAR_H:.0f}" fill="{color["bar"]}"/>'
            ),
        ]
        if dead_w > 0:
            out.append(
                f'<rect x="{bar_x + live_w:.1f}" y="{y:.0f}" width="{dead_w:.1f}" '
                f'height="{BAR_H:.0f}" fill="{color["dead"]}"/>'
            )
        out.append(
            f'<text x="{bar_x + live_w + dead_w + 8:.1f}" y="{y + BAR_H - 2:.0f}" '
            f'fill="{color["dim"]}">{value_label(count, tokens, unreachable)}</text>'
        )

    legend_y = HEADER_H + len(rows) * ROW_H + 16
    out += [
        (
            f'<rect x="{PAD:.0f}" y="{legend_y - 9:.0f}" width="11" height="11" '
            f'fill="{color["bar"]}"/>'
        ),
        (
            f'<text x="{PAD + 18:.0f}" y="{legend_y:.0f}" fill="{color["dim"]}">'
            f"schema a tool call can reference</text>"
        ),
        (
            f'<rect x="{PAD + 246:.0f}" y="{legend_y - 9:.0f}" width="11" height="11" '
            f'fill="{color["dead"]}"/>'
        ),
        (
            f'<text x="{PAD + 264:.0f}" y="{legend_y:.0f}" fill="{color["dim"]}">'
            f"unreferenced $defs and schema metadata — nothing can point at it</text>"
        ),
        "</svg>",
    ]
    return "\n".join(out)


def widest_text(svg: str) -> float:
    """Where the rightmost glyph lands. Clipped labels are a silent failure otherwise."""
    widest = 0.0
    for match in re.finditer(r'<text x="([\d.]+)"([^>]*)>(.*?)</text>', svg, re.DOTALL):
        size = 15.0 if 'font-size="15"' in match.group(2) else 12.0
        text = re.sub("<[^>]+>", "", match.group(3))
        widest = max(widest, float(match.group(1)) + len(text) * size * 0.602)
    return widest


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("payload", help="a captured request carrying the tool definitions")
    parser.add_argument("-o", "--output", required=True)
    parser.add_argument("--theme", choices=sorted(THEMES), default="dark")
    args = parser.parse_args()

    payload = json.loads(Path(args.payload).read_text(encoding="utf-8"))
    tools = payload.get("request", payload).get("tools") or []
    if not tools:
        print("no tool definitions in that payload", file=sys.stderr)
        return 1
    rows = group(tools)
    report = lint_tools(tools)
    svg = render(rows, report.baseline_tokens, report.removable_tokens, args.theme)
    overflow = widest_text(svg) - WIDTH
    if overflow > 0:
        print(f"text runs {overflow:.0f}px past the edge; widen the image", file=sys.stderr)
        return 1
    Path(args.output).write_text(svg + "\n", encoding="utf-8")
    print(f"wrote {args.output}: {len(rows)} servers, {args.theme}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
