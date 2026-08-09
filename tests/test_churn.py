from __future__ import annotations

from typing import Any

from ctxprofile.churn import analyze_churn, first_divergence, prefix_signature


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
    assert first_divergence(before, after) == ("tool_def", "b")


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
