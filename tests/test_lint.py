from __future__ import annotations

from ctxprofile.lint import CHECK_DEAD_DEFS, CHECK_SCHEMA_META, lint_tools, slim_tools


def tool(name: str, schema: dict) -> dict:
    return {"name": name, "description": "d", "input_schema": schema}


def test_unreferenced_defs_are_reported() -> None:
    t = tool(
        "a",
        {
            "type": "object",
            "properties": {"x": {"type": "string"}},
            "$defs": {"Dead": {"type": "object", "properties": {"q": {"type": "string"}}}},
        },
    )
    report = lint_tools([t])
    assert [f.check for f in report.findings] == [CHECK_DEAD_DEFS]
    assert report.findings[0].names == ["Dead"]
    assert report.removable_tokens > 0


def test_referenced_defs_are_kept() -> None:
    t = tool(
        "a",
        {
            "type": "object",
            "properties": {"x": {"$ref": "#/$defs/Live"}},
            "$defs": {"Live": {"type": "string"}},
        },
    )
    assert lint_tools([t]).findings == []


def test_transitive_reference_keeps_a_def_alive() -> None:
    t = tool(
        "a",
        {
            "type": "object",
            "properties": {"x": {"$ref": "#/$defs/One"}},
            "$defs": {
                "One": {"type": "object", "properties": {"y": {"$ref": "#/$defs/Two"}}},
                "Two": {"type": "string"},
                "Three": {"type": "string"},
            },
        },
    )
    findings = lint_tools([t]).findings
    assert [f.names for f in findings] == [["Three"]]


def test_mutually_referencing_dead_defs_do_not_keep_each_other_alive() -> None:
    t = tool(
        "a",
        {
            "type": "object",
            "properties": {"x": {"type": "string"}},
            "$defs": {
                "A": {"$ref": "#/$defs/B"},
                "B": {"$ref": "#/$defs/A"},
            },
        },
    )
    assert lint_tools([t]).findings[0].names == ["A", "B"]


def test_recursive_reachable_def_terminates() -> None:
    t = tool(
        "a",
        {
            "type": "object",
            "properties": {"x": {"$ref": "#/$defs/Node"}},
            "$defs": {"Node": {"properties": {"child": {"$ref": "#/$defs/Node"}}}},
        },
    )
    assert lint_tools([t]).findings == []


def test_property_named_ref_is_not_a_reference() -> None:
    t = tool(
        "a",
        {
            "type": "object",
            "properties": {"$ref": {"type": "string"}},
            "$defs": {"Dead": {"type": "string"}},
        },
    )
    assert lint_tools([t]).findings[0].names == ["Dead"]


def test_unresolvable_ref_suppresses_the_check() -> None:
    t = tool(
        "a",
        {
            "type": "object",
            "properties": {"x": {"$ref": "https://example.com/schema.json#/X"}},
            "$defs": {"Maybe": {"type": "string"}},
        },
    )
    assert lint_tools([t]).findings == []


def test_dynamic_ref_suppresses_the_check() -> None:
    t = tool(
        "a",
        {
            "type": "object",
            "properties": {"x": {"$dynamicRef": "#meta"}},
            "$defs": {"Maybe": {"type": "string"}},
        },
    )
    assert lint_tools([t]).findings == []


def test_schema_meta_keys_are_reported() -> None:
    t = tool(
        "a",
        {"$schema": "http://json-schema.org/draft-07/schema#", "type": "object", "properties": {}},
    )
    report = lint_tools([t])
    assert [f.check for f in report.findings] == [CHECK_SCHEMA_META]
    assert report.findings[0].names == ["$schema"]


def test_definitions_holder_is_supported() -> None:
    t = tool(
        "a",
        {"type": "object", "properties": {}, "definitions": {"Dead": {"type": "string"}}},
    )
    assert lint_tools([t]).findings[0].names == ["Dead"]


def test_clean_schema_produces_nothing() -> None:
    report = lint_tools([tool("a", {"type": "object", "properties": {"x": {"type": "string"}}})])
    assert report.findings == []
    assert report.removable_tokens == 0


def test_slim_tools_removes_exactly_the_reported_weight() -> None:
    tools = [
        tool(
            "a",
            {
                "$schema": "http://json-schema.org/draft-07/schema#",
                "type": "object",
                "properties": {"x": {"type": "string"}},
                "$defs": {"Dead": {"type": "string"}},
            },
        )
    ]
    slim = slim_tools(tools)
    assert slim[0]["input_schema"] == {"type": "object", "properties": {"x": {"type": "string"}}}
    assert tools[0]["input_schema"]["$schema"], "input must not be mutated"


def test_slim_tools_leaves_a_clean_schema_untouched() -> None:
    tools = [tool("a", {"type": "object", "properties": {"x": {"type": "string"}}})]
    assert slim_tools(tools) == tools


