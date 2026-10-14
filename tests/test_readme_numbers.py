"""`README.md`'s numbers are read out of the tree, and this runs the reader.

`tests/check_readme_numbers.py` re-derives every number in the README's prose and
its senses table from `discover_senses()`, `tools.TOOLS` and the gschema, and
prints `PROBLEMS (n)` when they drift.

**It was wired into nothing** - no pytest module, no CI workflow, no script ran it.
The file was committed, it was correct, and it reported **five problems** the first
time it was run by hand in this pass, one of them serious: **`kernellog`,
`monitoring`, `polkitpolicy` and `ups` were absent from the senses table
entirely**. `ups` defaults **on**, so the README was under-reporting by a sense
which senses poll by default - on the page whose whole job is to say which ones do.

`tests/check_capabilities_doc.py` has exactly the same shape and is wired by
`test_capabilities_doc.py`; this module is the same wiring for the other one. **A
check that is not run is not a check**, which is the sentence in that file that
made this one necessary.

Run by hand:

    python3 tests/check_readme_numbers.py
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent


def test_the_readme_matches_the_tree():
    """The checker's own verdict, run for real."""
    out = subprocess.run(
        [sys.executable, str(REPO / "tests" / "check_readme_numbers.py")],
        capture_output=True, text=True, cwd=str(REPO), timeout=300)
    assert out.returncode == 0, (
        "check_readme_numbers.py reported problems - the README's numbers have "
        "drifted from the tree. Fix the numbers, not the checker:\n"
        + out.stdout + out.stderr)
    assert "README matches the tree" in out.stdout, (
        f"the checker printed no verdict, which is a regression in the checker "
        f"rather than a clean result:\n{out.stdout}")


def test_the_checker_actually_fails_when_it_should():
    """**The negative control, because a green run proves nothing on its own.**

    The first version of this module had only the test above and could not fail:
    if the checker always exited 0, or the assertion above never ran, the module
    would be green forever. So the README is edited in a **copy** of the tree and
    the checker is pointed at it, which is the only way to show the assertion
    above is attached to something.
    """
    import shutil
    import tempfile

    work = Path(tempfile.mkdtemp(prefix="readme-numbers-", dir="/tmp/opencode"))
    try:
        for name in ("tests", "usr", "tools", "README.md"):
            source = REPO / name
            target = work / name
            if source.is_dir():
                shutil.copytree(source, target)
            else:
                shutil.copy2(source, target)
        readme = work / "README.md"
        # **Derive the number this control drifts, do not remember it.** This used
        # to replace the literal `"**257 callable\n  skills**"`, and the README has
        # said **256** since `8c92258` retired `trash_file` - so the replace matched
        # **0** times, the "drifted" copy was byte-identical to the real README,
        # and the checker correctly exited 0. The control failed looking exactly
        # like a broken checker, which is the one thing a negative control must
        # never be. Same stale-literal shape as `test_schema_caps`'s 257.
        import re

        text = readme.read_text(encoding="utf-8")
        claimed = re.search(r"\*\*(\d+) callable\s*\n?\s*skills\*\*", text)
        assert claimed, (
            "the README has no 'callable skills' count for this control to "
            "drift, so the copy below would not be drifted at all")
        real = int(claimed.group(1))
        wrong = real + 1
        readme.write_text(text.replace(claimed.group(0),
                                       f"**{wrong} callable\n  skills**"),
                          encoding="utf-8")
        out = subprocess.run(
            [sys.executable, str(work / "tests" / "check_readme_numbers.py")],
            capture_output=True, text=True, cwd=str(work), timeout=300)
        assert out.returncode != 0, (
            f"a README claiming {wrong} skills did not fail the checker, so the "
            f"test above would pass on a drifted file:\n" + out.stdout)
        assert str(wrong) in out.stdout and str(real) in out.stdout, (
            "the checker failed without saying what the drift was: it must name "
            f"the {wrong} the README claims and the {real} the tree holds:\n"
            + out.stdout)
    finally:
        shutil.rmtree(work, ignore_errors=True)
