"""The measurement path over real HTTP, against a local count_tokens server.

test_online.py substitutes a counter function, which proves the arithmetic but skips
everything between it and the network: whether the SDK accepts these keyword arguments,
whether `system` and `tools` survive serialization, and whether the endpoint path and
the leave-one-out request pattern are what this code intends. A server here answers
those without an API key, and fails loudly if the SDK's contract moves underneath us.
"""

from __future__ import annotations

import json
import threading
from collections.abc import Iterator
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any

import pytest

anthropic = pytest.importorskip("anthropic", reason='needs pip install "ctxprofile[online]"')

from ctxprofile.online import anthropic_counter, measure, planned_calls

ENVELOPE = 11
PER_MESSAGE = 3
TOOL_PREAMBLE = 7
SYSTEM_COST = 63
# Deliberately unrelated to schema length, so the offline heuristic cannot fake a pass.
TOOL_COST = {"read_file": 181, "web_search": 900, "write_file": 44}

REQUEST: dict[str, Any] = {
    "model": "claude-opus-4-8",
    "system": "you are a careful assistant",
    "tools": [
        {"name": name, "description": name, "input_schema": {"type": "object"}}
        for name in TOOL_COST
    ],
    "messages": [{"role": "user", "content": "hello"}],
}


def _count(body: dict[str, Any]) -> int:
    total = ENVELOPE + PER_MESSAGE * len(body.get("messages") or [])
    if body.get("system"):
        total += SYSTEM_COST
    tools = body.get("tools") or []
    if tools:
        total += TOOL_PREAMBLE + sum(TOOL_COST[t["name"]] for t in tools)
    return total


def _serve(handler: type[BaseHTTPRequestHandler]) -> Iterator[str]:
    server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_address[1]}"
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


@pytest.fixture
def stub() -> Iterator[tuple[str, list[dict[str, Any]]]]:
    received: list[dict[str, Any]] = []

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self) -> None:
            length = int(self.headers.get("Content-Length", "0"))
            body = json.loads(self.rfile.read(length) or b"{}")
            received.append({"path": self.path, "body": body})
            payload = json.dumps({"input_tokens": _count(body)}).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)

        def log_message(self, *args: object) -> None:
            pass

    for base_url in _serve(Handler):
        yield base_url, received


def _counter(base_url: str, **kwargs: Any) -> Any:
    return anthropic_counter(anthropic.Anthropic(api_key="stub-key", base_url=base_url, **kwargs))


def test_measured_values_are_correct_over_the_wire(stub):
    base_url, _ = stub
    split = measure(REQUEST, "claude-opus-4-8", _counter(base_url))

    assert split.system == SYSTEM_COST
    assert split.tools == TOOL_COST
    assert split.tool_overhead == TOOL_PREAMBLE
    assert sum(split.tools.values()) + split.tool_overhead == split.tools_total


def test_the_wire_carries_the_leave_one_out_pattern(stub):
    base_url, received = stub
    split = measure(REQUEST, "claude-opus-4-8", _counter(base_url))

    assert len(received) == split.calls == planned_calls(REQUEST)
    assert {r["path"] for r in received} == {"/v1/messages/count_tokens"}

    shapes = [
        (bool(r["body"].get("system")), tuple(t["name"] for t in (r["body"].get("tools") or [])))
        for r in received
    ]
    names = tuple(TOOL_COST)
    assert shapes == [
        (False, ()),                        # floor
        (True, ()),                         # system marginal
        (False, names),                     # the whole tool block
        (False, names[1:]),                 # then each tool left out in turn
        (False, (names[0], names[2])),
        (False, names[:2]),
    ]
    # Schemas must survive serialization, or the counts would be of an empty shell.
    for row in received:
        assert row["body"]["model"] == "claude-opus-4-8"
        for tool in row["body"].get("tools") or []:
            assert tool["input_schema"] == {"type": "object"}


def test_an_api_error_becomes_a_clean_message_not_a_traceback():
    class Failing(BaseHTTPRequestHandler):
        def do_POST(self) -> None:
            body = json.dumps(
                {"type": "error", "error": {"type": "authentication_error", "message": "bad key"}}
            ).encode()
            self.send_response(401)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *args: object) -> None:
            pass

    for base_url in _serve(Failing):
        counter = _counter(base_url, max_retries=0)
        # RuntimeError is in the CLI's handled set, so the user sees "error: ..." and
        # exit 2 rather than an SDK stack trace.
        with pytest.raises(RuntimeError, match="count_tokens failed"):
            measure(REQUEST, "claude-opus-4-8", counter)
