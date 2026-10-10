"""`apply_patch`: apply a .patch/.diff file to files on this machine.

Nothing applied one. `resolve_conflict` says *which files conflict* under git
and stops there; a hand-copied hunk is how a diff usually gets applied, which is
also how it half-applies.

All three behaviours below are measured against the real GNU patch 2.8 on this
box, and all three contradict its exit status.
"""
from __future__ import annotations

import pathlib
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

_REPO = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_REPO / "usr" / "lib" / "shani-chronoa"))

from shani_chronoa.skills import apply_patch as AP  # noqa: E402

PATCH = """--- f.txt
+++ expected.txt
@@ -1,3 +1,3 @@
 one
-two
+TWO
 three
"""

#: A patch whose context does **not** appear in the target, so it fails. The
#: first version's context line was "nothing like this" - which was only in
#: `bad.txt`, a file the patch never named - so it reported a `.rej` for the
#: wrong file and the assertion looked for the wrong name too.
BAD_PATCH = """--- f.txt
+++ expected.txt
@@ -1,1 +1,1 @@
-this context is nowhere in f.txt
+something else
"""


@pytest.fixture
def work(tmp_path):
    """A real working copy and a real patch made with `diff -u`, in $HOME so the
    sandbox can read it."""
    root = Path.home() / "apply-patch-test"
    shutil.rmtree(root, ignore_errors=True)
    root.mkdir(parents=True)
    (root / "f.txt").write_text("one\ntwo\nthree\n")
    (root / "expected.txt").write_text("one\nTWO\nthree\n")
    subprocess.run(["diff", "-u", "f.txt", "expected.txt"], cwd=root,
                   stdout=open(root / "change.patch", "w"), check=False)
    with open(root / "bad.patch", "w") as handle:
        handle.write(BAD_PATCH)
    yield root
    # **`shutil.rmtree`, not `subprocess.run(["rm", ...])`.** The timeout test
    # patches `subprocess.run` to raise, and a teardown that runs through the
    # same module raises with it - so the fixture reported the test's own
    # stub failing rather than the assertion failing. One cleanup path, one
    # import, nothing shared with what the tests patch.
    shutil.rmtree(root, ignore_errors=True)


def _run(patch, path, action=None, strip=None):
    """`_run_skill`, not `_run`.

    **The module's `_run` is its subprocess helper and returns a tuple.** The
    first version called it, so every assertion was comparing a string against
    `('', '', 0)` and the `action` argument reached patch as a filename -
    `patch: **** Can't open patch file action`. Two failures, one cause, and
    the cause was the name.
    """
    arguments = {"patch": str(patch), "path": str(path)}
    if action:
        arguments["action"] = action
    if strip is not None:
        arguments["strip"] = strip
    return AP._run_skill(arguments)


# --- the default is the safe one ----------------------------------------------

def test_check_is_the_default_and_changes_nothing(work):
    before = (work / "f.txt").read_text()
    out = _run(work / "change.patch", work)
    assert "would apply" in out
    assert (work / "f.txt").read_text() == before, "the dry run wrote"


def test_apply_writes(work):
    out = _run(work / "change.patch", work, "apply")
    assert "applied" in out
    assert "TWO" in (work / "f.txt").read_text()


# --- the three things its exit status does not say ----------------------------

def test_an_already_applied_patch_is_refused_not_reversed(work):
    """**Without `--forward`, `patch` reverses it and exits 0** - the file goes
    back to its old contents and nothing says so. This is the whole reason
    `--forward` is in the flag list."""
    _run(work / "change.patch", work, "apply")
    applied = (work / "f.txt").read_text()
    out = _run(work / "change.patch", work, "apply")
    assert "already applied" in out.lower(), out
    assert (work / "f.txt").read_text() == applied, \
        "the second apply changed the file - patch reversed it"
    assert "does not apply it backwards" in out


def test_a_failing_hunk_is_reported_despite_rc_0(work):
    """**Measured: `Hunk #1 FAILED` exits 0.** A reader trusting the status
    reports success for a patch that changed nothing, which is the most useful
    wrong answer available here."""
    out = _run(work / "bad.patch", work)
    assert "FAILED" in out or "could not fully apply" in out, out
    assert "did not apply" in out or ".rej" in out, out


