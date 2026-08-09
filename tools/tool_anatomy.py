"""Break a corpus of tool definitions down by where its bytes and tokens sit.

`analyze` tells you what a tool definition costs. This asks what it is made of:
how much of it is language a model reads, and how much is JSON structure carrying
that language. The split is reported in characters first, because that needs no
tokenizer and no assumptions, and only then converted to tokens and dollars with
the same labelled estimator the rest of the tool uses.

    python tools/tool_anatomy.py examples/mcp-survey.json
"""

from __future__ import annotations

import argparse
import json
import math
import sys
from collections import defaultdict
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from ctxprofile.pricing import usd
from ctxprofile.tokenizer import CHARS_PER_TOKEN, estimate_tokens

PROSE_KEYS = ("description", "title")


def server_of(name: str) -> str:
    parts = name.split("__")
    return parts[1] if name.startswith("mcp__") and len(parts) >= 3 else "(local)"


def prose_chars(node: Any) -> int:
    """Characters of human-readable text anywhere inside a JSON schema."""
    if isinstance(node, dict):
        return sum(
            len(value) if key in PROSE_KEYS and isinstance(value, str) else prose_chars(value)
            for key, value in node.items()
        )
    if isinstance(node, list):
        return sum(prose_chars(item) for item in node)
    return 0


def anatomy(tool: dict[str, Any]) -> dict[str, Any]:
    description = str(tool.get("description") or "")
    schema = tool.get("input_schema") or {}
    schema_text = json.dumps(schema, ensure_ascii=False)
    inside = prose_chars(schema)
    return {
        "tool": str(tool.get("name", "?")),
        "server": server_of(str(tool.get("name", ""))),
        "prose_chars": len(description) + inside,
        "structure_chars": len(schema_text) - inside,
        "tokens": estimate_tokens(json.dumps(tool, ensure_ascii=False)),
        "description_tokens": estimate_tokens(description),
    }


def sensitivity(prose: int, structure: int) -> list[tuple[float, int, int]]:
    """Structure's share if JSON compresses better than prose does.

    The estimator assumes one ratio for everything. Repetitive JSON plausibly beats
    that, so the honest question is not what the split is under one assumption but
    how far the assumption has to bend before the answer flips.
    """
    prose_tokens = math.ceil(prose / CHARS_PER_TOKEN)
    rows = []
    for chars_per_token in (4.0, 6.0, 8.0, 10.0, 12.0):
        structure_tokens = math.ceil(structure / chars_per_token)
        rows.append((chars_per_token, structure_tokens, prose_tokens))
    return rows


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("request", help="a request JSON whose tools[] holds the definitions")
    parser.add_argument("--model", default="claude-opus-4-8")
    args = parser.parse_args()

    request = json.loads(Path(args.request).read_text(encoding="utf-8"))
    tools = request.get("tools") or []
    if not tools:
        print("no tool definitions in that request")
        return 1

    rows = [anatomy(tool) for tool in tools]
    tokens = sum(r["tokens"] for r in rows)
    prose = sum(r["prose_chars"] for r in rows)
    structure = sum(r["structure_chars"] for r in rows)

    by_server: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        by_server[row["server"]].append(row)

    print(
        f"{len(by_server)} servers   {len(rows)} tool definitions   ~{tokens:,} tokens   "
        f"${usd(tokens, args.model, 'input'):.4f} per request"
    )
    print(
        f"  that is {tokens / 200_000:.0%} of a 200K context window and "
        f"{tokens / 1_000_000:.0%} of a 1M one, spent before the user types"
    )
    print("(token counts are the ~4 chars/token estimate; the character counts below are exact)")

    print()
    print("  what a tool definition is made of, in characters")
    total_chars = prose + structure
    print(f"  {'language a model reads':<32}{prose:>10,}{prose / total_chars:>8.0%}")
    print(f"  {'JSON structure around it':<32}{structure:>10,}{structure / total_chars:>8.0%}")

    print()
    print("  if JSON compresses better than prose, structure's share of tokens becomes:")
    print(f"  {'JSON chars/token':>18}{'structure':>12}{'prose':>10}{'structure share':>18}")
    for chars_per_token, structure_tokens, prose_tokens in sensitivity(prose, structure):
        share = structure_tokens / (structure_tokens + prose_tokens)
        print(
            f"  {chars_per_token:>18.1f}{structure_tokens:>12,}{prose_tokens:>10,}{share:>18.0%}"
        )
    flip = structure / math.ceil(prose / CHARS_PER_TOKEN)
    print(
        f"  prose only overtakes structure past {flip:.1f} chars/token — JSON would have to be"
    )
    print(f"  {flip / CHARS_PER_TOKEN:.1f}x more compressible than English for that to happen.")

    print()
    print(f"  {'server':<24}{'tools':>6}{'tokens':>9}{'$/req':>9}{'tok/tool':>10}{'prose':>8}")
    ranked_servers = sorted(by_server.items(), key=lambda kv: -sum(r["tokens"] for r in kv[1]))
    for server, items in ranked_servers:
        server_tokens = sum(r["tokens"] for r in items)
        server_prose = sum(r["prose_chars"] for r in items)
        server_chars = server_prose + sum(r["structure_chars"] for r in items)
        print(
            f"  {server[:23]:<24}{len(items):>6}{server_tokens:>9,}"
            f"{usd(server_tokens, args.model, 'input'):>9.4f}"
            f"{server_tokens // len(items):>10}{server_prose / server_chars:>8.0%}"
        )

    biggest, share = ranked_servers[0]
    print()
    print(
        f"  {biggest} alone is {sum(r['tokens'] for r in share) / tokens:.0%} of the bill "
        f"across {len(share)} of {len(rows)} tools"
    )
    ranked = sorted(rows, key=lambda r: -r["tokens"])
    top = max(1, len(ranked) // 10)
    concentration = sum(r["tokens"] for r in ranked[:top]) / tokens
    print(f"  the {top} priciest definitions ({top / len(rows):.0%} of them) are {concentration:.0%} of it")
    median = sorted(r["tokens"] for r in rows)[len(rows) // 2]
    print(f"  median definition {median:,} tokens; largest {ranked[0]['tokens']:,} ({ranked[0]['tool']})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
