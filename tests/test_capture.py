from __future__ import annotations

import json
import threading
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

from ctxprofile.capture import build_record, reassemble_sse, serve, write_capture

SSE = (
    'event: message_start\n'
    'data: {"type":"message_start","message":{"id":"msg_s","model":"claude-opus-4-8",'
    '"usage":{"input_tokens":50,"cache_read_input_tokens":10,"output_tokens":1}}}\n\n'
    'event: content_block_start\n'
    'data: {"type":"content_block_start","index":0,"content_block":{"type":"text","text":""}}\n\n'
    'event: content_block_delta\n'
    'data: {"type":"content_block_delta","index":0,"delta":{"type":"text_delta","text":"Hello"}}\n\n'
    'event: content_block_delta\n'
    'data: {"type":"content_block_delta","index":0,"delta":{"type":"text_delta","text":" world"}}\n\n'
    'event: message_delta\n'
    'data: {"type":"message_delta","delta":{"stop_reason":"end_turn"},"usage":{"output_tokens":8}}\n\n'
    'event: message_stop\n'
    'data: {"type":"message_stop"}\n'
)

CANNED = {
    "id": "msg_test",
    "type": "message",
    "role": "assistant",
    "content": [{"type": "text", "text": "hi"}],
    "usage": {"input_tokens": 5, "output_tokens": 2},
}


def test_build_record_json() -> None:
    record = build_record("/v1/messages", b'{"model": "m"}', b'{"id": "msg_1"}', "2026-07-24T00:00:00+00:00")
    assert record["request"] == {"model": "m"}
    assert record["response"] == {"id": "msg_1"}
    assert record["path"] == "/v1/messages"
    assert record["captured_at"] == "2026-07-24T00:00:00+00:00"


def test_build_record_streaming_kept_raw() -> None:
    record = build_record("/v1/messages", b'{"model": "m"}', b"event: message_start\ndata: {}", "t")
    assert "response" not in record
    assert "response_raw" in record


def test_write_capture_uses_message_id(tmp_path: Path) -> None:
    record = build_record("/v1/messages", b"{}", b'{"id": "msg_abc"}', "2026-07-24T00:00:00")
    path = write_capture(tmp_path, record)
    assert "msg_abc" in path.name


def test_write_capture_sanitizes_malicious_id(tmp_path: Path) -> None:
    record = build_record("/v1/messages", b"{}", b'{"id": "../../../../evil"}', "2026-07-24T00:00:00")
    path = write_capture(tmp_path, record)
    assert tmp_path.resolve() in path.resolve().parents
    assert ".." not in path.name


def test_reassemble_sse_merges_usage_and_text() -> None:
    message = reassemble_sse(SSE)
    assert message is not None
    assert message["id"] == "msg_s"
    assert message["usage"]["input_tokens"] == 50
    assert message["usage"]["cache_read_input_tokens"] == 10
    assert message["usage"]["output_tokens"] == 8
    assert message["content"][0]["text"] == "Hello world"


def test_build_record_reassembles_a_stream() -> None:
    record = build_record("/v1/messages", b'{"model": "m"}', SSE.encode("utf-8"), "t")
    assert record.get("response_reassembled") is True
    assert record["response"]["usage"]["output_tokens"] == 8
    assert "response_raw" not in record


def test_build_record_keeps_non_sse_raw() -> None:
    record = build_record("/v1/messages", b"{}", b"neither json nor sse", "t")
    assert "response_raw" in record
    assert "response" not in record


class _MockUpstream(BaseHTTPRequestHandler):
    def log_message(self, *_: Any) -> None:
        return

    def do_POST(self) -> None:
        self.rfile.read(int(self.headers.get("Content-Length", 0)))
        body = json.dumps(CANNED).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


def test_proxy_relays_and_captures(tmp_path: Path) -> None:
    upstream = ThreadingHTTPServer(("127.0.0.1", 0), _MockUpstream)
    upstream_port = upstream.server_address[1]
    threading.Thread(target=upstream.serve_forever, daemon=True).start()

    proxy = serve(0, tmp_path, "127.0.0.1", upstream_port, use_tls=False)
    proxy_port = proxy.server_address[1]
    threading.Thread(target=proxy.serve_forever, daemon=True).start()

    try:
        payload = json.dumps({"model": "claude-opus-4-8", "messages": [{"role": "user", "content": "hi"}]})
        request = urllib.request.Request(
            f"http://127.0.0.1:{proxy_port}/v1/messages",
            data=payload.encode("utf-8"),
            headers={"Content-Type": "application/json", "x-api-key": "secret-key"},
            method="POST",
        )
        with urllib.request.urlopen(request, timeout=10) as response:
            relayed = json.loads(response.read())
        assert relayed["id"] == "msg_test"

        files = list(Path(tmp_path).glob("*.json"))
        assert len(files) == 1
        text = files[0].read_text(encoding="utf-8")
        record = json.loads(text)
        assert record["request"]["model"] == "claude-opus-4-8"
        assert record["response"]["id"] == "msg_test"
        assert "secret-key" not in text
    finally:
        proxy.shutdown()
        upstream.shutdown()
