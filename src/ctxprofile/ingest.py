from __future__ import annotations

import json
from typing import Any

from ctxprofile.models import (
    KIND_CURRENT_USER,
    KIND_HISTORY,
    KIND_SYSTEM,
    KIND_TOOL_DEF,
    KIND_TOOL_RESULT,
    Component,
)


def _block_text(block: Any) -> str:
    if isinstance(block, str):
        return block
    if not isinstance(block, dict):
        return str(block)
    kind = block.get("type")
    if kind == "text":
        return str(block.get("text", ""))
    if kind == "thinking":
        return str(block.get("thinking", ""))
    if kind == "tool_use":
        return json.dumps(block.get("input", {}), ensure_ascii=False)
    if kind == "tool_result":
        return _content_text(block.get("content", ""))
    return json.dumps(block, ensure_ascii=False)


def _content_text(content: Any) -> str:
    if content is None:
        return ""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "\n".join(_block_text(b) for b in content)
    return str(content)


def parse_request(req: dict[str, Any]) -> tuple[list[Component], set[str], set[str]]:
    """Split an Anthropic Messages request into named context components and
    return (components, tools_defined, tools_called)."""
    components: list[Component] = []

    system = req.get("system")
    if system:
        components.append(Component(KIND_SYSTEM, "system", _content_text(system)))

    defined: set[str] = set()
    for tool in req.get("tools") or []:
        name = tool.get("name", "<unnamed>")
        defined.add(name)
        components.append(Component(KIND_TOOL_DEF, name, json.dumps(tool, ensure_ascii=False)))

    called: set[str] = set()
    messages = req.get("messages") or []
    last_user = max((i for i, m in enumerate(messages) if m.get("role") == "user"), default=-1)

    for i, message in enumerate(messages):
        role = message.get("role")
        content = message.get("content")
        blocks = content if isinstance(content, list) else [content] if content is not None else []

        if role == "assistant":
            for block in blocks:
                if isinstance(block, dict) and block.get("type") == "tool_use" and block.get("name"):
                    called.add(block["name"])

        for block in blocks:
            if isinstance(block, dict) and block.get("type") == "tool_result":
                components.append(
                    Component(KIND_TOOL_RESULT, f"tool_result[{i}]", _content_text(block.get("content", "")))
                )

        rest = [b for b in blocks if not (isinstance(b, dict) and b.get("type") == "tool_result")]
        text = _content_text(rest)
        if text.strip():
            kind = KIND_CURRENT_USER if role == "user" and i == last_user else KIND_HISTORY
            components.append(Component(kind, f"{role}[{i}]", text))

    return components, defined, called
