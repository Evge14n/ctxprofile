from __future__ import annotations

import json
import os
import re
from datetime import UTC, datetime
from http.client import HTTPConnection, HTTPSConnection
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

# Only request/response bodies are stored, never headers, so the API key never
# lands on disk. Captures still hold plaintext prompts and model output — treat
# the capture directory as secret.
_HOP_BY_HOP = {"host", "content-length", "connection", "transfer-encoding", "accept-encoding"}


def _safe_json(raw: bytes) -> Any:
    try:
        return json.loads(raw.decode("utf-8"))
    except (json.JSONDecodeError, UnicodeDecodeError):
        return None


def reassemble_sse(text: str) -> dict[str, Any] | None:
    """Rebuild a Message from an Anthropic streaming (SSE) response so its usage
    and content are usable. The input-side usage comes from message_start and the
    final output_tokens from message_delta; text and tool_use blocks are rejoined
    from their deltas. Returns None if the text is not a recognizable stream."""
    message: dict[str, Any] | None = None
    blocks: dict[int, dict[str, Any]] = {}
    partial: dict[int, str] = {}

    for line in text.splitlines():
        stripped = line.strip()
        if not stripped.startswith("data:"):
            continue
        try:
            event = json.loads(stripped[5:].strip())
        except json.JSONDecodeError:
            continue
        etype = event.get("type")
        if etype == "message_start":
            message = dict(event.get("message") or {})
        elif etype == "content_block_start":
            blocks[int(event.get("index", 0))] = dict(event.get("content_block") or {})
        elif etype == "content_block_delta":
            index = int(event.get("index", 0))
            delta = event.get("delta") or {}
            if delta.get("type") == "text_delta":
                block = blocks.setdefault(index, {"type": "text", "text": ""})
                block["text"] = block.get("text", "") + delta.get("text", "")
            elif delta.get("type") == "input_json_delta":
                partial[index] = partial.get(index, "") + delta.get("partial_json", "")
        elif etype == "message_delta" and message is not None:
            message.setdefault("usage", {}).update(event.get("usage") or {})
            if "stop_reason" in (event.get("delta") or {}):
                message["stop_reason"] = event["delta"]["stop_reason"]

    if message is None:
        return None
    for index, raw in partial.items():
        if blocks.get(index, {}).get("type") == "tool_use":
            try:
                blocks[index]["input"] = json.loads(raw) if raw else {}
            except json.JSONDecodeError:
                blocks[index]["input"] = {}
    if blocks:
        message["content"] = [blocks[i] for i in sorted(blocks)]
    return message


def build_record(
    path: str, request_bytes: bytes, response_bytes: bytes, captured_at: str
) -> dict[str, Any]:
    request = _safe_json(request_bytes)
    response = _safe_json(response_bytes)
    record: dict[str, Any] = {
        "ctxprofile_capture_version": 1,
        "captured_at": captured_at,
        "path": path,
        "request": request,
    }
    if response is not None:
        record["response"] = response
        return record
    text = response_bytes.decode("utf-8", "replace")
    reassembled = reassemble_sse(text)
    if reassembled is not None:
        record["response"] = reassembled
        record["response_reassembled"] = True
    else:
        record["response_raw"] = text
    return record


def _capture_name(record: dict[str, Any], captured_at: str) -> str:
    stamp = captured_at.replace(":", "").replace("-", "").replace(".", "")
    response = record.get("response") or {}
    msg_id = response.get("id") if isinstance(response, dict) else None
    # The id comes from the upstream response body, so strip it to a safe token
    # before it goes anywhere near a filename.
    safe = re.sub(r"[^A-Za-z0-9_-]", "", str(msg_id or ""))[:64] or "req"
    return f"{stamp}-{safe}.json"


def write_capture(out_dir: str | Path, record: dict[str, Any]) -> Path:
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    path = out / _capture_name(record, str(record.get("captured_at", "")))
    if out.resolve() not in path.resolve().parents:
        raise ValueError("refusing to write a capture outside the output directory")
    path.write_text(json.dumps(record, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    try:
        os.chmod(path, 0o600)
    except OSError:
        pass
    return path


def _make_handler(
    out_dir: str | Path, upstream_host: str, upstream_port: int, use_tls: bool
) -> type[BaseHTTPRequestHandler]:
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_: Any) -> None:  # keep the proxy quiet
            return

        def do_POST(self) -> None:
            self.close_connection = True
            try:
                length = int(self.headers.get("Content-Length", 0))
            except ValueError:
                self.send_error(400, "invalid Content-Length")
                return
            request_bytes = self.rfile.read(length)
            headers = {
                key: value
                for key, value in self.headers.items()
                if key.lower() not in _HOP_BY_HOP
            }
            conn_cls = HTTPSConnection if use_tls else HTTPConnection
            conn = conn_cls(upstream_host, upstream_port, timeout=600)
            conn.request("POST", self.path, body=request_bytes, headers=headers)
            upstream = conn.getresponse()
            response_bytes = upstream.read()

            if self.path.rstrip("/").endswith("/v1/messages"):
                captured_at = datetime.now(UTC).isoformat()
                write_capture(out_dir, build_record(self.path, request_bytes, response_bytes, captured_at))

            self.send_response(upstream.status)
            for key, value in upstream.getheaders():
                if key.lower() not in _HOP_BY_HOP:
                    self.send_header(key, value)
            self.send_header("Content-Length", str(len(response_bytes)))
            self.end_headers()
            self.wfile.write(response_bytes)
            conn.close()

    return Handler


def serve(
    port: int,
    out_dir: str | Path,
    upstream_host: str = "api.anthropic.com",
    upstream_port: int = 443,
    use_tls: bool = True,
) -> ThreadingHTTPServer:
    handler = _make_handler(out_dir, upstream_host, upstream_port, use_tls)
    return ThreadingHTTPServer(("127.0.0.1", port), handler)
