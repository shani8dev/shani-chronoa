"""`tools/list-audit.py` reported a clean result from measuring nothing.

Run as a person would run it - from the repo root, no environment variable - it
printed

    scanned 0 code-level name lists across 4 repos; 0 unmeasured

**"0 unmeasured" read as a pass.** It says every hand-kept name list in four
repos is measured by some test. The truth was that `ROOT` defaulted to the
script's own directory (`shani-chronoa/tools/`), so `ROOT /
"shani-chronoa/usr/lib/..."` did not exist, all four targets were dropped by
`if not base.is_dir(): continue`, and nothing was scanned at all.

It is the same shape this repository records everywhere: an absence presented as
a result, indistinguishable from a real one because both print a number.

**Two halves, and the second is what makes the first honest.** The default is
derived from the tool's own location now, so it finds the workspace it lives in
without an env var. And `main` refuses to print the verdict when nothing was
scanned - exit 2, naming the four paths it looked for - because a default that
happens to be wrong today would be wrong again the day the tree moves.

This is a survey, not a gate: its own docstring is explicit that UNMEASURED
"does not prove the list is wrong; it proves nothing would notice if it were."
So the count is not asserted. What is asserted is that it *measures*, and that
it says so when it does not.
"""

from __future__ import annotations

import os
import pathlib
import subprocess
import sys

REPO = pathlib.Path(__file__).resolve().parent.parent
TOOL = REPO / "tools" / "list-audit.py"


def _run(env_overrides):
    env = dict(os.environ)
    env.pop("SHANI_ROOT", None)
    env["XDG_STATE_HOME"] = "/tmp/opencode/xdgstate"
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    env.update(env_overrides)
    return subprocess.run(
        [sys.executable, str(TOOL)],
        capture_output=True, text=True, cwd=str(REPO), env=env, timeout=900)


def test_it_measures_the_workspace_it_lives_in_with_no_environment():
    """No SHANI_ROOT, run from the repo root: it must actually scan something.

    This is the invocation that used to report a clean zero. The count itself
    moves as skills land, so what is pinned is that it is a real measurement:
    more than zero lists, more than zero repos, and exit 0.
    """
    out = _run({})
    assert out.returncode == 0, (
        "list-audit.py failed when run with no environment:\n"
        + out.stdout + out.stderr)
    assert "scanned 0 code-level name lists" not in out.stdout, (
        "list-audit.py scanned nothing and reported it as a clean result - "
        "the defect this file exists for is back:\n" + out.stdout)
    summary = [l for l in out.stdout.split("\n") if l.startswith("scanned ")]
    assert summary, "the tool printed no summary line:\n" + out.stdout
    assert "across 4 repos" in summary[0], (
        "it did not scan all four sibling repos:\n" + summary[0])


def test_it_refuses_to_report_a_clean_result_when_nothing_is_scanned():
    """**The negative control, because a green run proves nothing on its own.**

    Pointed at a tree that has none of the four repos, the tool must refuse
    rather than print "0 unmeasured". Without this test the first one would pass
    on a tool that had gone back to skipping every target silently, as long as
    it exited 0.
    """
    empty = pathlib.Path("/tmp/opencode/emptyroot-list-audit")
    empty.mkdir(parents=True, exist_ok=True)
    out = _run({"SHANI_ROOT": str(empty)})
    assert out.returncode == 2, (
        "a run that measured nothing exited %d; it must refuse rather than "
        "report a clean zero:\n%s" % (out.returncode, out.stdout))
    assert "NOTHING WAS MEASURED" in out.stdout, (
        "the refusal did not say what was wrong:\n" + out.stdout)
    # It must name the paths it looked for, so a reader can see why.
    assert "shani-cassini/src" in out.stdout and "shani-settings/etc" in out.stdout, \
        "the refusal did not name the four targets:\n" + out.stdout
    # And it must not also print the clean summary it is refusing to stand behind.
    assert "scanned 0 code-level name lists" not in out.stdout, (
        "the refusal still printed the verdict it is refusing:\n" + out.stdout)


def test_the_override_still_wins():
    """SHANI_ROOT is honoured - the fix must not have removed the escape hatch."""
    home = pathlib.Path(os.environ["HOME"])
    out = _run({"SHANI_ROOT": str(home / "Documents" / "shani")})
    # Either the workspace is there and it scans it, or the override is
    # honoured and the refusal names the path. Both prove the override is read;
    # what must not happen is the tool ignoring it and discovering its own.
    assert out.returncode in (0, 2), (
        "the SHANI_ROOT override run failed outright:\n" + out.stdout)
    if out.returncode == 2:
        assert str(home / "Documents" / "shani") in out.stdout, (
            "the refusal did not name the overridden root:\n" + out.stdout)
