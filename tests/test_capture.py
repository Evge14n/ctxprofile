from __future__ import annotations

import json
import threading
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

from ctxprofile.capture import build_record, serve, write_capture

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
