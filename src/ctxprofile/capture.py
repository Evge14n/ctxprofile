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
    else:
        # Streaming (SSE) or non-JSON: keep the raw text; message reassembly is deferred.
        record["response_raw"] = response_bytes.decode("utf-8", "replace")
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
