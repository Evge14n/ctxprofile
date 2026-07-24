from __future__ import annotations

import json
from pathlib import Path

import pytest

from ctxprofile.cli import main
from ctxprofile.ingest_sdk import parse_trace_file
from ctxprofile.mcp_audit import audit

FIXTURES = Path(__file__).parent / "fixtures"


def test_trace_parses_calls_and_cost() -> None:
    trace = parse_trace_file(FIXTURES / "trace.jsonl")
    assert trace.tools_called["mcp__files__read"] == 2
    assert trace.tools_called["read_file"] == 1
    assert trace.api_calls == 3
    assert trace.total_cost_usd == 0.12
    assert trace.session_id == "s1"


def test_audit_flags_unused_server_first() -> None:
    defs = json.loads((FIXTURES / "mcp_defs.json").read_text(encoding="utf-8"))
    trace = parse_trace_file(FIXTURES / "trace.jsonl")
    report = audit(defs, [trace])
    ruflo = next(s for s in report.servers if s.server == "mcp__ruflo")
    assert ruflo.calls == 0
    assert ruflo.tool_count == 2
    assert set(ruflo.uncalled_tools) == {"mcp__ruflo__agent_spawn", "mcp__ruflo__agent_list"}
    assert report.servers[0].server == "mcp__ruflo"
    assert report.window_api_calls == 3


def test_called_server_has_calls() -> None:
    defs = json.loads((FIXTURES / "mcp_defs.json").read_text(encoding="utf-8"))
    trace = parse_trace_file(FIXTURES / "trace.jsonl")
    report = audit(defs, [trace])
    files = next(s for s in report.servers if s.server == "mcp__files")
    assert files.calls == 2
    assert files.uncalled_tools == []


def test_cli_mcp_audit(capsys: pytest.CaptureFixture[str]) -> None:
    main(
        [
            "mcp-audit",
            "--defs",
            str(FIXTURES / "mcp_defs.json"),
            "--traces",
            str(FIXTURES / "trace.jsonl"),
        ]
    )
    out = capsys.readouterr().out
    assert "mcp__ruflo" in out
    assert "shipped every request" in out
