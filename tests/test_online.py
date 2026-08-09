from __future__ import annotations

import json
import sys
from typing import Any

import pytest

from ctxprofile.budget import Budget, check
from ctxprofile.cli import format_report, main
from ctxprofile.compare import compare_payloads
from ctxprofile.cost import TOOL_BLOCK_OVERHEAD, analyze, build_report, called_in_response
from ctxprofile.ingest import parse_request
from ctxprofile.mcp_audit import audit
from ctxprofile.models import (
    KIND_CURRENT_USER,
    KIND_HISTORY,
    KIND_SYSTEM,
    KIND_TOOL_DEF,
    Component,
)
from ctxprofile.online import (
    PROBE_MESSAGES,
    ExactSplit,
    anthropic_counter,
    default_counter,
    measure,
    planned_calls,
)


class FakeAPI:
    """A count_tokens stand-in with a known, exactly additive cost model.

    Mirrors the property the measurement relies on: the system block and each tool
    schema are serialized independently, and a fixed preamble is added once when
    any tool is present.
    """

    ENVELOPE = 10
    PROBE = 3
    TOOL_PREAMBLE = 7

    def __init__(self, system_cost: int = 0, tool_costs: dict[str, int] | None = None) -> None:
        self.system_cost = system_cost
        self.tool_costs = tool_costs or {}
        self.calls = 0

    def __call__(
        self,
        *,
        model: str,
        messages: list[dict[str, Any]],
        system: Any = None,
        tools: list[dict[str, Any]] | None = None,
    ) -> int:
        self.calls += 1
        total = self.ENVELOPE + self.PROBE * len(messages)
        if system:
            total += self.system_cost
        if tools:
            total += self.TOOL_PREAMBLE + sum(self.tool_costs[t["name"]] for t in tools)
        return total


def _request(tool_names: list[str], system: str = "you are a helpful agent") -> dict[str, Any]:
    return {
        "model": "claude-opus-4-8",
        "system": system,
        "tools": [{"name": name, "input_schema": {"type": "object"}} for name in tool_names],
        "messages": [{"role": "user", "content": "hello"}],
    }


def test_system_marginal_is_measured_not_estimated():
    api = FakeAPI(system_cost=91, tool_costs={"a": 20})
    split = measure(_request(["a"]), "claude-opus-4-8", api)
    assert split.system == 91


def test_each_tool_row_is_its_own_deletable_cost_not_a_share_of_the_block():
    costs = {"read_file": 40, "write_file": 120, "web_search": 200}
    api = FakeAPI(system_cost=50, tool_costs=costs)
    split = measure(_request(list(costs)), "claude-opus-4-8", api)

    assert split.tools_total == FakeAPI.TOOL_PREAMBLE + sum(costs.values())
    # Each row is exactly what removing that tool saves — the shared preamble is not
    # folded in, because deleting a tool does not recover it.
    assert split.tools == costs
    assert split.tool_overhead == FakeAPI.TOOL_PREAMBLE
    assert sum(split.tools.values()) + split.tool_overhead == split.tools_total


def test_the_shared_overhead_gets_its_own_row_rather_than_inflating_the_tools():
    costs = {"read_file": 40, "web_search": 200}
    api = FakeAPI(tool_costs=costs)
    report = analyze({"request": _request(list(costs), system="")}, counter=api)

    rows = {r.name: r for r in report.components}
    assert rows["read_file"].tokens == 40
    assert rows["web_search"].tokens == 200
    assert rows[TOOL_BLOCK_OVERHEAD].tokens == FakeAPI.TOOL_PREAMBLE
    # The overhead is not a declared tool, so it can never be reported as dead weight.
    assert not rows[TOOL_BLOCK_OVERHEAD].unused
    assert report.dead_tools == ["read_file", "web_search"]


