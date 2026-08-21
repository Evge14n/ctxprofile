from __future__ import annotations

import json
from pathlib import Path

import pytest

pytest.importorskip("mcp", reason="the MCP server is an optional extra")

from ctxprofile.mcp_server import analyze_request, audit_mcp_servers, lint_tool_schemas

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


LINTABLE = {
    "model": "claude-opus-4-8",
    "messages": [{"role": "user", "content": "hi"}],
    "tools": [
        {
            "name": "t",
            "description": "d",
            "input_schema": {
                "$schema": "http://json-schema.org/draft-07/schema#",
                "type": "object",
                "properties": {"x": {"type": "string"}},
                "$defs": {"Unused": {"type": "string"}},
            },
        }
    ],
}


def test_lint_tool_schemas_reports_removable_weight() -> None:
    result = lint_tool_schemas(LINTABLE)
    assert result["removable_tokens"] > 0
    assert {f["check"] for f in result["findings"]} == {"unreferenced-defs", "schema-meta"}
    assert result["model"] == "claude-opus-4-8"


def test_lint_tool_schemas_accepts_a_wrapped_capture() -> None:
    assert lint_tool_schemas({"request": LINTABLE})["removable_tokens"] > 0


def test_lint_tool_schemas_needs_a_model() -> None:
    with pytest.raises(ValueError, match="model not found"):
        lint_tool_schemas({"tools": [], "messages": []})
