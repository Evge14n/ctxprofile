"""A local stand-in for `count_tokens`, so the `--online` example can be reproduced.

The README shows what `analyze --online` prints. Nobody can check that without an
API key, and an example nobody can check is the kind of thing this project exists to
argue against. So the documented run goes through this: a local server that answers
the endpoint with a deterministic count, and a wrapper that points the SDK at it.

    python tools/stub_count_tokens.py ctxprofile analyze examples/capture.json --online

The counts are a stand-in, not Anthropic's tokenizer — that is the whole point of
saying so. What the example demonstrates is the shape of the output and the
labelling, both of which are the same either way.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any

# Roughly what an Anthropic-shaped request costs around its content, chosen so the
# numbers land in a believable range rather than to match any real tokenizer.
ENVELOPE = 11
PER_MESSAGE = 3
TOOL_PREAMBLE = 7
CHARS_PER_TOKEN = 3.4


def _tokens(text: str) -> int:
    return max(1, round(len(text) / CHARS_PER_TOKEN))


def count(body: dict[str, Any]) -> int:
    """A schema-length-proportional count: bigger definitions cost more, as they do."""
    total = ENVELOPE + PER_MESSAGE * len(body.get("messages") or [])
    system = body.get("system")
    if system:
        total += _tokens(json.dumps(system, ensure_ascii=False))
    tools = body.get("tools") or []
    if tools:
        total += TOOL_PREAMBLE
        total += sum(_tokens(json.dumps(tool, ensure_ascii=False)) for tool in tools)
    return total


class _Handler(BaseHTTPRequestHandler):
    def do_POST(self) -> None:
        length = int(self.headers.get("Content-Length", "0"))
        body = json.loads(self.rfile.read(length) or b"{}")
        payload = json.dumps({"input_tokens": count(body)}).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def log_message(self, *args: Any) -> None:
        pass


def run(command: list[str]) -> int:
    server = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        env = {
            **os.environ,
            "ANTHROPIC_BASE_URL": f"http://127.0.0.1:{server.server_address[1]}",
            "ANTHROPIC_API_KEY": "local-stand-in",
        }
        return subprocess.run(command, env=env, check=False).returncode
    finally:
        server.shutdown()


def main() -> int:
    command = sys.argv[1:]
    if not command:
        print(__doc__, file=sys.stderr)
        return 2
    return run(command)


if __name__ == "__main__":
    raise SystemExit(main())