def test_a_single_tool_carries_the_whole_block():
    api = FakeAPI(tool_costs={"only": 60})
    split = measure({"tools": [{"name": "only"}]}, "claude-opus-4-8", api)
    assert split.tools == {"only": FakeAPI.TOOL_PREAMBLE + 60}
    assert split.tool_overhead == 0


def test_call_count_is_bounded_and_reported():
    names = ["a", "b", "c", "d"]
    api = FakeAPI(system_cost=10, tool_costs=dict.fromkeys(names, 25))
    request = _request(names)
    split = measure(request, "claude-opus-4-8", api)
    # floor + system + all-tools + one leave-one-out per tool
    assert split.calls == 3 + len(names) == api.calls == planned_calls(request)


def test_max_calls_refuses_before_spending_any():
    api = FakeAPI(tool_costs=dict.fromkeys(["a", "b", "c"], 10))
    with pytest.raises(ValueError, match="count_tokens calls"):
        measure(_request(["a", "b", "c"]), "claude-opus-4-8", api, max_calls=3)
    assert api.calls == 0


def test_a_request_with_nothing_to_measure_spends_no_calls():
    api = FakeAPI()
    split = measure({"messages": []}, "claude-opus-4-8", api)
    assert (split.calls, split.system, split.tools) == (0, None, {})
    assert api.calls == 0


def test_measured_rows_keep_their_counts_and_the_rest_absorbs_the_remainder():
    components = [
        Component(KIND_SYSTEM, "system", "s" * 400),
        Component(KIND_TOOL_DEF, "read_file", "r" * 400),
        Component(KIND_HISTORY, "user[0]", "h" * 400),
        Component(KIND_CURRENT_USER, "user[1]", "u" * 400),
    ]
    exact = ExactSplit(
        system=300, tools={"read_file": 500}, tools_total=500, tool_overhead=0, calls=3
    )
    report = build_report(
        components,
        {"read_file"},
        set(),
        "claude-opus-4-8",
        usage={"input_tokens": 1000},
        exact=exact,
    )

    rows = {r.name: r for r in report.components}
    assert (rows["system"].tokens, rows["system"].exact) == (300, True)
    assert (rows["read_file"].tokens, rows["read_file"].exact) == (500, True)
    assert not rows["user[0]"].exact
    # The 200 tokens the billed total has left split across the two unmeasured rows.
    assert rows["user[0]"].tokens + rows["user[1]"].tokens == 200
    assert sum(r.tokens for r in report.components) == 1000
    assert report.reconciled and report.measured_calls == 3


def test_measurement_larger_than_the_billed_total_does_not_reconcile():
    components = [Component(KIND_SYSTEM, "system", "s" * 100)]
    exact = ExactSplit(system=9000, tools={}, tools_total=0, tool_overhead=0, calls=2)
    report = build_report(
        components, set(), set(), "claude-opus-4-8", usage={"input_tokens": 50}, exact=exact
    )
    assert report.reconciled is False
    assert report.components[0].tokens == 9000


def test_analyze_end_to_end_marks_the_measured_rows():
    request = _request(["read_file", "web_search"])
    api = FakeAPI(system_cost=64, tool_costs={"read_file": 40, "web_search": 260})
    report = analyze({"request": request}, counter=api)

    measured = {r.name for r in report.components if r.exact}
    assert measured == {"system", "read_file", "web_search", TOOL_BLOCK_OVERHEAD}
    assert report.measured_calls == api.calls
    # The ingest split, plus exactly one row for the shared tool-block overhead.
    assert len(report.components) == len(parse_request(request)[0]) + 1


def test_mcp_audit_uses_measured_tool_tokens_and_skips_the_system_call():
    costs = {"mcp__files__read": 400, "mcp__files__write": 600, "local_tool": 100}
    api = FakeAPI(system_cost=999, tool_costs=costs)
    report = audit(_request(list(costs)), [], counter=api)

    by_server = {s.server: s for s in report.servers}
    # Measured, not the ~4 chars/token estimate of a two-key schema stub, and each
    # server carries only its own tools' deletable cost.
    assert by_server["mcp__files"].tokens_per_request == 400 + 600
    assert by_server["(local)"].tokens_per_request == 100
    # floor + all-tools + one leave-one-out per tool; the system prompt is not measured.
    assert report.measured_calls == 2 + len(costs) == api.calls


