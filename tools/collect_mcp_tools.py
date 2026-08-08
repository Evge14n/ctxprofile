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
from mcp.client.stdio import stdio_client


async def list_tools(name: str, command: str, args: list[str], timeout: float) -> list[dict[str, Any]]:
    params = StdioServerParameters(command=command, args=args)
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


async def collect(config: dict[str, Any], timeout: float) -> list[dict[str, Any]]:
    collected: list[dict[str, Any]] = []
    for name, spec in config.get("mcpServers", {}).items():
        try:
            tools = await list_tools(name, spec["command"], spec.get("args", []), timeout)
        except Exception as error:  # noqa: BLE001 - one broken server must not lose the rest
            print(f"  {name}: skipped ({type(error).__name__}: {error})")
            continue
        print(f"  {name}: {len(tools)} tools")
        collected.extend(tools)
    return collected


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("config", help="MCP client config JSON with an mcpServers object")
    parser.add_argument("-o", "--output", required=True, help="where to write the request JSON")
    parser.add_argument("--model", default="claude-opus-4-8")
    parser.add_argument("--timeout", type=float, default=90.0)
    args = parser.parse_args()

    config = json.loads(Path(args.config).read_text(encoding="utf-8"))
    print("collecting tools from MCP servers:")
    tools = asyncio.run(collect(config, args.timeout))

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
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
