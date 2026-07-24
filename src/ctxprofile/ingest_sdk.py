from __future__ import annotations

import json
from collections import Counter
from collections.abc import Iterable, Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class Trace:
    """A normalized view of one Claude Agent SDK / `claude -p` run.

    This is the usage + call-graph source. It carries tool NAMES (from the init
    line) and call counts, not the tool schemas, so it is not an attribution
    source on its own — pair it with a raw request for token costs.
    """

    session_id: str | None
    model: str | None
    tools_declared: frozenset[str]
    tools_called: Counter[str]
    total_cost_usd: float | None
    api_calls: int


def iter_json_lines(text: str) -> Iterator[dict[str, Any]]:
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        try:
            obj = json.loads(stripped)
        except json.JSONDecodeError:
            continue
        if isinstance(obj, dict):
            yield obj


def parse_trace(lines: Iterable[dict[str, Any]]) -> Trace:
    session_id: str | None = None
    model: str | None = None
    declared: set[str] = set()
    called: Counter[str] = Counter()
    total_cost: float | None = None
    api_calls = 0

    for obj in lines:
        kind = obj.get("type")
        if kind == "system" and obj.get("subtype") == "init":
            model = obj.get("model") or model
            session_id = obj.get("session_id") or session_id
            for name in obj.get("tools") or []:
                declared.add(str(name))
        elif kind == "assistant":
            api_calls += 1
            message = obj.get("message") or {}
            for block in message.get("content") or []:
                if isinstance(block, dict) and block.get("type") == "tool_use" and block.get("name"):
                    called[str(block["name"])] += 1
        elif kind == "result":
            session_id = obj.get("session_id") or session_id
            if obj.get("total_cost_usd") is not None:
                total_cost = float(obj["total_cost_usd"])

    return Trace(session_id, model, frozenset(declared), called, total_cost, api_calls)


def parse_trace_file(path: str | Path) -> Trace:
    return parse_trace(iter_json_lines(Path(path).read_text(encoding="utf-8")))
