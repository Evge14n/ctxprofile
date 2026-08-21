from __future__ import annotations

import copy
import json
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from typing import Any

from ctxprofile.tokenizer import estimate_tokens

CHECK_DEAD_DEFS = "unreferenced-defs"
CHECK_SCHEMA_META = "schema-meta"

# JSON Schema annotations that describe the document rather than the arguments, and
# that nothing can point at: removing them cannot change a tool call.
FREE_META_KEYS = ("$schema", "$comment")
# These two *can* be reference targets — `$anchor` for `#name`, `$id` as the base a
# relative `$ref` resolves against — so they only come out when the schema holds no
# reference this linter cannot follow.
TARGETABLE_META_KEYS = ("$id", "$anchor")
META_KEYS = (*FREE_META_KEYS, *TARGETABLE_META_KEYS)

_DEF_HOLDERS = ("$defs", "definitions")
# Keywords whose value is a map of *names* to subschemas. The names are data, not
# keywords, so a property called "$ref" must not be mistaken for a reference.
_SCHEMA_MAPS = frozenset(
    {"properties", "patternProperties", "dependentSchemas", "$defs", "definitions"}
)
# Reference forms this linter cannot resolve. Their presence means "assume everything
# is reachable" rather than "delete something that might be needed".
_OPAQUE_REFS = frozenset({"$dynamicRef", "$recursiveRef"})

MeasureTools = Callable[[list[dict[str, Any]]], int]


@dataclass(frozen=True)
class Finding:
    tool: str
    check: str
    names: list[str]
    tokens: int

    @property
    def detail(self) -> str:
        shown = ", ".join(self.names[:3])
        extra = len(self.names) - 3
        return f"{shown} (+{extra} more)" if extra > 0 else shown


@dataclass(frozen=True)
class LintReport:
    findings: list[Finding]
    baseline_tokens: int
    removable_tokens: int
    exact: bool

    @property
    def removable_pct(self) -> float:
        if self.baseline_tokens <= 0:
            return 0.0
        return self.removable_tokens / self.baseline_tokens * 100.0


def _tokens(tools: Iterable[dict[str, Any]]) -> int:
    """Token estimate for a tools array, using the same per-tool serialization as `analyze`.

    Estimating each definition separately (rather than the concatenated array) keeps
    `lint` and `analyze` reporting the same number for the same tool block.
    """
    return sum(estimate_tokens(json.dumps(tool, ensure_ascii=False)) for tool in tools)


def _unescape(token: str) -> str:
    """JSON Pointer escaping: `~1` is a literal `/`, `~0` a literal `~` (RFC 6901)."""
    return token.replace("~1", "/").replace("~0", "~")


def _scan_refs(node: Any, targets: set[tuple[str, str]], opaque: list[bool]) -> None:
    """Collect the (holder, definition) pairs a subtree points at.

    `opaque` is raised by any reference this linter cannot follow — an anchor, a
    relative or absolute URI, a dynamic reference. When it is raised the caller
    keeps every definition rather than guessing which one the reference meant.
    """
    if isinstance(node, dict):
        for key, value in node.items():
            if key in _OPAQUE_REFS:
                opaque[0] = True
                continue
            if key == "$ref":
                _classify_ref(value, targets, opaque)
                continue
            if key in _SCHEMA_MAPS and isinstance(value, dict):
                for sub in value.values():
                    _scan_refs(sub, targets, opaque)
                continue
            _scan_refs(value, targets, opaque)
    elif isinstance(node, list):
        for item in node:
            _scan_refs(item, targets, opaque)


def _classify_ref(ref: Any, targets: set[tuple[str, str]], opaque: list[bool]) -> None:
    if not isinstance(ref, str) or not ref.startswith("#/"):
        # "#name" (anchor), "other.json", "https://…": not a plain local pointer.
        opaque[0] = True
        return
    segments = ref[2:].split("/")
    holder = _unescape(segments[0])
    if holder not in _DEF_HOLDERS:
        return  # a local pointer into the schema body, e.g. "#/properties/x"
    if len(segments) < 2:
        opaque[0] = True  # "#/$defs" — the whole registry
        return
    # A pointer *into* a definition ("#/$defs/Shape/properties/inner") still needs the
    # whole definition to be present, so the first segment is what keeps it alive.
    targets.add((holder, _unescape(segments[1])))


