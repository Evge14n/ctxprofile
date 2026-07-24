from __future__ import annotations

import json
from pathlib import Path

import pytest

from ctxprofile.cli import main

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
