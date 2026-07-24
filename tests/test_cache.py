from __future__ import annotations

import json
from pathlib import Path

import pytest

from ctxprofile import pricing
from ctxprofile.cli import main
from ctxprofile.cost import analyze

FIXTURES = Path(__file__).parent / "fixtures"


def _load(name: str) -> dict:
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


def test_billed_input_usd_blends_buckets() -> None:
    # 100 fresh @ $5, 800 cache-read @ $0.5 per MTok
    assert pricing.billed_input_usd("claude-opus-4-8", 100, cache_read=800) == pytest.approx(0.0009)


def test_effective_below_cold_when_cached() -> None:
    report = analyze(_load("capture_cached.json"))
    assert report.cached
    assert report.total_usd_effective < report.total_usd_cold
    assert report.total_usd_effective == pytest.approx(0.0009, abs=1e-5)


def test_uncached_effective_equals_cold() -> None:
    report = analyze(_load("capture.json"))
    assert not report.cached
    assert report.total_usd_effective == pytest.approx(report.total_usd_cold)


def test_cli_shows_effective_when_cached(capsys: pytest.CaptureFixture[str]) -> None:
    main(["analyze", str(FIXTURES / "capture_cached.json")])
    assert "measured, blended cache" in capsys.readouterr().out
