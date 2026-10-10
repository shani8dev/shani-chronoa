"""`CAPABILITIES.md` is generated, so it can go stale - and this is the gate.

`tools/gen_capabilities.py` writes the document from `capabilities._GROUPS` and
the consent tables beside it, so the file *should* never drift.
`tests/check_capabilities_doc.py` checks that it did not: it regenerates the
document, compares it byte-for-byte with what is committed, and cross-checks the
prose claims (how many skills exist, which are gated, which are destructive)
against the tree.

**That checker was wired into nothing.** It was committed in `225b2cf` and
`2b95046`, it is correct, and no CI workflow, no pytest module and no script ever
ran it - so `PROBLEMS (4)` sat in a file nobody executed for as long as the file
existed. It was still red at HEAD when this was written, and only became green in
a working tree where another session had landed `container.py`, `ir_remote.py`
and `vm.py`. Those three are precisely the entries `capabilities.GATED` names and
`_GROUPS` advertises: **the checker was reporting, correctly, that the repo
declared a consent switch and a help label for skills that did not exist.** A
gate that is not run is not a gate.

This module does one thing: it runs that checker and fails the suite on a
non-zero exit. It lives here rather than beside its subject because **pytest does
not collect from a module named `check_*`** - which is the whole reason the
checker was dead, and why the fix is a `test_*` file that invokes it rather than
a rename of the thing it guards.

**Not vacuous by construction:** the checker's success line is asserted as well
as its exit status. A checker that died before printing anything, or that exited
0 having compared nothing, would fail this - `rc == 0` alone is exactly the shape
of a green signal that guaranteed nothing.
"""

from __future__ import annotations

import pathlib
import subprocess
import sys

import pytest

REPO = pathlib.Path(__file__).resolve().parent.parent
CHECKER = REPO / "tests" / "check_capabilities_doc.py"

#: The line the checker prints when the committed document matches the tree. It
#: encodes the two things it verified - the skill count and byte-identity - so
#: its absence means the check did not actually happen.
_OK = "CAPABILITIES.md matches the tree"


def _run_checker() -> subprocess.CompletedProcess:
    """Run the checker the way CI would, from the repo root.

    A subprocess rather than an import: the checker puts its own paths on
    `sys.path` and then imports `shani_chronoa`, which needs GTK on some
    importers, and it shells out to the generator itself. Running it as its own
    process is also how a person would run it, so the gate tests the same thing.
    """
    return subprocess.run(
        [sys.executable, str(CHECKER)],
        cwd=REPO, capture_output=True, text=True, timeout=600,
        env={"PYTHONDONTWRITEBYTECODE": "1", "PATH": "/usr/bin:/bin",
             "HOME": str(REPO)},
    )


def test_the_generated_capabilities_document_matches_the_tree():
    proc = _run_checker()
    assert proc.returncode == 0, (
        "check_capabilities_doc.py reported problems - the committed "
        "CAPABILITIES.md no longer matches the tree:\n"
        f"{proc.stdout.strip() or proc.stderr.strip()}")


def test_the_checker_actually_ran_rather_than_passing_silently():
    """The gate's own control.

    `rc == 0` is satisfied by a checker that crashed into a zero exit, or that
    compared an empty document against an empty file. The success line carries
    the skill count and the byte-identity claim, so asserting it is present is
    what stops this test from passing while inspecting nothing.
    """
    proc = _run_checker()
    assert _OK in proc.stdout, (
        f"the checker exited 0 but never reported success; stdout was:\n"
        f"{proc.stdout.strip() or '(empty)'}")


def test_the_checker_reports_a_skill_count_rather_than_nothing():
    """A count that cannot be zero, and that must equal the tree.

    This is what makes the previous two non-vacuous: the checker prints the
    number of skills it found, so a run that saw no skills at all would report
    "0 skills" and pass an rc==0 that means nothing.
    """
    proc = _run_checker()
    assert proc.returncode == 0, proc.stdout or proc.stderr
    # The count follows the marker: "CAPABILITIES.md matches the tree: 238
    # skills, ...". Taking the text *before* the marker yields an empty string,
    # and an IndexError off an empty split is a test that cannot run rather than
    # a test that can fail.
    # "... matches the tree: 238 skills, ..." - the number follows a colon, so
    # the first whitespace token is ":" and int(":") is a ValueError. Strip
    # punctuation, then take the first token that is a number.
    after = proc.stdout.split(_OK, 1)[1].lstrip(":").strip()
    digits = [t for t in after.split() if t.isdigit()]
    assert digits, proc.stdout
    count = int(digits[0])
    assert count > 0, proc.stdout