def test_empty_defs_holder_is_dropped_not_left_empty() -> None:
    tools = [tool("a", {"type": "object", "properties": {}, "$defs": {"Dead": {"type": "string"}}})]
    assert "$defs" not in slim_tools(tools)[0]["input_schema"]


def test_findings_are_sorted_by_savings() -> None:
    small = tool("small", {"$schema": "x", "type": "object", "properties": {}})
    big = tool(
        "big",
        {
            "type": "object",
            "properties": {},
            "$defs": {"Dead": {"type": "object", "description": "a" * 400}},
        },
    )
    findings = lint_tools([small, big]).findings
    assert [f.tool for f in findings] == ["big", "small"]


def test_non_dict_schema_is_ignored() -> None:
    assert lint_tools([{"name": "a", "input_schema": "not a schema"}]).findings == []


def test_missing_schema_is_ignored() -> None:
    assert lint_tools([{"name": "a"}]).findings == []


def test_unnamed_tool_gets_the_shared_fallback_label() -> None:
    t = {"input_schema": {"type": "object", "properties": {}, "$defs": {"D": {"type": "string"}}}}
    assert lint_tools([t]).findings[0].tool == "<unnamed>"


def test_total_is_the_all_at_once_delta_not_the_sum_of_parts() -> None:
    t = tool(
        "a",
        {
            "$schema": "x",
            "type": "object",
            "properties": {},
            "$defs": {"Dead": {"type": "string"}},
        },
    )
    report = lint_tools([t])
    assert len(report.findings) == 2
    assert report.removable_tokens > 0
    assert report.baseline_tokens > report.removable_tokens


def test_measured_totals_override_the_estimate() -> None:
    t = tool("a", {"type": "object", "properties": {}, "$defs": {"Dead": {"type": "string"}}})
    report = lint_tools([t], measure=lambda tools: 1000 if len(str(tools)) > 90 else 400)
    assert report.exact is True
    assert report.baseline_tokens == 1000
    assert report.removable_tokens == 600


def test_measurement_that_reports_no_saving_is_reported_honestly() -> None:
    t = tool("a", {"type": "object", "properties": {}, "$defs": {"Dead": {"type": "string"}}})
    report = lint_tools([t], measure=lambda tools: 500)
    assert report.exact is True
    assert report.removable_tokens == 0
    assert report.findings, "the finding still stands even when the measured delta is zero"


def test_escaped_pointer_names_are_resolved() -> None:
    t = tool(
        "a",
        {
            "type": "object",
            "properties": {"x": {"$ref": "#/$defs/a~1b"}},
            "$defs": {"a/b": {"type": "string"}, "Dead": {"type": "string"}},
        },
    )
    assert lint_tools([t]).findings[0].names == ["Dead"]


def test_tilde_escape_is_resolved() -> None:
    t = tool(
        "a",
        {
            "type": "object",
            "properties": {"x": {"$ref": "#/$defs/a~0b"}},
            "$defs": {"a~b": {"type": "string"}},
        },
    )
    assert lint_tools([t]).findings == []


def test_a_ref_into_a_definition_keeps_the_whole_definition() -> None:
    t = tool(
        "a",
        {
            "type": "object",
            "properties": {"x": {"$ref": "#/$defs/Shape/properties/inner"}},
            "$defs": {"Shape": {"properties": {"inner": {"$ref": "#/$defs/Inner"}}},
                      "Inner": {"type": "string"}},
        },
    )
    assert lint_tools([t]).findings == []


def test_a_reference_across_the_two_holders_counts() -> None:
    t = tool(
        "a",
        {
            "type": "object",
            "properties": {"x": {"$ref": "#/definitions/Live"}},
            "definitions": {"Live": {"$ref": "#/$defs/AlsoLive"}},
            "$defs": {"AlsoLive": {"type": "string"}},
        },
    )
    assert lint_tools([t]).findings == []


def test_anchor_is_kept_when_something_could_point_at_it() -> None:
    schema = {
        "type": "object",
        "$anchor": "root",
        "properties": {"x": {"$ref": "#root"}},
    }
    assert slim_tools([tool("a", schema)])[0]["input_schema"]["$anchor"] == "root"


def test_id_is_kept_when_a_reference_could_resolve_against_it() -> None:
    schema = {
        "type": "object",
        "$id": "https://example.com/base.json",
        "properties": {"x": {"$ref": "other.json"}},
    }
    assert slim_tools([tool("a", schema)])[0]["input_schema"]["$id"]


def test_schema_and_comment_are_always_removable() -> None:
    schema = {
        "type": "object",
        "$schema": "http://json-schema.org/draft-07/schema#",
        "$comment": "generated",
        "properties": {"x": {"$ref": "other.json"}},
    }
    slim = slim_tools([tool("a", schema)])[0]["input_schema"]
    assert "$schema" not in slim
    assert "$comment" not in slim
