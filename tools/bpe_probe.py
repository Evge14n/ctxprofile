"""Measure how JSON structure and English prose compress under real BPE tokenizers.

The anatomy survey splits a tool definition into language and the JSON scaffolding
around it, and converts that split to tokens with a flat ~4 chars/token estimate. The
obvious objection is that one ratio cannot be right for both kinds of text.

Claude's tokenizer is not public, so this cannot answer what Claude counts. It can
answer the question the objection actually rests on — does repetitive JSON compress
*better* than prose, and by enough to change the conclusion — because that is a
property of BPE tokenizers, and several are public. Four are used here so the answer
does not depend on one vocabulary.

Needs `pip install tiktoken`, which is why this lives in tools/ and not in the package.

    python tools/bpe_probe.py examples/mcp-survey.json
"""

from __future__ import annotations

import argparse
import json
import statistics
from pathlib import Path
from typing import Any

import tiktoken

PROSE_KEYS = ("description", "title")
ENCODINGS = ("cl100k_base", "o200k_base", "p50k_base", "r50k_base")


def split(node: Any, prose: list[str]) -> Any:
    """Blank every prose value, collecting it, so what is left is pure scaffolding."""
    if isinstance(node, dict):
        clean = {}
        for key, value in node.items():
            if key in PROSE_KEYS and isinstance(value, str):
                prose.append(value)
                clean[key] = ""
            else:
                clean[key] = split(value, prose)
        return clean
    if isinstance(node, list):
        return [split(item, prose) for item in node]
    return node


def corpora(tools: list[dict[str, Any]]) -> tuple[str, str]:
    prose: list[str] = []
    structure: list[str] = []
    for tool in tools:
        if tool.get("description"):
            prose.append(str(tool["description"]))
        skeleton = split(tool.get("input_schema") or {}, prose)
        structure.append(json.dumps(skeleton, ensure_ascii=False))
    return "\n".join(prose), "\n".join(structure)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("request", help="a request JSON whose tools[] holds the definitions")
    parser.add_argument(
        "--flip-at",
        type=float,
        default=2.28,
        help="the ratio at which the survey's conclusion would reverse",
    )
    args = parser.parse_args()

    tools = json.loads(Path(args.request).read_text(encoding="utf-8")).get("tools") or []
    if not tools:
        print("no tool definitions in that request")
        return 1

    prose_text, structure_text = corpora(tools)
    print(f"prose {len(prose_text):,} chars   structure {len(structure_text):,} chars")
    print()
    print(f"  {'encoding':<14}{'prose c/t':>11}{'structure c/t':>15}{'ratio':>8}")

    ratios = []
    shares = []
    for name in ENCODINGS:
        enc = tiktoken.get_encoding(name)
        prose_tokens = len(enc.encode(prose_text))
        structure_tokens = len(enc.encode(structure_text))
        prose_cpt = len(prose_text) / prose_tokens
        structure_cpt = len(structure_text) / structure_tokens
        ratios.append(structure_cpt / prose_cpt)
        shares.append(structure_tokens / (structure_tokens + prose_tokens))
        print(
            f"  {name:<14}{prose_cpt:>11.2f}{structure_cpt:>15.2f}"
            f"{structure_cpt / prose_cpt:>8.2f}x"
        )

    print()
    print(
        f"  structure's share of tool-definition tokens: "
        f"{min(shares):.0%}-{max(shares):.0%} (the ~4 chars/token estimate says 69%)"
    )
    print(
        f"  ratio {min(ratios):.2f}x-{max(ratios):.2f}x — above 1.0 would mean JSON compresses"
    )
    print(f"  better than prose; {args.flip_at:.2f}x is what would overturn the survey.")

    per_tool = []
    for tool in tools:
        pieces: list[str] = [str(tool["description"])] if tool.get("description") else []
        skeleton = json.dumps(split(tool.get("input_schema") or {}, pieces), ensure_ascii=False)
        joined = " ".join(pieces)
        if len(joined) < 50 or len(skeleton) < 50:
            continue
        enc = tiktoken.get_encoding(ENCODINGS[0])
        per_tool.append(
            (len(skeleton) / len(enc.encode(skeleton))) / (len(joined) / len(enc.encode(joined)))
        )

    if per_tool:
        print()
        beats = sum(1 for r in per_tool if r > args.flip_at)
        print(
            f"  per tool ({len(per_tool)} with enough of both, {ENCODINGS[0]}): "
            f"median {statistics.median(per_tool):.2f}x, max {max(per_tool):.2f}x"
        )
        print(f"  tools that would need to exceed {args.flip_at:.2f}x to flip: {beats}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
