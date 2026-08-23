"""The README says its images are generated from real output. This makes that true.

Both figures carry a claim: the demo image "is the output of that command", and the
survey chart comes "from the committed corpus". A claim like that decays silently —
the image stays on the page long after the output it depicts has changed. So it is
checked here instead of asserted in prose.
"""

from __future__ import annotations

import importlib.util
import io
import json
import sys
from contextlib import redirect_stdout
from itertools import pairwise
from pathlib import Path
from types import ModuleType

import pytest

from ctxprofile.cli import main

ROOT = Path(__file__).resolve().parents[1]
TOOLS = ROOT / "tools"
DOCS = ROOT / "docs"


def _load(name: str) -> ModuleType:
    spec = importlib.util.spec_from_file_location(name, TOOLS / f"{name}.py")
    if spec is None or spec.loader is None:  # pragma: no cover - only if tools/ is gone
        pytest.skip(f"tools/{name}.py not available")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def _committed(path: Path) -> str:
    return path.read_text(encoding="utf-8").replace("\r\n", "\n")


@pytest.mark.parametrize("theme", ["light", "dark"])
def test_the_readme_demo_image_still_shows_what_demo_prints(theme: str) -> None:
    render = _load("render_terminal_svg")
    captured = io.StringIO()
    with redirect_stdout(captured):
        assert main(["demo"]) == 0
    lines = captured.getvalue().replace("\r\n", "\n").split("\n")
    while lines and not lines[-1].strip():
        lines.pop()

    fresh = render.render(lines, theme, "ctxprofile demo") + "\n"
    assert fresh == _committed(DOCS / f"demo-{theme}.svg"), (
        f"docs/demo-{theme}.svg no longer matches `ctxprofile demo`. Regenerate it: "
        "ctxprofile demo | python tools/render_terminal_svg.py "
        f"-o docs/demo-{theme}.svg --theme {theme}"
    )


@pytest.mark.parametrize("theme", ["light", "dark"])
def test_the_survey_chart_still_matches_the_committed_corpus(theme: str) -> None:
    chart = _load("render_survey_chart")
    payload = json.loads((ROOT / "examples" / "mcp-survey.json").read_text(encoding="utf-8"))
    tools = payload.get("request", payload)["tools"]

    from ctxprofile.lint import lint_tools

    report = lint_tools(tools)
    fresh = chart.render(chart.group(tools), report.baseline_tokens, report.removable_tokens, theme)
    assert fresh + "\n" == _committed(DOCS / f"survey-{theme}.svg"), (
        f"docs/survey-{theme}.svg no longer matches examples/mcp-survey.json. Regenerate it: "
        f"python tools/render_survey_chart.py examples/mcp-survey.json "
        f"-o docs/survey-{theme}.svg --theme {theme}"
    )


def test_the_demo_image_is_not_double_spaced() -> None:
    """A CR-doubling pipe once rendered every line followed by a blank one."""
    import re

    svg = _committed(DOCS / "demo-light.svg")
    ys = [float(y) for y in re.findall(r'<text[^>]*\by="([\d.]+)"', svg)]
    render = _load("render_terminal_svg")
    steps = {round(b - a, 1) for a, b in pairwise(ys[2:])}
    assert steps, "no text rows in the demo image"
    assert min(steps) == render.LINE_HEIGHT, (
        f"row spacing {sorted(steps)} does not start at LINE_HEIGHT "
        f"{render.LINE_HEIGHT}; the input had blank lines inserted"
    )
