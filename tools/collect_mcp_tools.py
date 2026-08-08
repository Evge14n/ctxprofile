"""Collect real tool definitions from MCP servers into an Anthropic-shaped request.

Connects to each configured MCP server over stdio, lists its tools, and writes a
request JSON whose `tools[]` holds the real schemas — so `ctxprofile analyze` can
report what those servers actually cost per request.

Usage:
    python tools/collect_mcp_tools.py servers.json -o examples/mcp-real.json
"""

from __future__ import annotations

import argparse
import asyncio
import json
from pathlib import Path
from typing import Any

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import get_default_environment, stdio_client


async def list_tools(
    name: str, spec: dict[str, Any], timeout: float
) -> list[dict[str, Any]]:
    # Servers that need credentials get them from the config's own env block, merged
    # over the SDK's default environment; without this they start and then fail.
    env = {**get_default_environment(), **(spec.get("env") or {})}
    params = StdioServerParameters(
        command=spec["command"], args=spec.get("args", []), env=env
    )
    tools: list[dict[str, Any]] = []
    async with stdio_client(params) as (read, write), ClientSession(read, write) as session:
        await asyncio.wait_for(session.initialize(), timeout)
        result = await asyncio.wait_for(session.list_tools(), timeout)
        for tool in result.tools:
            tools.append(
                {
                    "name": f"mcp__{name}__{tool.name}",
                    "description": tool.description or "",
                    "input_schema": tool.inputSchema or {"type": "object"},
                }
            )
    return tools


def _describe(error: BaseException) -> str:
    """Flatten a TaskGroup's ExceptionGroup down to the error that actually happened."""
    while isinstance(error, BaseExceptionGroup) and error.exceptions:
        error = error.exceptions[0]
    return f"{type(error).__name__}: {error}"


async def collect(config: dict[str, Any], timeout: float) -> tuple[list[dict[str, Any]], list[str]]:
    collected: list[dict[str, Any]] = []
    skipped: list[str] = []
    for name, spec in config.get("mcpServers", {}).items():
        if not spec.get("command"):
            # url / http entries speak a different transport than this collector.
            print(f"  {name}: skipped (not a stdio server)")
            skipped.append(name)
            continue
        try:
            tools = await list_tools(name, spec, timeout)
        except Exception as error:  # noqa: BLE001 - one broken server must not lose the rest
            print(f"  {name}: skipped ({_describe(error)})")
            skipped.append(name)
            continue
        print(f"  {name}: {len(tools)} tools")
        collected.extend(tools)
    return collected, skipped


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("config", help="MCP client config JSON with an mcpServers object")
    parser.add_argument("-o", "--output", required=True, help="where to write the request JSON")
    parser.add_argument("--model", default="claude-opus-4-8")
    parser.add_argument("--timeout", type=float, default=90.0)
    args = parser.parse_args()

    config = json.loads(Path(args.config).read_text(encoding="utf-8"))
    print("collecting tools from MCP servers:")
    tools, skipped = asyncio.run(collect(config, args.timeout))

    request = {
        "model": args.model,
        "system": "You are a helpful coding assistant.",
        "tools": tools,
        "messages": [{"role": "user", "content": "List the files in this project."}],
    }
    Path(args.output).write_text(
        json.dumps(request, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(f"wrote {args.output}: {len(tools)} tool definitions")
    if skipped:
        # A partial collection understates the per-request cost, which is the one number
        # this file exists to produce — so it must not look like a clean run.
        print(f"incomplete: {len(skipped)} server(s) contributed nothing ({', '.join(skipped)})")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
