"""Read real tool definitions out of MCP servers into an Anthropic-shaped request.

Connects to each configured stdio server, lists its tools, and assembles a request
whose `tools[]` holds the actual schemas, so the rest of the CLI can cost and lint
what those servers really ship.

Optional. Talking to MCP servers needs the `mcp` SDK (`pip install "ctxprofile[mcp]"`);
the import is deferred so the core stays dependency-free.
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from typing import Any

DEFAULT_MODEL = "claude-opus-4-8"
DEFAULT_TIMEOUT = 90.0
Report = Callable[[str], None]


def ensure_sdk() -> None:
    """Fail once, up front, rather than as a per-server "skipped" for every entry."""
    try:
        import mcp  # noqa: F401
    except ImportError as error:
        raise RuntimeError(
            'collecting tools needs the MCP SDK: pip install "ctxprofile[mcp]"'
        ) from error


def tool_definition(server: str, tool: Any) -> dict[str, Any]:
    """One MCP tool as an Anthropic tool definition.

    The SDK renamed the field in 2.0 — `inputSchema` became `input_schema` — and a
    collector that knows only one of them silently returns zero tools.
    """
    schema = getattr(tool, "input_schema", None)
    if schema is None:
        schema = getattr(tool, "inputSchema", None)
    return {
        "name": f"mcp__{server}__{tool.name}",
        "description": tool.description or "",
        "input_schema": schema or {"type": "object"},
    }


async def list_tools(name: str, spec: dict[str, Any], timeout: float) -> list[dict[str, Any]]:
    ensure_sdk()
    from mcp import ClientSession, StdioServerParameters
    from mcp.client.stdio import get_default_environment, stdio_client

    # Servers that need credentials get them from the config's own env block, merged
    # over the SDK's default environment; without this they start and then fail.
    env = {**get_default_environment(), **(spec.get("env") or {})}
    params = StdioServerParameters(command=spec["command"], args=spec.get("args", []), env=env)
    async with stdio_client(params) as (read, write), ClientSession(read, write) as session:
        await asyncio.wait_for(session.initialize(), timeout)
        result = await asyncio.wait_for(session.list_tools(), timeout)
        return [tool_definition(name, tool) for tool in result.tools]


def describe(error: BaseException) -> str:
    """Flatten a TaskGroup's ExceptionGroup down to the error that actually happened."""
    while isinstance(error, BaseExceptionGroup) and error.exceptions:
        error = error.exceptions[0]
    return f"{type(error).__name__}: {error}"


async def collect(
    config: dict[str, Any],
    timeout: float = DEFAULT_TIMEOUT,
    report: Report = print,
    lister: Any = None,
) -> tuple[list[dict[str, Any]], list[str]]:
    lister = lister or list_tools
    collected: list[dict[str, Any]] = []
    skipped: list[str] = []
    for name, spec in (config.get("mcpServers") or {}).items():
        if not spec.get("command"):
            # url / http entries speak a different transport than this collector.
            report(f"  {name}: skipped (not a stdio server)")
            skipped.append(name)
            continue
        try:
            tools = await lister(name, spec, timeout)
        except Exception as error:  # noqa: BLE001 - one broken server must not lose the rest
            report(f"  {name}: skipped ({describe(error)})")
            skipped.append(name)
            continue
        report(f"  {name}: {len(tools)} tools")
        collected.extend(tools)
    return collected, skipped


def build_request(tools: list[dict[str, Any]], model: str = DEFAULT_MODEL) -> dict[str, Any]:
    return {
        "model": model,
        "system": "You are a helpful coding assistant.",
        "tools": tools,
        "messages": [{"role": "user", "content": "List the files in this project."}],
    }


def run(
    config: dict[str, Any],
    model: str = DEFAULT_MODEL,
    timeout: float = DEFAULT_TIMEOUT,
    report: Report = print,
    lister: Any = None,
) -> tuple[dict[str, Any], list[str]]:
    tools, skipped = asyncio.run(collect(config, timeout, report, lister))
    return build_request(tools, model), skipped
