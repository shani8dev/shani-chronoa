"""`empty_directories`: which directories in here are empty?

Nothing answered it. An empty directory costs no space, so every size-based
cleanup answer skips it, and it is the usual residue of a move, an uninstall or
an extraction that unwound. Measured on this box: 217 under a home directory.
"""
from __future__ import annotations

import os
import pathlib
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

_REPO = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_REPO / "usr" / "lib" / "shani-chronoa"))

from shani_chronoa.skills import empty_directories as ED  # noqa: E402


@pytest.fixture
def tree(tmp_path):
    root = tmp_path / "tree"
    root.mkdir(parents=True, exist_ok=True)
    (root / "truly-empty").mkdir()
    (root / "has-file").mkdir()
    (root / "has-file" / "x.txt").write_text("x\n")
    # Holds no files but holds a directory: `rmdir` refuses it.
    (root / "only-subdirs").mkdir()
    (root / "only-subdirs" / "inner").mkdir()
    (root / "deep").mkdir()
    (root / "deep" / "also-empty").mkdir()
    return root


def _run(path):
    return ED._run_skill({"path": str(path)})


# --- "empty" is rmdir's definition --------------------------------------------

def test_a_directory_holding_only_subdirectories_is_not_empty(tree):
    """**The rule that matters.** It holds no *files*, so a naive "no entries"
    test calls it empty and sends someone to `rmdir` a directory that refuses.
    Verified against the real `rmdir` below."""
    empty, why = ED._is_empty(tree / "only-subdirs")
    assert empty is False, why
    assert "sub-directory" in why


def test_rmdir_agrees_with_the_rule(tmp_path):
    """The definition is checked against the tool that has to act on it, not just
    against the code's own answer."""
    root = tmp_path / "rmtest"
    (root / "only-subdirs").mkdir(parents=True)
    (root / "only-subdirs" / "inner").mkdir()
    (root / "truly-empty").mkdir()
    refused = subprocess.run(["rmdir", str(root / "only-subdirs")],
                             capture_output=True, text=True)
    works = subprocess.run(["rmdir", str(root / "truly-empty")],
                           capture_output=True, text=True)
    assert refused.returncode != 0, "rmdir should refuse it"
    assert works.returncode == 0, "rmdir should take it"
    empty_rule, _ = ED._is_empty(root / "only-subdirs")
    assert empty_rule is False


def test_a_directory_with_a_file_is_not_empty(tree):
    assert ED._is_empty(tree / "has-file")[0] is False


def test_a_directory_that_cannot_be_read_is_not_empty(tmp_path):
    """**Measured: listing it is a permission error.** Reporting "empty" there is
    the confidently wrong answer - the directory may be full of things this user
    cannot see."""
    root = tmp_path / "locked"
    root.mkdir()
    private = root / "private"
    private.mkdir()
    (private / "hidden.txt").write_text("x\n")
    os.chmod(private, 0)
    try:
        empty, why = ED._is_empty(private)
        assert empty is False, why
        assert "could not be read" in why
    finally:
        os.chmod(private, 0o755)


# --- the answer ----------------------------------------------------------------

def test_all_empties_are_listed_with_their_depth(tree):
    out = _run(tree)
    assert "**3 of 7" in out or "**3 of" in out, out
    assert "truly-empty" in out
    assert "also-empty" in out
    assert "has-file" not in out


def test_no_empties_is_a_different_answer_from_nothing_found(tmp_path):
    root = tmp_path / "full"
    root.mkdir()
    (root / "a").mkdir()
    (root / "a" / "f.txt").write_text("x\n")
    out = _run(root)
    assert "none of them is empty" in out
    assert "different answer from" in out


def test_it_warns_that_empty_is_not_unused(tree):
    out = _run(tree)
    # **Matched across the markdown emphasis.** The sentence is
    # "Empty is **not** the same as unused", so the phrase
    # "not the same as unused" is not a substring - the first version of this
    # assertion failed on a string that was right there.
    assert "the same as unused" in out
    assert "about to write into it" in out


def test_it_removes_nothing(tree):
    before = sorted(p.as_posix() for p in tree.rglob("*"))
    _run(tree)
    after = sorted(p.as_posix() for p in tree.rglob("*"))
    assert before == after


def test_the_limit_caps_the_list(tree):
    out = ED._run_skill({"path": str(tree), "limit": 1})
    assert "more" in out


def test_an_unentered_directory_is_named(tmp_path):
    root = tmp_path / "mixed"
    root.mkdir()
    (root / "empty").mkdir()
    private = root / "private"
    private.mkdir()
    (private / "x").mkdir()
    os.chmod(private, 0)
    try:
        out = _run(root)
        assert "could not be entered" in out
        assert "private" in out
    finally:
        os.chmod(private, 0o755)


def test_a_non_directory_is_refused(tmp_path):
    target = tmp_path / "f.txt"
    target.write_text("x\n")
    assert "not a directory" in _run(target)


def test_it_says_nothing_was_removed(tree):
    """**The claim, not only the behaviour.** A mutation replacing
    "None of these has been removed." with "Removed them." left the suite green,
    because the other test asserts the *files* survived and nothing asserted the
    *sentence*. A tool that says it removed something when it did not is the
    confident-wrong-answer shape this package keeps recording - and the inverse,
    saying nothing was removed after removing it, is the one that destroys data.
    """
    assert "None of these has been removed" in _run(tree)
