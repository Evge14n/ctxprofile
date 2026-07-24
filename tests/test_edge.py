from __future__ import annotations

from ctxprofile.cost import analyze
from ctxprofile.mcp_audit import audit


def test_request_with_only_messages() -> None:
    report = analyze({"model": "claude-opus-4-8", "messages": [{"role": "user", "content": "hi"}]})
    assert report.total_tokens > 0
    assert report.dead_tools == []
    assert not report.reconciled


def test_empty_tools_have_no_dead_tools() -> None:
    report = analyze(
        {
            "model": "claude-opus-4-8",
            "system": "s",
            "tools": [],
            "messages": [{"role": "user", "content": "hi"}],
        }
    )
    assert report.dead_tools == []


def test_string_content_blocks_and_list_blocks_both_parse() -> None:
    report = analyze(
        {
            "model": "claude-opus-4-8",
            "messages": [
                {"role": "user", "content": "plain string"},
                {"role": "assistant", "content": [{"type": "text", "text": "list block"}]},
            ],
        }
    )
    assert report.total_tokens > 0


def test_audit_with_no_traces_marks_everything_uncalled() -> None:
    defs = {
        "model": "claude-opus-4-8",
        "tools": [{"name": "mcp__x__a", "description": "d", "input_schema": {}}],
        "messages": [],
    }
    report = audit(defs, [])
    assert report.window_api_calls == 0
    assert report.servers[0].calls == 0
    assert report.servers[0].uncalled_tools == ["mcp__x__a"]