def test_a_dry_run_leaves_no_rejects_file(work):
    """**The whole point of a dry run, measured.** `patch --dry-run` reports a
    failed hunk and writes nothing - the first version of this test expected a
    `.rej` from the default action and found none, because it had only measured
    the applying path.
    """
    out = _run(work / "bad.patch", work)
    assert "FAILED" in out, out
    assert "`.rej` file(s)" not in out, "the dry run reported rejects it did not write"
    assert sorted(work.glob("*.rej")) == [], "the dry run wrote a .rej"
    # And the target is untouched.
    assert (work / "f.txt").read_text() == "one\ntwo\nthree\n"


def test_the_rejects_file_is_reported_and_kept(work):
    """`patch` leaves a `.rej` holding what it could not apply. A directory with
    new unexpected files is its own surprise, and removing one silently would be
    a write the caller did not ask for."""
    out = _run(work / "bad.patch", work, "apply")
    # **The block's own wording, not the `.rej` string.** `patch` echoes "saving
    # rejects to file f.txt.rej" on stderr and this skill passes that line
    # through, so asserting `".rej" in out` is satisfied whether or not the
    # reporting block runs. The block adds what the echo cannot: the paths
    # **actually found on disk**, which is the assertion that has teeth.
    assert "left 1 `.rej` file(s)" in out, out
    rejects = sorted(work.glob("*.rej"))
    assert rejects, "the .rej vanished after a real apply"
    assert str(rejects[0]) in out, (rejects, out)
    # And the hunks it did manage are still in the file.
    assert "one" in (work / "f.txt").read_text()


def test_a_timed_out_patch_is_not_a_success(work, monkeypatch):
    def boom(*a, **k):
        raise subprocess.TimeoutExpired("patch", 30)

    monkeypatch.setattr(AP.subprocess, "run", boom)
    out = _run(work / "change.patch", work, "apply")
    assert "did not answer" in out
    assert "applied" not in out.lower().replace("already applied", "")


# --- never prompts -------------------------------------------------------------

def test_the_flags_make_it_cannot_prompt(work, monkeypatch):
    """`patch` without `--batch` asks `Assume -R? [n]` and **waits**, which is
    the skill-hanging shape this package has been bitten by more than once."""
    argv = []
    real = subprocess.run

    def watch(command, *args, **kwargs):
        if command and command[0] == "patch":
            argv.append(command)
        return real(command, *args, **kwargs)

    monkeypatch.setattr(AP.subprocess, "run", watch)
    _run(work / "change.patch", work, "apply")
    assert argv, "patch was never run"
    flags = argv[0]
    assert "--forward" in flags, flags
    assert "--batch" in flags, flags


def test_stdin_is_never_wired_to_the_patch(work, monkeypatch):
    """`patch < file` blocks when there is no stdin, which is how a skill hangs
    in a subprocess that has no terminal."""
    argv = []
    real = subprocess.run

    def watch(command, *args, **kwargs):
        if command and command[0] == "patch":
            argv.append((command, kwargs.get("stdin", "unset")))
        return real(command, *args, **kwargs)

    monkeypatch.setattr(AP.subprocess, "run", watch)
    _run(work / "change.patch", work, "apply")
    assert argv[0][1] in (None, "unset"), argv


# --- the honesty caveat --------------------------------------------------------

def test_it_does_not_claim_the_result_is_what_the_author_intended(work):
    """`patch` applies hunks by context, so a patch landing on different code
    applies cleanly and is still wrong."""
    out = _run(work / "change.patch", work)
    # Both clauses of the caveat, because it is built from several appends and
    # a mutation of the first leaves the rest standing.
    assert "**What this does not claim**" in out, out
    assert "by context" in out
    assert "still be wrong" in out


def test_a_trailing_slash_path_is_not_a_directory_to_patch_in(work):
    """`--directory` to a leaf that is not a directory is a refusal, not an
    empty apply."""
    out = _run(work / "change.patch", work / "f.txt")
    assert "not a directory" in out


def test_arguments_are_bounded(work):
    assert "Action must be check or apply" in \
        _run(work / "change.patch", work, "merge")
    assert "Which patch file" in AP._run_skill({"path": str(work)})
    assert "is not a number" in _run(work / "change.patch", work, strip="p")


def test_a_missing_patch_file_is_refused(work):
    assert "not a file" in _run(work / "nope.patch", work)
