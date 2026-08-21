from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

from ctxprofile.cli import main
from ctxprofile.collect import build_request, describe, run

CONFIG = {
    "mcpServers": {
        "good": {"command": "npx", "args": ["-y", "some-server"]},
        "remote": {"url": "https://example.com/mcp"},
        "broken": {"command": "npx", "args": ["-y", "missing"]},
    }
}


async def fake_lister(name: str, spec: dict, timeout: float) -> list[dict]:
    if name == "broken":
        raise RuntimeError("server exited before listing tools")
    return [{"name": f"mcp__{name}__do", "description": "d", "input_schema": {"type": "object"}}]


def test_a_broken_server_does_not_lose_the_working_ones() -> None:
    request, skipped = run(CONFIG, report=lambda _: None, lister=fake_lister)
    assert [tool["name"] for tool in request["tools"]] == ["mcp__good__do"]
    assert skipped == ["remote", "broken"]


def test_every_skip_is_reported_with_its_reason() -> None:
    lines: list[str] = []
    run(CONFIG, report=lines.append, lister=fake_lister)
    assert "  remote: skipped (not a stdio server)" in lines
    assert "  broken: skipped (RuntimeError: server exited before listing tools)" in lines
    assert "  good: 1 tools" in lines


def test_an_empty_config_yields_an_empty_request() -> None:
    request, skipped = run({}, report=lambda _: None, lister=fake_lister)
    assert request["tools"] == []
    assert skipped == []


def test_the_request_is_shaped_for_the_rest_of_the_cli() -> None:
    request = build_request([], model="claude-sonnet-5")
    assert request["model"] == "claude-sonnet-5"
    assert request["messages"], "count_tokens and analyze both need a non-empty messages array"


def test_a_task_group_error_is_flattened_to_the_real_cause() -> None:
    group = BaseExceptionGroup("task group", [ValueError("port already in use")])
    assert describe(group) == "ValueError: port already in use"


def test_collect_writes_a_request_and_flags_an_incomplete_run(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr("ctxprofile.collect.list_tools", fake_lister)
    config = tmp_path / "servers.json"
    config.write_text(json.dumps(CONFIG), encoding="utf-8")
    out = tmp_path / "mine.json"

    assert main(["collect", str(config), "-o", str(out)]) == 1, "a partial collection is not clean"
    written = json.loads(out.read_text(encoding="utf-8"))
    assert [tool["name"] for tool in written["tools"]] == ["mcp__good__do"]
    assert "incomplete: 2 server(s)" in capsys.readouterr().out


def test_collect_exits_clean_when_every_server_answers(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("ctxprofile.collect.list_tools", fake_lister)
    config = tmp_path / "servers.json"
    config.write_text(json.dumps({"mcpServers": {"good": {"command": "npx"}}}), encoding="utf-8")
    assert main(["collect", str(config), "-o", str(tmp_path / "mine.json")]) == 0


def test_collect_says_which_extra_to_install_when_the_sdk_is_missing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setitem(sys.modules, "mcp", None)
    config = tmp_path / "servers.json"
    config.write_text(json.dumps({"mcpServers": {"good": {"command": "npx"}}}), encoding="utf-8")
    out = tmp_path / "mine.json"

    assert main(["collect", str(config), "-o", str(out)]) == 2
    assert 'pip install "ctxprofile[mcp]"' in capsys.readouterr().err
    assert not out.exists(), "a run that cannot talk to any server must not write a request"


class _Tool:
    def __init__(self, field: str) -> None:
        self.name = "read"
        self.description = "read a file"
        setattr(self, field, {"type": "object", "properties": {"path": {"type": "string"}}})


@pytest.mark.parametrize("field", ["inputSchema", "input_schema"])
def test_both_sdk_generations_of_the_tool_model_are_read(field: str) -> None:
    from ctxprofile.collect import tool_definition

    definition = tool_definition("files", _Tool(field))
    assert definition["name"] == "mcp__files__read"
    assert definition["input_schema"]["properties"] == {"path": {"type": "string"}}


def test_a_tool_without_a_schema_still_produces_a_valid_definition() -> None:
    from ctxprofile.collect import tool_definition

    class Bare:
        name = "ping"
        description = None

    assert tool_definition("s", Bare())["input_schema"] == {"type": "object"}
    assert tool_definition("s", Bare())["description"] == ""
