from __future__ import annotations

from pathlib import Path

import pytest

from ctxprofile.cli import main

FIXTURES = Path(__file__).parent / "fixtures"


def test_unknown_model_is_a_clean_error(capsys: pytest.CaptureFixture[str]) -> None:
    rc = main(["analyze", str(FIXTURES / "capture.json"), "--model", "gpt-4"])
    assert rc == 2
    err = capsys.readouterr().err
    assert "unknown model" in err
    assert "Traceback" not in err


def test_missing_file_is_a_clean_error(capsys: pytest.CaptureFixture[str]) -> None:
    rc = main(["analyze", "no_such_file.json"])
    assert rc == 2
    assert capsys.readouterr().err.startswith("error:")


def test_ci_without_budget_or_lock_returns_2(capsys: pytest.CaptureFixture[str]) -> None:
    rc = main(["ci", str(FIXTURES / "capture.json")])
    assert rc == 2
