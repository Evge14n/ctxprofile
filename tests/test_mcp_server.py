from __future__ import annotations

import json
from pathlib import Path

import pytest

pytest.importorskip("mcp", reason="the MCP server is an optional extra")

from ctxprofile.mcp_server import analyze_request, audit_mcp_servers

FIXTURES = Path(__file__).parent / "fixtures"


def test_analyze_request_tool_returns_report() -> None:
    payload = json.loads((FIXTURES / "capture.json").read_text(encoding="utf-8"))
    result = analyze_request(payload)
    assert result["total_tokens"] == 900
    assert result["reconciled"] is True
    assert "web_search" in result["dead_tools"]


def test_audit_tool_returns_servers() -> None:
    defs = json.loads((FIXTURES / "mcp_defs.json").read_text(encoding="utf-8"))
    traces = (FIXTURES / "trace.jsonl").read_text(encoding="utf-8")
    result = audit_mcp_servers(defs, traces)
    ruflo = next(s for s in result["servers"] if s["server"] == "mcp__ruflo")
    assert ruflo["calls"] == 0


def test_audit_tool_handles_empty_traces() -> None:
    defs = json.loads((FIXTURES / "mcp_defs.json").read_text(encoding="utf-8"))
    result = audit_mcp_servers(defs, "")
    assert result["window_api_calls"] == 0
