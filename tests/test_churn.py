from __future__ import annotations

from typing import Any

from ctxprofile.churn import analyze_churn, first_divergence, prefix_signature
from ctxprofile.models import KIND_HISTORY, KIND_SYSTEM, KIND_TOOL_DEF


def capture(
    at: str,
    *,
    system: str = "you are helpful",
    tools: list[str] | None = None,
    messages: list[str] | None = None,
    created: int = 0,
    read: int = 0,
    model: str = "claude-opus-4-8",
) -> dict[str, Any]:
    return {
        "captured_at": at,
        "request": {
            "model": model,
            "system": system,
            "tools": [{"name": name, "input_schema": {"type": "object"}} for name in (tools or [])],
            "messages": [{"role": "user", "content": text} for text in (messages or ["hi"])],
        },
        "response": {
            "usage": {
                "input_tokens": 10,
                "cache_creation_input_tokens": created,
                "cache_read_input_tokens": read,
            }
        },
    }


def test_the_signature_follows_the_api_render_order():
    # tools render before system, and system before messages; a change early in that
    # order invalidates everything after it, which is what attribution depends on.
    request = capture("t", system="sys", tools=["a", "b"], messages=["one"])["request"]
    kinds = [kind for kind, _, _ in prefix_signature(request)]
    assert kinds == ["tool_def", "tool_def", "system", "history"]


def test_appending_a_message_is_not_a_divergence():
    before = prefix_signature(capture("t", messages=["one"])["request"])
    after = prefix_signature(capture("t", messages=["one", "two"])["request"])
    assert first_divergence(before, after) is None


def test_editing_an_earlier_message_is_a_divergence():
    before = prefix_signature(capture("t", messages=["one", "two"])["request"])
    after = prefix_signature(capture("t", messages=["EDITED", "two"])["request"])
    assert first_divergence(before, after) == ("history", "user[0]")


def test_the_earliest_change_in_render_order_wins():
    # Both the tool and the system prompt changed; the tool renders first, so it is
    # what actually invalidated the prefix.
    before = prefix_signature(capture("t", system="old", tools=["a"])["request"])
    after = prefix_signature(capture("t", system="new", tools=["b"])["request"])
    # tools render before the system prompt, so the tool slot wins; naming both
    # ends is the honest description of a slot whose occupant was replaced.
    assert first_divergence(before, after) == ("tool_def", "a -> b")


def test_a_rebuild_after_an_edit_is_attributed_to_the_component_that_changed():
    captures = [
        capture("2026-08-09T10:00:00+00:00", system="old", read=500),
        capture("2026-08-09T10:00:30+00:00", system="new", created=500),
    ]
    report = analyze_churn(captures)

    assert len(report.events) == 1
    event = report.events[0]
    assert (event.cause, event.kind) == ("system", "system")
    assert event.rebuilt_tokens == 500
    # A rebuild costs the write rate where a hit would have cost the read rate; the
    # excess is the difference, which is the part the change actually wasted.
    assert event.excess_usd > 0
    assert report.total_excess_usd == event.excess_usd


def test_an_unchanged_prefix_after_a_long_gap_is_a_ttl_expiry_not_your_fault():
    captures = [
        capture("2026-08-09T10:00:00+00:00", read=500),
        capture("2026-08-09T10:40:00+00:00", created=500),
    ]
    report = analyze_churn(captures)
    assert [e.cause for e in report.events] == ["ttl expiry"]
    assert report.events[0].gap_seconds == 2400.0


def test_an_unchanged_prefix_inside_the_ttl_is_reported_as_unexplained():
    captures = [
        capture("2026-08-09T10:00:00+00:00", read=500),
        capture("2026-08-09T10:01:00+00:00", created=500),
    ]
    report = analyze_churn(captures)
    # Refusing to guess is the point: nothing in the capture explains this one.
    assert [e.cause for e in report.events] == ["unexplained"]


def test_switching_model_is_reported_as_its_own_cause():
    captures = [
        capture("2026-08-09T10:00:00+00:00", read=500),
        capture("2026-08-09T10:00:10+00:00", created=500, model="claude-sonnet-5"),
    ]
    report = analyze_churn(captures)
    assert [e.cause for e in report.events] == ["model change"]


def test_no_rebuild_produces_no_event():
    captures = [
        capture("2026-08-09T10:00:00+00:00", read=500),
        capture("2026-08-09T10:00:10+00:00", read=500),
    ]
    report = analyze_churn(captures)
    assert report.events == []
    assert report.total_excess_usd == 0.0


def test_captures_are_ordered_by_time_not_by_argument_order():
    later = capture("2026-08-09T10:05:00+00:00", system="new", created=500)
    earlier = capture("2026-08-09T10:00:00+00:00", system="old", read=500)
    report = analyze_churn([later, earlier])
    assert [e.cause for e in report.events] == ["system"]


def test_captures_without_a_timestamp_or_usage_are_skipped_not_guessed():
    usable = capture("2026-08-09T10:00:00+00:00", read=500)
    no_stamp = capture("2026-08-09T10:01:00+00:00", created=500)
    del no_stamp["captured_at"]
    report = analyze_churn([usable, no_stamp])
    assert report.captures == 1
    assert report.events == []


def test_a_single_capture_has_nothing_to_compare():
    report = analyze_churn([capture("2026-08-09T10:00:00+00:00", created=500)])
    assert (report.captures, report.events) == (1, [])


