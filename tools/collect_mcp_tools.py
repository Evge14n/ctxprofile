"""Collect real tool definitions from MCP servers into an Anthropic-shaped request.

This is now a command, so a pip install can do it without cloning the repo:

    ctxprofile collect servers.json -o mine.json

The script stays as an alias for anyone following an older copy of the docs.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from ctxprofile.cli import main

if __name__ == "__main__":
    args = sys.argv[1:]
    raise SystemExit(main(["collect", *args]))