class StubMessages:
    """Records the kwargs the SDK adapter builds, and answers like the real endpoint."""

    def __init__(self) -> None:
        self.seen: list[dict[str, Any]] = []

    def count_tokens(self, **kwargs: Any) -> Any:
        self.seen.append(kwargs)
        return type("MessageTokensCount", (), {"input_tokens": 42})()


def test_the_sdk_adapter_sends_only_the_fields_the_request_has():
    messages = StubMessages()
    counter = anthropic_counter(type("Client", (), {"messages": messages})())

    assert counter(model="claude-opus-4-8", messages=PROBE_MESSAGES) == 42
    counter(model="claude-opus-4-8", messages=PROBE_MESSAGES, system="s", tools=[{"name": "a"}])

    assert set(messages.seen[0]) == {"model", "messages"}
    assert set(messages.seen[1]) == {"model", "messages", "system", "tools"}
    assert messages.seen[1]["system"] == "s"


def test_a_tool_called_in_the_response_is_not_reported_as_dead():
    # A capture holds the request's history and the response it produced; the tool call
    # this turn made lives in the response, not yet echoed into messages.
    payload = {
        "request": {
            "model": "claude-opus-4-8",
            "tools": [{"name": "write_file", "input_schema": {}}, {"name": "idle", "input_schema": {}}],
            "messages": [{"role": "user", "content": "write it"}],
        },
        "response": {
            "content": [{"type": "tool_use", "id": "tu1", "name": "write_file", "input": {}}],
            "stop_reason": "tool_use",
        },
    }
    report = analyze(payload)
    assert report.dead_tools == ["idle"]
    assert {r.name for r in report.components if r.unused} == {"idle"}


def test_compare_and_ci_see_the_response_call_too():
    # Every command routes through analyze, so the fix has to hold for all of them —
    # a tool called in the response must not trip a max_dead_tools budget.
    def payload(called: bool) -> dict[str, Any]:
        return {
            "request": {
                "model": "claude-opus-4-8",
                "tools": [{"name": "write_file", "input_schema": {}}],
                "messages": [{"role": "user", "content": "go"}],
            },
            "response": {
                "content": [{"type": "tool_use", "id": "t1", "name": "write_file", "input": {}}]
                if called
                else []
            },
        }

    diff = compare_payloads(payload(False), payload(True))
    assert (diff.dead_tools_a, diff.dead_tools_b) == (["write_file"], [])
    assert check(analyze(payload(True)), Budget(max_dead_tools=0)) == []
    assert check(analyze(payload(False)), Budget(max_dead_tools=0)) != []


def test_response_tool_use_extraction_ignores_malformed_content():
    assert called_in_response(None) == set()
    assert called_in_response({"content": "not a list"}) == set()
    assert called_in_response({"content": [1, None, {"type": "text", "text": "x"}]}) == set()
    assert called_in_response({"content": [{"type": "tool_use"}]}) == set()
    assert called_in_response(
        {"content": [{"type": "mcp_tool_use", "name": "mcp__files__read"}]}
    ) == {"mcp__files__read"}


def test_two_tools_sharing_a_name_are_not_counted_twice():
    request = {
        "model": "claude-opus-4-8",
        "tools": [{"name": "dup", "input_schema": {"a": 1}}, {"name": "dup", "input_schema": {"b": 2}}],
        "messages": [{"role": "user", "content": "hi"}],
    }
    api = FakeAPI(tool_costs={"dup": 100})
    split = measure(request, "claude-opus-4-8", api)
    report = analyze({"request": request}, counter=FakeAPI(tool_costs={"dup": 100}))

    dup_rows = [r for r in report.components if r.name == "dup"]
    assert len(dup_rows) == 2
    # There is no way to tell which row owns the measurement, so neither claims it and
    # both fall back to the estimate rather than each billing the whole block.
    assert not any(r.exact for r in dup_rows)
    assert sum(r.tokens for r in dup_rows) < 2 * split.tools_total


