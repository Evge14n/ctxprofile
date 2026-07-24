from __future__ import annotations

import pytest

from ctxprofile import pricing


def test_input_output_rates() -> None:
    assert pricing.rate_per_mtok("claude-opus-4-8", "input") == 5.0
    assert pricing.rate_per_mtok("claude-opus-4-8", "output") == 25.0


def test_cache_rates_are_derived() -> None:
    assert pricing.rate_per_mtok("claude-opus-4-8", "cache_read") == pytest.approx(0.5)
    assert pricing.rate_per_mtok("claude-opus-4-8", "cache_write_5m") == pytest.approx(6.25)
    assert pricing.rate_per_mtok("claude-sonnet-5", "cache_write_1h") == pytest.approx(6.0)


def test_usd() -> None:
    assert pricing.usd(1_000_000, "claude-haiku-4-5", "input") == pytest.approx(1.0)
    assert pricing.usd(2_000_000, "claude-opus-4-8", "output") == pytest.approx(50.0)


def test_unknown_model_raises() -> None:
    with pytest.raises(KeyError):
        pricing.rate_per_mtok("gpt-4", "input")


def test_unknown_mode_raises() -> None:
    with pytest.raises(ValueError):
        pricing.rate_per_mtok("claude-opus-4-8", "bogus")
