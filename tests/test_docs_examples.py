"""The README shows what the commands print. This checks it still does.

A fenced block in a README is a claim about behaviour, and it is the claim most
likely to rot: the code moves, the block does not, and nobody notices because
nothing runs it. This section of the README has been wrong twice — once with numbers
no command produced, once with a provenance line naming a stub that produced
different ones. So the blocks are regenerated here and compared.
"""

from __future__ import annotations

import io
import os
import re
import subprocess
import sys
from contextlib import redirect_stdout
from pathlib import Path

import pytest

from ctxprofile.cli import main

ROOT = Path(__file__).resolve().parents[1]
README = ROOT / "README.md"
CAPTURE = ROOT / "examples" / "capture.json"

ONLINE_ANCHOR = "ctxprofile analyze capture.json --online"
TABLE_ANCHOR = "with no proxy and no network."


def _readme() -> str:
    return README.read_text(encoding="utf-8").replace("\r\n", "\n")


def _block_after(anchor: str) -> str:
    """The first non-empty fenced block whose content starts after `anchor`."""
    text = _readme()
    after = text.index(anchor) + len(anchor)
    fences = [match.start() for match in re.finditer(r"^```", text, re.MULTILINE)]
    for opener, closer in zip(fences[::2], fences[1::2], strict=False):
        content = text.index("\n", opener) + 1
        if content >= after and text[content:closer].strip():
            return text[content:closer].rstrip("\n")
    raise AssertionError(f"no fenced block after {anchor!r}")


def test_the_readme_headline_table_is_what_analyze_prints() -> None:
    captured = io.StringIO()
    with redirect_stdout(captured):
        assert main(["analyze", str(CAPTURE)]) == 0
    assert captured.getvalue().rstrip() == _block_after(TABLE_ANCHOR), (
        "the README's opening table no longer matches "
        "`ctxprofile analyze examples/capture.json` — regenerate it"
    )


def test_the_readme_online_example_is_reproducible_from_the_committed_stub() -> None:
    pytest.importorskip("anthropic", reason='needs pip install "ctxprofile[online]"')
    env = dict(os.environ)
    env["PYTHONPATH"] = str(ROOT / "src")
    env["PYTHONIOENCODING"] = "utf-8"
    result = subprocess.run(
        [
            sys.executable,
            str(ROOT / "tools" / "stub_count_tokens.py"),
            sys.executable,
            "-m",
            "ctxprofile.cli",
            "analyze",
            str(CAPTURE),
            "--online",
        ],
        capture_output=True,
        cwd=ROOT,
        env=env,
        check=True,
    )
    printed = result.stdout.decode("utf-8").replace("\r\n", "\n").rstrip()
    assert printed == _block_after(ONLINE_ANCHOR), (
        "the README's --online block no longer matches "
        "`python tools/stub_count_tokens.py ctxprofile analyze examples/capture.json "
        "--online` — regenerate it rather than editing the block by hand"
    )


def test_every_measured_row_in_the_documented_example_is_marked() -> None:
    """The `*` is the point of that example: it says which rows were measured."""
    block = _block_after(ONLINE_ANCHOR)
    measured = re.findall(r"^\s+(\S+)\s+\S+\s+\d+\*", block, re.MULTILINE)
    assert set(measured) >= {"system", "read_file", "web_search", "write_file"}
    assert "tokens measured with count_tokens" in block
