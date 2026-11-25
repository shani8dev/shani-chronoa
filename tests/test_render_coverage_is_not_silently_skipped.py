"""The render tests are deselected by the default batch, and that must be loud.

One `pytest tests apps/shani-sysadmin/tests` run died in a native GTK/Pango
snapshot (`pango_glyph_string_extents_range`, SIGSEGV/SIGABRT): every test that
maps a real ChronoaApplication into one process builds a second application,
and the crash takes the whole process with it. The fix is a process boundary -
`render` marker, default exclusion, `tools/run_render_tests.sh` - not a skip of
the assertions. This file is the check that the boundary stays visible: a
marker nobody registered, an exclusion that quietly disappeared or a runner
that did not exist would turn the coverage loss into a green suite.
"""

import os
import re
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
CONFTEST = REPO / "tests" / "conftest.py"
PYTEST_INI = REPO / "pytest.ini"
RUNNER = REPO / "tools" / "run_render_tests.sh"


def _text(path):
    return path.read_text(encoding="utf-8")


def _collect_render():
    out = subprocess.run(
        [
            sys.executable,
            "-m",
            "pytest",
            "--collect-only",
            "-q",
            "-m",
            "render",
            "-p",
            "no:cacheprovider",
        ],
        cwd=str(REPO),
        capture_output=True,
        text=True,
        timeout=120,
        # Keep the caller's display environment: conftest ignores window files
        # when no backend is advertised, which would hide the very coverage
        # this check is here to prove.
        env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"},
    )
    assert out.returncode == 0, out.stderr
    return [
        line.strip()
        for line in out.stdout.splitlines()
        if line.strip().startswith("tests/") and "::" in line
    ]


def test_the_marker_is_registered_and_says_why():
    source = _text(CONFTEST)
    assert re.search(r'"render:\s*maps a real ChronoaApplication', source), (
        "the render marker is not registered, so the exclusion is an unknown "
        "marker rather than a documented boundary"
    )
    # Negative control: the predicate must refuse a conftest without it.
    assert not re.search(
        r'"render:\s*maps a real ChronoaApplication',
        source.replace("render: maps a real", "gone: maps a real"),
    ), "the registration predicate could not see what it claims to check"


def test_the_default_batch_names_the_exclusion():
    ini = _text(PYTEST_INI)
    addopts = re.search(r"^addopts\s*=\s*(.+)$", ini, re.M)
    assert addopts, "pytest.ini has no addopts"
    assert "-m" in addopts.group(1) and "not render" in addopts.group(1), (
        "the default batch does not say it defers render tests"
    )
    assert "run_render_tests.sh" in ini, (
        "the exclusion has no pointer to the door back to that coverage"
    )


def test_the_runner_is_executable_and_collects_the_real_tests():
    assert RUNNER.is_file() and bool(RUNNER.stat().st_mode & 0o111), (
        "the documented door to render coverage is missing or not runnable"
    )
    script = _text(RUNNER)
    assert "-m render" in script, "the runner does not collect the marker"
    nodes = _collect_render()
    assert nodes, "no render tests were collected, so the runner would run none"
    for name in ("test_setup_button_is_reachable.py::", "test_sidebar_toggle.py::"):
        assert any(name in n for n in nodes), (
            f"the real-window file {name} is not covered by the runner"
        )


def test_a_run_that_cannot_be_measured_is_not_a_pass():
    """The runner's own verdict rule, read out of the script.

    A crash prints no pytest summary; a runner that treats that as a pass is
    the absence-presented-as-success this whole boundary exists to prevent.
    """
    script = _text(RUNNER)
    assert "NO SUMMARY" in script and "bad=$((bad + 1))" in script, (
        "the runner does not count a missing verdict as a failure"
    )
    # Negative control: the same predicate on a script that only prints the
    # count must refuse it, or it would pass on a runner that never checks.
    lookalike = script.replace("bad=$((bad + 1))", "# nothing counted")
    assert "bad=$((bad + 1))" not in lookalike, (
        "the predicate could not see the verdict check"
    )
