from __future__ import annotations

import json
from pathlib import Path

from ctxprofile.cli import main
from ctxprofile.lockfile import build_lock, diff_lock, static_summary

FIXTURES = Path(__file__).parent / "fixtures"


def _request(name: str) -> dict:
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))["request"]


def test_build_lock_records_static_floor() -> None:
    lock = build_lock(_request("capture_b.json"), "claude-opus-4-8")
    assert lock["tools"] == ["read_file", "write_file"]
    assert lock["static_input_tokens"] > 0
    assert lock["estimator"] == "chars4-v1"


def test_diff_lock_flags_new_tool() -> None:
    lock = build_lock(_request("capture_b.json"), "claude-opus-4-8")
    current = static_summary(_request("capture.json"))
    violations = diff_lock(current, lock)
    assert any("web_search" in v for v in violations)


def test_diff_lock_flags_regression() -> None:
    lock = build_lock(_request("capture_b.json"), "claude-opus-4-8")
    current = static_summary(_request("capture.json"))
    violations = diff_lock(current, lock, max_regression_tokens=0, fail_on_new_tool=False)
    assert any("static floor" in v for v in violations)


def test_diff_lock_clean_when_identical() -> None:
    lock = build_lock(_request("capture_b.json"), "claude-opus-4-8")
    current = static_summary(_request("capture_b.json"))
    assert diff_lock(current, lock) == []


def test_cli_lock_and_ci(tmp_path: Path) -> None:
    lock_path = tmp_path / ".ctxprofile.lock"
    assert main(["lock", "--from", str(FIXTURES / "capture_b.json"), "-o", str(lock_path)]) == 0
    assert lock_path.exists()
    # capture.json adds web_search on top of the locked floor
    assert main(["ci", "--lock", str(lock_path), str(FIXTURES / "capture.json")]) == 1
    # capture_b.json matches the lock
    assert main(["ci", "--lock", str(lock_path), str(FIXTURES / "capture_b.json")]) == 0