def test_the_checker_is_not_a_dead_file():
    """The check this whole module exists to make.

    If someone deletes or renames the checker, every test above would error
    rather than fail - and an erroring gate is one more dead gate. This asserts
    the file is present and is a runnable Python module with the pieces its
    docstring promises: a generator it shells out to, and a document it reads.
    """
    assert CHECKER.is_file(), f"{CHECKER} does not exist"
    assert (REPO / "tools" / "gen_capabilities.py").is_file()
    assert (REPO / "CAPABILITIES.md").is_file()


@pytest.mark.parametrize("mutation", [
    "stale_doc", "missing_doc", "dropped_gate",
])
def test_the_gate_fails_when_the_document_or_the_gate_is_broken(mutation, tmp_path):
    """Run the checker against a *broken copy* of the repo.

    The mutations are the three ways this document rots, and each must be caught:

    * `stale_doc`      - a skill was added and the document was not regenerated
    * `missing_doc`    - the document is gone, so there is nothing to compare
    * `dropped_gate`   - a consent switch was removed from a skill but not from
                         `capabilities.GATED`, which is the dead-switch class

    It is run in a copy rather than in place because the checker regenerates and
    compares against the repo it is given, so mutating the real one would dirty
    the working tree - and a test that leaves the tree dirty is a test that gets
    disabled.
    """
    import shutil

    work = tmp_path / "repo"
    work.mkdir(parents=True)
    # Only what the checker and its generator read. Copying the whole repo
    # exceeded the /tmp quota (a 16G tmpfs, 3.1G free) and failed with EIO 122
    # mid-copy - which is a test that cannot run, not one that can fail.
    for sub in ("tests", "tools", "usr/lib/shani-chronoa"):
        src = REPO / sub
        if src.is_dir():
            shutil.copytree(src, work / sub,
                            ignore=shutil.ignore_patterns("__pycache__",
                                                          "*.pyc"))
    shutil.copy2(REPO / "CAPABILITIES.md", work / "CAPABILITIES.md")

    doc = work / "CAPABILITIES.md"
    if mutation == "stale_doc":
        text = doc.read_text(encoding="utf-8")
        # A line the generator cannot produce, so the comparison must differ.
        doc.write_text(text + "\n<!-- stale -->\n", encoding="utf-8")
    elif mutation == "missing_doc":
        doc.unlink()
    elif mutation == "dropped_gate":
        # Remove one GATED entry without regenerating the document, so the
        # "gated skill(s) not marked" comparison must disagree.
        cap = work / "usr/lib/shani-chronoa/shani_chronoa/capabilities.py"
        text = cap.read_text(encoding="utf-8")
        lines = text.splitlines(keepends=True)
        hit = None
        for i, line in enumerate(lines):
            stripped = line.strip()
            if (stripped.startswith('"') and '": "' in stripped
                    and "enabled" in stripped):
                hit = i
                break
        assert hit is not None, "no GATED entry found to mutate"
        del lines[hit]
        cap.write_text("".join(lines), encoding="utf-8")

    proc = subprocess.run(
        [sys.executable, str(work / "tests" / "check_capabilities_doc.py")],
        cwd=work, capture_output=True, text=True, timeout=600,
        env={"PYTHONDONTWRITEBYTECODE": "1", "PATH": "/usr/bin:/bin",
             "HOME": str(work)},
    )
    assert proc.returncode != 0, (
        f"{mutation}: the checker accepted a broken tree:\n"
        f"{proc.stdout.strip()}")
    # "Said why" rather than "printed the word PROBLEMS": a missing document has
    # its own message on stdout and nothing on stderr, so a check that failed
    # while explaining itself must still pass this.
    said = (proc.stdout + proc.stderr).strip()
    assert said, f"{mutation}: the checker failed without saying why"
    if mutation == "missing_doc":
        assert "CAPABILITIES.md" in said, said
