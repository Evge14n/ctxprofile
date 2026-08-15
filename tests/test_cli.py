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