def _reachable_defs(schema: dict[str, Any]) -> tuple[set[tuple[str, str]], bool]:
    root = {key: value for key, value in schema.items() if key not in _DEF_HOLDERS}
    opaque = [False]
    frontier: set[tuple[str, str]] = set()
    _scan_refs(root, frontier, opaque)
    reached: set[tuple[str, str]] = set()
    while frontier:
        target = frontier.pop()
        if target in reached:
            continue
        reached.add(target)
        holder, name = target
        body = schema.get(holder)
        if isinstance(body, dict) and name in body:
            _scan_refs(body[name], frontier, opaque)
    return reached, opaque[0]


def _dead_defs(schema: dict[str, Any], holder: str) -> list[str]:
    """Definitions under `holder` that no `$ref` can reach from the schema root."""
    defs = schema.get(holder)
    if not isinstance(defs, dict) or not defs:
        return []
    reached, opaque = _reachable_defs(schema)
    if opaque:
        return []
    live = {name for found_holder, name in reached if found_holder == holder}
    return [name for name in defs if name not in live]


def _removable_meta(schema: dict[str, Any]) -> list[str]:
    keys = [key for key in FREE_META_KEYS if key in schema]
    _, opaque = _reachable_defs(schema)
    if not opaque:
        keys += [key for key in TARGETABLE_META_KEYS if key in schema]
    return [key for key in META_KEYS if key in keys]


def _slim_schema(schema: dict[str, Any]) -> dict[str, Any]:
    drop = set(_removable_meta(schema))
    out = {key: value for key, value in schema.items() if key not in drop}
    for holder in _DEF_HOLDERS:
        dead = _dead_defs(out, holder)
        if not dead:
            continue
        kept = {name: body for name, body in out[holder].items() if name not in dead}
        if kept:
            out[holder] = kept
        else:
            del out[holder]
    return out


def slim_tools(tools: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    """A copy of `tools` with weight no tool call can use removed."""
    slim: list[dict[str, Any]] = []
    for tool in tools:
        copied = copy.deepcopy(tool)
        schema = copied.get("input_schema")
        if isinstance(schema, dict):
            copied["input_schema"] = _slim_schema(schema)
        slim.append(copied)
    return slim


def _saving(tool: dict[str, Any], schema: dict[str, Any]) -> int:
    variant = {**tool, "input_schema": schema}
    return max(0, _tokens([tool]) - _tokens([variant]))


def lint_tools(
    tools: Iterable[dict[str, Any]], measure: MeasureTools | None = None
) -> LintReport:
    tools = list(tools)
    findings: list[Finding] = []
    for tool in tools:
        name = str(tool.get("name", "<unnamed>"))
        schema = tool.get("input_schema")
        if not isinstance(schema, dict):
            continue
        meta = _removable_meta(schema)
        if meta:
            without = {key: value for key, value in schema.items() if key not in set(meta)}
            findings.append(Finding(name, CHECK_SCHEMA_META, meta, _saving(tool, without)))
        for holder in _DEF_HOLDERS:
            dead = _dead_defs(schema, holder)
            if not dead:
                continue
            without = dict(schema)
            kept = {n: b for n, b in schema[holder].items() if n not in dead}
            if kept:
                without[holder] = kept
            else:
                del without[holder]
            findings.append(Finding(name, CHECK_DEAD_DEFS, dead, _saving(tool, without)))
    findings.sort(key=lambda f: (-f.tokens, f.tool, f.check))

    slim = slim_tools(tools)
    if measure is not None:
        baseline = measure(tools)
        removable = max(0, baseline - measure(slim))
        return LintReport(findings, baseline, removable, exact=True)
    baseline = _tokens(tools)
    removable = max(0, baseline - _tokens(slim))
    return LintReport(findings, baseline, removable, exact=False)