def test_a_billed_total_of_zero_is_not_treated_as_an_exact_reconciliation():
    components = [
        Component(KIND_SYSTEM, "system", "s" * 400),
        Component(KIND_CURRENT_USER, "user[0]", "u" * 400),
    ]
    report = build_report(components, set(), set(), "claude-opus-4-8", {"input_tokens": 0})

    assert report.reconciled is False
    assert all(r.tokens > 0 for r in report.components)
    assert report.total_tokens == sum(r.tokens for r in report.components)


def test_a_rejected_usage_block_is_not_reported_as_a_missing_one():
    components = [Component(KIND_SYSTEM, "system", "s" * 100)]
    exact = ExactSplit(system=9000, tools={}, tools_total=0, tool_overhead=0, calls=2)
    report = build_report(
        components, set(), set(), "claude-opus-4-8", {"input_tokens": 50}, exact
    )

    assert (report.reconciled, report.usage_seen) == (False, True)
    assert "no usage block" not in format_report(report)
    assert "inconsistent" in format_report(report)


def test_measuring_some_rows_reports_how_wrong_the_estimate_was():
    # 400 characters of system text estimates to 100 tokens; measuring it at 200 means
    # the estimate ran half the real value, which is what the unmeasured rows inherit.
    components = [
        Component(KIND_SYSTEM, "system", "s" * 400),
        Component(KIND_CURRENT_USER, "user[0]", "u" * 400),
    ]
    exact = ExactSplit(system=200, tools={}, tools_total=0, tool_overhead=0, calls=2)
    report = build_report(components, set(), set(), "claude-opus-4-8", None, exact)

    assert report.estimator_error == pytest.approx(-0.5)
    assert "50% below the measured value" in format_report(report)


def test_the_overhead_row_is_left_out_of_the_calibration():
    # It is measured but has no text, so including it would invent an error.
    costs = {"read_file": 40, "web_search": 200}
    report = analyze({"request": _request(list(costs), system="")}, counter=FakeAPI(tool_costs=costs))
    assert report.estimator_error is not None

    without_overhead = [r for r in report.components if r.name != TOOL_BLOCK_OVERHEAD]
    assert len(without_overhead) < len(report.components)


def test_no_calibration_is_reported_without_measurement():
    report = analyze({"request": _request(["read_file"])})
    assert report.estimator_error is None
    assert "measured value" not in format_report(report)


def test_default_counter_names_the_extra_when_the_sdk_is_missing(monkeypatch):
    monkeypatch.setitem(sys.modules, "anthropic", None)
    with pytest.raises(RuntimeError, match=r"ctxprofile\[online\]"):
        default_counter()


def test_the_table_marks_measured_rows_and_says_what_is_still_estimated():
    api = FakeAPI(system_cost=85, tool_costs={"read_file": 183})
    text = format_report(analyze({"request": _request(["read_file"])}, counter=api))
    assert "system + tools measured, rest estimated" in text
    assert "* tokens measured with count_tokens" in text
    system_row = next(line for line in text.splitlines() if line.strip().startswith("system "))
    assert "85*" in system_row


def test_online_without_the_sdk_exits_with_a_message(tmp_path, monkeypatch, capsys):
    payload = tmp_path / "capture.json"
    payload.write_text(json.dumps(_request(["read_file"])), encoding="utf-8")
    monkeypatch.setitem(sys.modules, "anthropic", None)
    assert main(["analyze", str(payload), "--online"]) == 2
    assert 'ctxprofile[online]' in capsys.readouterr().err
