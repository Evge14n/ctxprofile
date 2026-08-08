from __future__ import annotations

import json
import os
import re
import sys
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
        if not isinstance(event, dict):
            continue
        # Fields are read defensively: this text is whatever the upstream sent, and a
        # surprise here must not cost the caller a response they already paid for.
        etype = event.get("type")
        index = event.get("index", 0)
        if etype in ("content_block_start", "content_block_delta") and not isinstance(index, int):
            continue
        if etype == "message_start":
            if isinstance(event.get("message"), dict):
                message = dict(event["message"])
        elif etype == "content_block_start":
            start = event.get("content_block")
            blocks[index] = dict(start) if isinstance(start, dict) else {}
        elif etype == "content_block_delta":
            delta = event.get("delta")
            if not isinstance(delta, dict):
                continue
            if delta.get("type") == "text_delta" and isinstance(delta.get("text"), str):
                block = blocks.setdefault(index, {"type": "text", "text": ""})
                block["text"] = str(block.get("text", "")) + delta["text"]
            elif delta.get("type") == "input_json_delta" and isinstance(
                delta.get("partial_json"), str
            ):
                partial[index] = partial.get(index, "") + delta["partial_json"]
        elif etype == "message_delta" and message is not None:
            usage = event.get("usage")
            if isinstance(usage, dict):
                current = message.get("usage")
                message["usage"] = {**current, **usage} if isinstance(current, dict) else dict(usage)
            delta = event.get("delta")
            if isinstance(delta, dict) and "stop_reason" in delta:
                message["stop_reason"] = delta["stop_reason"]

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
            try:
                conn.request("POST", self.path, body=request_bytes, headers=headers)
                upstream = conn.getresponse()
                response_bytes = upstream.read()
                status = upstream.status
                out_headers = [
                    (key, value)
                    for key, value in upstream.getheaders()
                    if key.lower() not in _HOP_BY_HOP
                ]
            except OSError as error:
                self.send_error(502, f"upstream request failed: {error}")
                return
            finally:
                conn.close()

            if self.path.rstrip("/").endswith("/v1/messages"):
                captured_at = datetime.now(UTC).isoformat()
                try:
                    write_capture(
                        out_dir,
                        build_record(self.path, request_bytes, response_bytes, captured_at),
                    )
                except Exception as error:  # noqa: BLE001 - never lose a paid-for response
                    # The caller has already been billed for this response. A malformed
                    # stream or an unwritable output directory must cost them a capture,
                    # not the answer.
                    print(f"ctxprofile: capture failed ({error})", file=sys.stderr)

            self.send_response(status)
            for key, value in out_headers:
                self.send_header(key, value)
            self.send_header("Content-Length", str(len(response_bytes)))
            self.end_headers()
            self.wfile.write(response_bytes)

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
