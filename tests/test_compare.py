from __future__ import annotations

import json
from pathlib import Path

from ctxprofile.cli import main
from ctxprofile.compare import compare_payloads

FIXTURES = Path(__file__).parent / "fixtures"


def _load(name: str) -> dict:
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


def test_removing_a_tool_lowers_cost() -> None:
    diff = compare_payloads(_load("capture.json"), _load("capture_b.json"))
    assert diff.total_usd_cold_b < diff.total_usd_cold_a


def test_removed_tool_appears_in_diff() -> None:
    diff = compare_payloads(_load("capture.json"), _load("capture_b.json"))
    web = next(r for r in diff.rows if r.name == "web_search")
    assert web.status == "removed"
    assert web.delta_tokens < 0
    assert web.delta_usd_cold < 0


def test_dead_tool_set_shrinks() -> None:
    diff = compare_payloads(_load("capture.json"), _load("capture_b.json"))
    assert "web_search" in diff.dead_tools_a
    assert "web_search" not in diff.dead_tools_b


def test_cli_compare(capsys) -> None:
    rc = main(["compare", str(FIXTURES / "capture.json"), str(FIXTURES / "capture_b.json")])
    assert rc == 0
    out = capsys.readouterr().out
    assert "compare" in out
    assert "web_search" in out
