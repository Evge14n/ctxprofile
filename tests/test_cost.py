from __future__ import annotations

import json
from pathlib import Path

from ctxprofile.cost import analyze
from ctxprofile.models import KIND_TOOL_DEF

FIXTURE = Path(__file__).parent / "fixtures" / "capture.json"


def _payload() -> dict:
    return json.loads(FIXTURE.read_text(encoding="utf-8"))


def test_reconciles_to_billed_total_exactly() -> None:
    report = analyze(_payload())
    assert report.reconciled
    assert report.total_tokens == 900


def test_components_sum_to_total() -> None:
    report = analyze(_payload())
    assert sum(c.tokens for c in report.components) == report.total_tokens


def test_dead_tools_detected() -> None:
    report = analyze(_payload())
    assert report.dead_tools == ["web_search", "write_file"]
    assert report.wasted_usd_cold > 0


def test_unused_flag_only_on_dead_tools() -> None:
    report = analyze(_payload())
    for component in report.components:
        if component.unused:
            assert component.kind == KIND_TOOL_DEF
            assert component.name in {"web_search", "write_file"}


def test_sorted_by_cost_descending() -> None:
    report = analyze(_payload())
    costs = [c.usd_cold for c in report.components]
    assert costs == sorted(costs, reverse=True)


def test_no_usage_block_is_estimated() -> None:
    report = analyze(_payload()["request"])
    assert not report.reconciled
    assert report.total_tokens > 0


def test_model_override() -> None:
    payload = _payload()
    del payload["request"]["model"]
    report = analyze(payload, model_override="claude-haiku-4-5")
    assert report.model == "claude-haiku-4-5"


def test_reconciliation_is_exact_when_estimator_overshoots_billed() -> None:
    # Many components estimated far above a tiny billed total: the split must still
    # sum to the billed total with no negative components.
    request = {
        "model": "claude-opus-4-8",
        "system": "x" * 200,
        "tools": [
            {"name": f"t{i}", "description": "y" * 200, "input_schema": {}} for i in range(9)
        ],
        "messages": [{"role": "user", "content": "hi"}],
    }
    payload = {"request": request, "response": {"usage": {"input_tokens": 6}}}
    report = analyze(payload)
    assert report.reconciled
    assert report.total_tokens == 6
    assert sum(c.tokens for c in report.components) == 6
    assert all(c.tokens >= 0 for c in report.components)
