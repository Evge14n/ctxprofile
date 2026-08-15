"""Render captured terminal output as a self-contained SVG.

The image at the top of the README has to be generated from a real run, not drawn,
or it becomes another claim nobody can check. This takes the actual bytes a command
printed and lays them out as text — no screenshots, no external fonts, no network.

    ctxprofile demo | python tools/render_terminal_svg.py -o docs/demo-dark.svg --theme dark
"""

from __future__ import annotations

import argparse
import html
import sys
from pathlib import Path

THEMES = {
    "dark": {
        "bg": "#0d1117",
        "chrome": "#161b22",
        "fg": "#c9d1d9",
        "dim": "#8b949e",
        "accent": "#58a6ff",
        "warn": "#d29922",
        "strong": "#e6edf3",
    },
    "light": {
        "bg": "#ffffff",
        "chrome": "#f6f8fa",
        "fg": "#1f2328",
        "dim": "#656d76",
        "accent": "#0969da",
        "warn": "#9a6700",
        "strong": "#010409",
    },
}

CHAR_WIDTH = 7.22
LINE_HEIGHT = 19.0
PADDING = 18.0
CHROME = 34.0
FONT = (
    "ui-monospace, SFMono-Regular, 'SF Mono', Menlo, Consolas, "
    "'Liberation Mono', monospace"
)


def classify(line: str) -> str:
    """Colour carries meaning here or it is noise, so only four cases get one."""
    stripped = line.strip()
    if "[UNUSED]" in line or "not called in this capture" in line:
        return "warn"
    if stripped.startswith(("total $", "model:")):
        return "strong"
    if stripped.startswith(("component", "*", "...", "(tool block", "on the rows")):
        return "dim"
    return "fg"


def render(lines: list[str], theme: str, title: str) -> str:
    colors = THEMES[theme]
    width = max((len(line) for line in lines), default=40)
    px_width = round(width * CHAR_WIDTH + PADDING * 2)
    px_height = round(len(lines) * LINE_HEIGHT + PADDING * 2 + CHROME)

    out = [
        (
            f'<svg xmlns="http://www.w3.org/2000/svg" width="{px_width}" height="{px_height}" '
            f'viewBox="0 0 {px_width} {px_height}" font-family="{FONT}" font-size="13">'
        ),
        f'<rect width="{px_width}" height="{px_height}" rx="8" fill="{colors["bg"]}"/>',
        (
            f'<path d="M0 8a8 8 0 0 1 8-8h{px_width - 16}a8 8 0 0 1 8 8v{CHROME - 8:.0f}H0z" '
            f'fill="{colors["chrome"]}"/>'
        ),
    ]
    for index, dot in enumerate(("#ff5f57", "#febc2e", "#28c840")):
        out.append(f'<circle cx="{18 + index * 18}" cy="17" r="6" fill="{dot}"/>')
    out.append(
        f'<text x="{px_width / 2:.0f}" y="21" text-anchor="middle" '
        f'fill="{colors["dim"]}" font-size="12">{html.escape(title)}</text>'
    )

    for index, line in enumerate(lines):
        if not line.strip():
            continue
        y = PADDING + CHROME + index * LINE_HEIGHT + 12
        fill = colors[classify(line)]
        # xml:space keeps the column alignment the table depends on.
        out.append(
            f'<text x="{PADDING:.0f}" y="{y:.0f}" fill="{fill}" '
            f'xml:space="preserve">{html.escape(line)}</text>'
        )
    out.append("</svg>")
    return "\n".join(out)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("input", nargs="?", help="file of terminal output; stdin if omitted")
    parser.add_argument("-o", "--output", required=True)
    parser.add_argument("--theme", choices=sorted(THEMES), default="dark")
    parser.add_argument("--title", default="ctxprofile demo")
    args = parser.parse_args()

    text = Path(args.input).read_text(encoding="utf-8") if args.input else sys.stdin.read()
    lines = text.rstrip("\n").split("\n")
    Path(args.output).write_text(render(lines, args.theme, args.title) + "\n", encoding="utf-8")
    print(f"wrote {args.output}: {len(lines)} lines, {args.theme}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
