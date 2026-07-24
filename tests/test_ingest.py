from __future__ import annotations

import json
from pathlib import Path

from ctxprofile.ingest import parse_request
from ctxprofile.models import (
    KIND_CURRENT_USER,
    KIND_HISTORY,
    KIND_SYSTEM,
    KIND_TOOL_DEF,
    KIND_TOOL_RESULT,
)

FIXTURE = Path(__file__).parent / "fixtures" / "capture.json"


def _request() -> dict:
    return json.loads(FIXTURE.read_text(encoding="utf-8"))["request"]


def test_defined_and_called_tools() -> None:
    _, defined, called = parse_request(_request())
    assert defined == {"read_file", "write_file", "web_search"}
    assert called == {"read_file"}


def test_component_kinds() -> None:
    components, _, _ = parse_request(_request())
    kinds = [c.kind for c in components]
    assert kinds.count(KIND_TOOL_DEF) == 3
    assert KIND_SYSTEM in kinds
    assert KIND_TOOL_RESULT in kinds
    assert KIND_HISTORY in kinds
    assert kinds.count(KIND_CURRENT_USER) == 1


def test_current_user_is_last_message() -> None:
    components, _, _ = parse_request(_request())
    current = [c for c in components if c.kind == KIND_CURRENT_USER]
    assert len(current) == 1
    assert "summarise" in current[0].text
