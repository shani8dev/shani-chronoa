"""`files_by_name`: where are all the files with this name?

`find_files` matches a name pattern. That is not the question people have when
they find several copies of something: *where are they, and are they the same?*
This answers the location half; `duplicate_files` answers the content half, and
the two are deliberately separate.
"""
from __future__ import annotations

import os
import pathlib
import shutil
import sys
from pathlib import Path

import pytest

_REPO = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_REPO / "usr" / "lib" / "shani-chronoa"))

from shani_chronoa.skills import files_by_name as FN  # noqa: E402

_KIB = 1024


@pytest.fixture
def tree(tmp_path):
    root = tmp_path / "home"
    root.mkdir(parents=True, exist_ok=True)
    (root / "work").mkdir()
    (root / "archive").mkdir()
    # Same name, different sizes and dates - so they are NOT all copies.
    (root / "work" / "report.docx").write_bytes(b"x" * (10 * _KIB))
    (root / "archive" / "report.docx").write_bytes(b"x" * (40 * _KIB))
    (root / "report.docx").write_bytes(b"x" * (10 * _KIB))
    os.utime(root / "archive" / "report.docx", (0, 0))
    os.utime(root / "work" / "report.docx", (0, 0))
    # A different case, which on Linux is a different name.
    (root / "Report.docx").write_bytes(b"x" * _KIB)
    return root


def _run(name, path, **kw):
    arguments = {"name": name, "path": str(path)}
    arguments.update(kw)
    return FN._run_skill(arguments)


# --- the location half ---------------------------------------------------------

def test_all_copies_are_found_and_ranked(tree):
    out = _run("report.docx", tree)
    assert "3 file(s)" in out, out
    # **Positions of whole lines, not substrings.** "report.docx" is a substring
    # of "archive/report.docx", so `out.index("report.docx")` finds the first
    # one *inside* the archive path and the comparison is meaningless - the
    # first version reported `221 < 16` and I read it as a ranking failure
    # rather than as its own bug.
    lines = [l for l in out.splitlines() if "report.docx" in l]
    # **Only candidate lines, which begin with a size.** A looser filter picks up
    # prose lines that merely mention a path - which is where the spurious fourth
    # entry came from.
    sizes_in_order = []
    for line in lines:
        fields = line.split()
        if not fields or not fields[0].replace(".", "", 1).isdigit():
            continue                      # prose, not a candidate
        if len(fields) < 2:
            continue
        scale = {"B": 1, "KiB": 1024, "MiB": 1024 ** 2}.get(fields[1])
        if scale:
            sizes_in_order.append(float(fields[0]) * scale)
    assert len(sizes_in_order) == 3, (sizes_in_order, lines)
    assert sizes_in_order == sorted(sizes_in_order, reverse=True), sizes_in_order


def test_the_newest_is_named(tree):
    """If they are copies, the newest is usually the one wanted."""
    out = _run("report.docx", tree)
    assert "The newest is" in out
    assert str(tree / "report.docx") in out


def test_differing_sizes_are_reported_as_such(tree):
    """**They are not all the same size, so they are not all copies of one
    thing** - a message that says "these might be copies" for three different
    files is the confidently wrong answer."""
    out = _run("report.docx", tree)
    # **Matched across the markdown emphasis.** The sentence is "They are
    # **not** all the same size", so "not all the same size" is not a substring.
    assert "all the same size" in out
    assert "40.0 KiB" in out


def test_matching_sizes_are_a_hint_and_not_proof(tmp_path):
    root = tmp_path / "same"
    root.mkdir()
    for name, size in (("a.txt", 10), ("b.txt", 10)):
        (root / name).write_bytes(b"x" * size)
    # **Searching for a name both files share**, not for one of them: the first
    # version searched "a.txt" and found a single file, so the "all the same
    # size" branch never ran and the assertion failed on a fixture that was not
    # exercising it.
    (root / "shared.txt").write_bytes(b"x" * 10)
    (root / "sub").mkdir()
    (root / "sub" / "shared.txt").write_bytes(b"x" * 10)
    out = _run("shared.txt", root)
    assert "same size" in out, out
    assert "not proof" in out


# --- case, and the boundary with duplicate_files -------------------------------

def test_case_sensitive_by_default(tree):
    """On Linux `Report.docx` is a different file from `report.docx`; folding
    case by default would merge two things and report a copy that does not
    exist."""
    assert "Report.docx" not in _run("report.docx", tree)


def test_case_insensitive_finds_both(tree):
    out = _run("REPORT.DOCX", tree, case_sensitive=False)
    assert "4 file(s)" in out, out


def test_a_single_pass_whatever_the_folding(tree, monkeypatch):
    """**The first version walked the tree twice** when case-insensitive was
    asked for - the first pass found nothing, the second did the real work - so
    a search cost two full traversals and reported `seen` from the empty one."""
    # **A shim over the module's own `os`, not a patch of the shared module.**
    # `monkeypatch.setattr(FN.os, "walk", ...)` replaces `os.walk` for every
    # library in the process, so the counter collected 221 walks from pytest's
    # own internals and the fixture. A module-scoped shim counts only this one.
    class OnlyThisWalk:
        def __init__(self, inner):
            self._inner = inner
            self.calls = 0

        def walk(self, top, *args, **kwargs):
            self.calls += 1
            return self._inner.walk(top, *args, **kwargs)

        def __getattr__(self, name):
            return getattr(self._inner, name)

    shim = OnlyThisWalk(FN.os)
    monkeypatch.setattr(FN, "os", shim)
    _run("REPORT.DOCX", tree, case_sensitive=False)
    assert shim.calls == 1, f"walked {shim.calls} times"


def test_it_does_not_claim_the_contents_match(tree):
    """**The boundary with `duplicate_files`, stated in the answer.** Same name
    is not same content; merging the two questions is what makes a reader report
    a copy that differs."""
    out = _run("report.docx", tree)
    assert "has not compared their contents" in out
    assert "Same name is not same content" in out
    assert "duplicate_files" in out


def test_no_matches_points_at_the_tool_that_also_matches(tmp_path):
    root = tmp_path / "none"
    root.mkdir()
    out = _run("nothing", root)
    assert "No file with" in out
    assert "find_files" in out


# --- the walk's limits --------------------------------------------------------

def test_an_unentered_directory_is_named(tmp_path):
    root = tmp_path / "mixed"
    root.mkdir()
    private = root / "private"
    private.mkdir()
    (private / "report.docx").write_text("x\n")
    os.chmod(private, 0)
    try:
        out = _run("report.docx", root)
        assert "could not be entered" in out, out
        assert "different answer from there being no such file" in out, out
    finally:
        os.chmod(private, 0o755)


def test_a_non_directory_is_refused(tmp_path):
    target = tmp_path / "f.txt"
    target.write_text("x\n")
    assert "not a directory" in _run("x", target)


def test_no_name_is_a_question(tmp_path):
    root = tmp_path / "x"
    root.mkdir()
    assert "Which name" in FN._run_skill({"path": str(root)})
