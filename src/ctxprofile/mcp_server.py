from __future__ import annotations

from typing import Any

from ctxprofile.cost import analyze
from ctxprofile.ingest_sdk import iter_json_lines, parse_trace
from ctxprofile.mcp_audit import audit
from ctxprofile.serialize import audit_to_dict, report_to_dict


def _server_class() -> Any:
    """The SDK renamed its server class in 2.0; both expose .tool() and .run()."""
    try:
        from mcp.server.mcpserver import MCPServer

        return MCPServer
    except ImportError:  # mcp < 2.0
        from mcp.server.fastmcp import FastMCP

        return FastMCP


mcp = _server_class()("ctxprofile")


@mcp.tool()
def analyze_request(capture: dict[str, Any], model: str | None = None) -> dict[str, Any]:
    """Cost-attribute one captured Anthropic Messages request per context component
    (system, each tool definition, history, tool results) and flag tools that are
    defined but not called. Pass a request object, or {"request": ..., "response": ...}."""
    return report_to_dict(analyze(capture, model_override=model))


@mcp.tool()
def audit_mcp_servers(defs: dict[str, Any], traces_jsonl: str = "") -> dict[str, Any]:
    """Dead-tool cost grouped by MCP server: join tool schemas from a request (`defs`)
    with call counts from a Claude Agent SDK / `claude -p` JSONL trace corpus."""
    traces = [parse_trace(iter_json_lines(traces_jsonl))] if traces_jsonl.strip() else []
    return audit_to_dict(audit(defs, traces))


def main() -> None:
    mcp.run()


if __name__ == "__main__":
    main()