def test_a_naive_timestamp_next_to_an_aware_one_does_not_break_the_corpus():
    aware = capture("2026-08-09T10:00:00+00:00", system="old", read=500)
    naive = capture("2026-08-09T10:00:30", system="new", created=500)
    report = analyze_churn([aware, naive])
    assert report.captures == 2
    assert [e.cause for e in report.events] == ["system"]


def _capture(at: str, *, messages, usage, system="you are careful", tools=None, model="claude-opus-4-8"):
    request = {"model": model, "system": system, "messages": messages}
    if tools is not None:
        request["tools"] = tools
    return {"captured_at": at, "request": request, "response": {"usage": usage}}


def _usage(created: int, read: int = 0) -> dict:
    return {
        "input_tokens": 5,
        "cache_creation_input_tokens": created,
        "cache_read_input_tokens": read,
    }


def test_a_prefix_that_was_served_from_cache_is_not_a_rebuild() -> None:
    """Appending new content writes it to cache; that is the cache working, not churn."""
    first = _capture("2026-08-09T11:40:00+00:00", messages=[{"role": "user", "content": "one"}],
                     usage=_usage(2100))
    second = _capture("2026-08-09T11:40:20+00:00",
                      messages=[{"role": "user", "content": "one"},
                                {"role": "assistant", "content": "a"},
                                {"role": "user", "content": "two"}],
                      usage=_usage(20000, read=2100))
    report = analyze_churn([first, second])
    assert report.events == []
    assert report.total_excess_usd == 0.0


def test_a_prefix_that_was_not_served_is_a_rebuild_of_only_what_was_lost() -> None:
    first = _capture("2026-08-09T11:40:00+00:00", messages=[{"role": "user", "content": "one"}],
                     usage=_usage(2100))
    second = _capture("2026-08-09T11:40:20+00:00", messages=[{"role": "user", "content": "one"}],
                      system="a different system prompt", usage=_usage(2300, read=0))
    report = analyze_churn([first, second])
    assert len(report.events) == 1
    event = report.events[0]
    assert event.kind == KIND_SYSTEM
    assert event.rebuilt_tokens == 2100, "only the previously cached part was rebuilt"
    assert event.excess_usd > 0


def test_moving_a_cache_breakpoint_forward_is_not_a_content_change() -> None:
    """The documented multi-turn pattern rotates the ephemeral marker onto the newest turn."""
    def block(text: str, mark: bool) -> dict:
        content = {"type": "text", "text": text}
        if mark:
            content["cache_control"] = {"type": "ephemeral"}
        return {"role": "user", "content": [content]}

    before = prefix_signature({"messages": [block("one", True)]})
    after = prefix_signature({"messages": [block("one", False), block("two", True)]})
    assert first_divergence(before, after) is None


def test_removing_a_tool_names_the_tool_that_left() -> None:
    def tool(name: str) -> dict:
        return {"name": name, "input_schema": {"type": "object"}}

    before = prefix_signature({"tools": [tool("alpha"), tool("beta"), tool("gamma")]})
    after = prefix_signature({"tools": [tool("beta"), tool("gamma")]})
    kind, cause = first_divergence(before, after)
    assert kind == KIND_TOOL_DEF
    assert "alpha" in cause, f"named {cause!r}, but beta and gamma are byte-identical"


def test_removing_the_system_prompt_is_reported_as_a_system_change() -> None:
    tools = [{"name": "search", "input_schema": {"type": "object"}}]
    before = prefix_signature({"tools": tools, "system": "s", "messages": [{"role": "user", "content": "x"}]})
    after = prefix_signature({"tools": tools, "messages": [{"role": "user", "content": "x"}]})
    kind, cause = first_divergence(before, after)
    assert kind == KIND_SYSTEM
    assert "system" in cause


def test_dropping_a_message_from_the_middle_names_the_message_that_left() -> None:
    before = prefix_signature({"messages": [{"role": "user", "content": "a"},
                                            {"role": "assistant", "content": "b"},
                                            {"role": "user", "content": "c"}]})
    after = prefix_signature({"messages": [{"role": "user", "content": "a"},
                                           {"role": "user", "content": "c"}]})
    kind, cause = first_divergence(before, after)
    assert kind == KIND_HISTORY
    assert "assistant[1]" in cause


def test_inserting_a_tool_at_the_front_is_still_a_divergence() -> None:
    def tool(name: str) -> dict:
        return {"name": name, "input_schema": {"type": "object"}}

    before = prefix_signature({"tools": [tool("beta")]})
    after = prefix_signature({"tools": [tool("alpha"), tool("beta")]})
    kind, cause = first_divergence(before, after)
    assert kind == KIND_TOOL_DEF
    assert "alpha" in cause


def test_a_one_hour_cache_is_not_called_a_ttl_expiry_after_six_minutes() -> None:
    long_cache = [{"type": "text", "text": "s", "cache_control": {"type": "ephemeral", "ttl": "1h"}}]
    first = _capture("2026-08-09T11:40:00+00:00", messages=[{"role": "user", "content": "one"}],
                     system=long_cache, usage=_usage(2100))
    second = _capture("2026-08-09T11:46:30+00:00", messages=[{"role": "user", "content": "one"}],
                      system=long_cache, usage=_usage(2100, read=0))
    report = analyze_churn([first, second])
    assert len(report.events) == 1
    assert report.events[0].cause == "unexplained", "a 1h cache had not expired after 6.5 minutes"
