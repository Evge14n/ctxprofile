from __future__ import annotations

import json
from pathlib import Path

import pytest

from ctxprofile.cli import format_report, main
from ctxprofile.cost import analyze

FIXTURE = Path(__file__).parent / "fixtures" / "capture.json"


def test_cli_table(capsys: pytest.CaptureFixture[str]) -> None:
    rc = main(["analyze", str(FIXTURE)])
    assert rc == 0
    out = capsys.readouterr().out
    assert "not called in this capture" in out
    assert "web_search" in out
    assert "claude-opus-4-8" in out


def test_cli_json(capsys: pytest.CaptureFixture[str]) -> None:
    rc = main(["analyze", str(FIXTURE), "--json"])
    assert rc == 0
    data = json.loads(capsys.readouterr().out)
    assert data["reconciled"] is True
    assert data["total_tokens"] == 900
    assert set(data["dead_tools"]) == {"web_search", "write_file"}


def test_a_long_mcp_name_keeps_both_the_server_and_the_tool_readable():
    from ctxprofile.cli import _fit

    name = "mcp__playwright__browser_take_screenshot"
    fitted = _fit(name, 30)
    assert len(fitted) == 30
    assert fitted.startswith("mcp__play")
    assert fitted.endswith("take_screenshot")
    # Short names are left alone.
    assert _fit("system", 30) == "system"


def test_top_shows_the_priciest_rows_and_accounts_for_the_rest():
    payload = json.loads(Path("examples/mcp-real.json").read_text(encoding="utf-8"))
    report = analyze(payload)
    text = format_report(report, top=5)

    rows = [line for line in text.splitlines() if line.startswith("  mcp__")]
    assert len(rows) == 5
    hidden = len(report.components) - 5
    assert f"and {hidden} more components" in text
    # Nothing is dropped from the totals just because it is not printed.
    assert f"{report.total_usd_cold:.5f}" in text


def test_dead_tools_are_summarized_rather_than_listed_in_full():
    payload = json.loads(Path("examples/mcp-real.json").read_text(encoding="utf-8"))
    text = format_report(analyze(payload))
    assert "46 tools not called in this capture" in text
    assert "and 43 more" in text
    # The wall-of-names version was over 2,000 characters on this input.
    listing = next(line for line in text.splitlines() if "and 43 more" in line)
    assert len(listing) < 200


def test_demo_runs_on_bundled_data_with_no_arguments(capsys):
    assert main(["demo"]) == 0
    out = capsys.readouterr().out
    assert "four public MCP servers" in out
    assert "8299" in out


LINT_REQUEST = {
    "model": "claude-sonnet-5",
    "messages": [{"role": "user", "content": "hi"}],
    "tools": [
        {
            "name": "clean",
            "description": "a clean tool",
            "input_schema": {"type": "object", "properties": {"x": {"type": "string"}}},
        },
        {
            "name": "heavy",
            "description": "a tool carrying weight nothing can reach",
            "input_schema": {
                "$schema": "http://json-schema.org/draft-07/schema#",
                "type": "object",
                "properties": {"y": {"type": "string"}},
                "$defs": {"Unused": {"type": "object", "description": "z" * 300}},
            },
        },
    ],
}


def _write_lint_request(tmp_path: Path) -> Path:
    path = tmp_path / "request.json"
    path.write_text(json.dumps(LINT_REQUEST), encoding="utf-8")
    return path


def test_lint_reports_removable_weight(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["lint", str(_write_lint_request(tmp_path))]) == 0
    out = capsys.readouterr().out
    assert "unreferenced-defs" in out
    assert "schema-meta" in out
    assert "clean" not in out.split("removable:")[0].replace("no removable", "")


def test_lint_json_carries_the_dollar_figure(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    assert main(["lint", str(_write_lint_request(tmp_path)), "--json"]) == 0
    data = json.loads(capsys.readouterr().out)
    assert data["removable_tokens"] > 0
    assert data["removable_usd_cold"] > 0
    assert data["exact"] is False
    assert {f["check"] for f in data["findings"]} == {"unreferenced-defs", "schema-meta"}


def test_lint_write_slim_produces_a_request_that_analyzes_lower(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    source = _write_lint_request(tmp_path)
    slim = tmp_path / "slim.json"
    main(["lint", str(source), "--json", "--write-slim", str(slim)])
    claimed = json.loads(capsys.readouterr().out)["removable_tokens"]

    main(["analyze", str(source), "--json"])
    before = json.loads(capsys.readouterr().out)["total_tokens"]
    main(["analyze", str(slim), "--json"])
    after = json.loads(capsys.readouterr().out)["total_tokens"]
    assert before - after == claimed, "the promised saving must be the saving analyze then shows"


def test_lint_online_refuses_before_spending_when_over_the_call_budget(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    rc = main(["lint", str(_write_lint_request(tmp_path)), "--online", "--online-max-calls", "1"])
    assert rc != 0
    assert "online-max-calls" in capsys.readouterr().err


def test_lint_needs_a_model(tmp_path: Path) -> None:
    path = tmp_path / "no-model.json"
    path.write_text(json.dumps({"tools": [], "messages": []}), encoding="utf-8")
    assert main(["lint", str(path)]) != 0


def test_lint_baseline_matches_what_analyze_charges_for_tool_definitions(tmp_path: Path) -> None:
    from ctxprofile.lint import lint_tools
    from ctxprofile.models import KIND_TOOL_DEF

    report = analyze(LINT_REQUEST)
    from_analyze = sum(c.tokens for c in report.components if c.kind == KIND_TOOL_DEF)
    assert lint_tools(LINT_REQUEST["tools"]).baseline_tokens == from_analyze


def test_lint_caps_the_table_and_says_how_many_it_hid(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    tools = [
        {
            "name": f"tool_{i}",
            "description": "d",
            "input_schema": {"$schema": "s", "type": "object", "properties": {}},
        }
        for i in range(25)
    ]
    path = tmp_path / "many.json"
    path.write_text(
        json.dumps({"model": "claude-sonnet-5", "messages": [], "tools": tools}), encoding="utf-8"
    )
    main(["lint", str(path), "--top", "5"])
    out = capsys.readouterr().out
    assert "... and 20 more" in out
    assert "schema-meta in 25 tools" in out

    main(["lint", str(path), "--top", "0"])
    assert "more (--top 0" not in capsys.readouterr().out
