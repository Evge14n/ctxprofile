from __future__ import annotations

import json
from pathlib import Path

from ctxprofile.budget import check, load_budget
from ctxprofile.cli import main
from ctxprofile.cost import analyze

FIXTURES = Path(__file__).parent / "fixtures"
BUDGET = FIXTURES / "ctxbudget.toml"


def _report(name: str):
    payload = json.loads((FIXTURES / name).read_text(encoding="utf-8"))
    return analyze(payload)


def test_load_budget() -> None:
    budget = load_budget(BUDGET)
    assert budget.max_input_tokens == 800
    assert budget.max_dead_tools == 1


def test_breach_reports_token_and_dead_tool_violations() -> None:
    violations = check(_report("capture.json"), load_budget(BUDGET))
    assert any("input tokens" in v for v in violations)
    assert any("dead tools" in v for v in violations)


def test_within_budget_has_no_violations() -> None:
    assert check(_report("capture_b.json"), load_budget(BUDGET)) == []


def test_cli_ci_fails_on_breach() -> None:
    assert main(["ci", "--budget", str(BUDGET), str(FIXTURES / "capture.json")]) == 1


def test_cli_ci_passes_within_budget() -> None:
    assert main(["ci", "--budget", str(BUDGET), str(FIXTURES / "capture_b.json")]) == 0


def test_cli_ci_github_format(capsys) -> None:
    rc = main(["ci", "--budget", str(BUDGET), "--format", "github", str(FIXTURES / "capture.json")])
    assert rc == 1
    out = capsys.readouterr().out
    assert "### ctxprofile" in out
    assert "❌" in out
