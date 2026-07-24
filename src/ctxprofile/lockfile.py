from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from ctxprofile.ingest import parse_request
from ctxprofile.models import KIND_SYSTEM, KIND_TOOL_DEF
from ctxprofile.tokenizer import estimate_tokens

LOCK_VERSION = 1
# The lock stores raw estimator counts (no usage reconciliation, since PR fixtures
# rarely carry a usage block). The absolute number is biased, but the gate fires on
# the delta of the same stable estimator, so the bias cancels. Bump this if the
# estimator changes, which forces a re-lock.
ESTIMATOR = "chars4-v1"


def server_prefix(tool: str) -> str | None:
    if not tool.startswith("mcp__"):
        return None
    parts = tool.split("__")
    return "__".join(parts[:2]) if len(parts) >= 2 else None


def static_summary(request: dict[str, Any]) -> dict[str, Any]:
    """The static context floor: the system prompt and the shipped tool definitions,
    which live in the repo and do not depend on a particular request's data."""
    components, defined, _ = parse_request(request)
    component_tokens: dict[str, int] = {}
    for component in components:
        if component.kind == KIND_SYSTEM:
            component_tokens["system"] = estimate_tokens(component.text)
        elif component.kind == KIND_TOOL_DEF:
            component_tokens[f"tool:{component.name}"] = estimate_tokens(component.text)
    servers = sorted({p for p in (server_prefix(t) for t in defined) if p is not None})
    return {
        "static_input_tokens": sum(component_tokens.values()),
        "components": component_tokens,
        "tools": sorted(defined),
        "servers": servers,
    }


def build_lock(request: dict[str, Any], model: str | None) -> dict[str, Any]:
    return {
        "ctxprofile_lock_version": LOCK_VERSION,
        "model": model,
        "estimator": ESTIMATOR,
        **static_summary(request),
    }


def write_lock(lock: dict[str, Any], path: str | Path) -> None:
    Path(path).write_text(json.dumps(lock, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def load_lock(path: str | Path) -> dict[str, Any]:
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise TypeError("lock file must be a JSON object")
    return data


def diff_lock(
    current: dict[str, Any],
    lock: dict[str, Any],
    max_regression_tokens: int = 0,
    fail_on_new_tool: bool = True,
) -> list[str]:
    violations: list[str] = []
    if lock.get("estimator") != ESTIMATOR:
        violations.append(f"lock estimator {lock.get('estimator')!r} != {ESTIMATOR!r}; re-lock needed")
    delta = int(current["static_input_tokens"]) - int(lock.get("static_input_tokens", 0))
    if delta > max_regression_tokens:
        violations.append(
            f"static floor +{delta} tokens over lock (allowed +{max_regression_tokens})"
        )
    if fail_on_new_tool:
        new_tools = sorted(set(current["tools"]) - set(lock.get("tools", [])))
        if new_tools:
            violations.append(f"new tool(s) not in lock: {', '.join(new_tools)}")
    return violations
