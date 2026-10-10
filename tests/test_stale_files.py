"""`stale_files`: what here is big *and* untouched?

`disk_usage` says what is big; `find_recently_modified` says what is recent.
Neither answers what comes after either, which is *which of these could I
delete* - and the answer is not "the biggest ones", because on a real machine
the biggest files are usually the ones that matter most.

A candidate is a file where two facts line up: large, and not touched.
"""
from __future__ import annotations

import os
import pathlib
import sys
import time
from pathlib import Path

import pytest

_REPO = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_REPO / "usr" / "lib" / "shani-chronoa"))

from shani_chronoa.skills import stale_files as SF  # noqa: E402

_MIB = 1024 * 1024


@pytest.fixture
def tree(tmp_path):
    """One candidate, and three near-misses that must not be reported."""
    root = tmp_path / "home"
    root.mkdir(parents=True, exist_ok=True)
    old = time.time() - 800 * 86400
    fresh = time.time()
    cases = {
        "big-and-old.bin": (_MIB * 40, old),      # the only candidate
        "big-and-recent.bin": (_MIB * 40, fresh),  # big, but in use
        "small-and-old.bin": (1024, old),          # untouched, but not worth it
        "just-under.bin": (_MIB * 15, old),        # just below the floor
    }
    for name, (size, when) in cases.items():
        path = root / name
        path.write_bytes(b"x" * size)
        os.utime(path, (when, when))
    return root


def _names(text):
    """The filenames from the candidate lines.

    A line is `-   40.0 MiB  /path/to/name  (untouched 2 year(s) ago)`, so the
    name is the **second** non-empty field. Splitting on `"  "` and taking the
    tail gives the age instead - which is what the first version did, so every
    assertion about which file was reported failed on the age string.
    """
    out = []
    for line in text.splitlines():
        if not line.startswith("- "):
            continue
        fields = [part for part in line[2:].split("  ") if part.strip()]
        if len(fields) >= 2:
            out.append(os.path.basename(fields[1].strip()))
    return out


# --- the two facts have to line up ---------------------------------------------

def test_only_the_big_and_old_file_is_a_candidate(tree):
    out = SF._run_skill({"path": str(tree), "older_than_days": 365})
    assert _names(out) == ["big-and-old.bin"], out


def test_a_big_file_in_use_is_not_a_candidate(tree):
    """**Size alone is not enough.** On a real machine the biggest files are
    usually the ones that matter most, so reporting the biggest untouched
    would be a different skill with a much worse failure mode."""
    out = SF._run_skill({"path": str(tree), "older_than_days": 365})
    assert "big-and-recent.bin" not in _names(out)


def test_a_small_old_file_is_below_the_floor(tree):
    """The decision costs more than the space, so a floor exists at all."""
    out = SF._run_skill({"path": str(tree), "older_than_days": 365})
    assert "small-and-old.bin" not in _names(out)


def test_the_floor_is_a_real_boundary(tree):
    assert "just-under.bin" not in _names(
        SF._run_skill({"path": str(tree), "older_than_days": 365}))


def test_the_age_floor_is_honoured(tmp_path):
    root = tmp_path / "aged"
    root.mkdir()
    for name, days in (("two-years.bin", 800), ("last-month.bin", 20)):
        path = root / name
        path.write_bytes(b"x" * (_MIB * 20))
        when = time.time() - days * 86400
        os.utime(path, (when, when))
    out = SF._run_skill({"path": str(root), "older_than_days": 365})
    assert _names(out) == ["two-years.bin"], out


def test_the_age_floor_accepts_a_shorter_window(tmp_path):
    root = tmp_path / "short"
    root.mkdir()
    path = root / "five-weeks.bin"
    path.write_bytes(b"x" * (_MIB * 20))
    when = time.time() - 35 * 86400
    os.utime(path, (when, when))
    assert "five-weeks.bin" in _names(
        SF._run_skill({"path": str(root), "older_than_days": 30}))


# --- the claim it refuses to make ----------------------------------------------

def test_it_never_claims_a_file_is_safe_to_delete(tree):
    """**The most damaging wrong answer available here is one that is both
    confident and actionable.** A large untouched file might be the archive
    nobody needed since 2023, or the backup of the thing that just broke."""
    out = SF._run_skill({"path": str(tree), "older_than_days": 365})
    assert "safe to delete" in out
    assert "None of these is safe to delete" in out


def test_untouched_is_not_claimed_to_mean_unused(tree):
    """**The kernel does not record reads by default.** With `relatime` a file
    opened every day can still carry an old mtime, so listing something a
    person reads daily as untouched is the failure this caveat exists for."""
    out = SF._run_skill({"path": str(tree), "older_than_days": 365})
    assert "does not record reads" in out
    assert "not as a list to act on" in out


def test_no_candidates_is_a_statement_about_what_was_checked(tmp_path):
    """Not a promise that nothing could be deleted - the walk may have been
    bounded."""
    root = tmp_path / "emptyish"
    root.mkdir()
    (root / "small.txt").write_bytes(b"x" * 100)
    out = SF._run_skill({"path": str(root), "older_than_days": 365})
    assert "not a promise that nothing could be deleted" in out
    assert "file(s) checked" in out


def test_it_deletes_nothing(tree):
    before = sorted(p.name for p in tree.iterdir())
    SF._run_skill({"path": str(tree), "older_than_days": 365})
    assert sorted(p.name for p in tree.iterdir()) == before


# --- the walk's own limits -----------------------------------------------------

def test_a_permission_failure_is_counted_not_hidden(tmp_path, monkeypatch):
    """A file this cannot read is not a file that does not exist - dropping it
    silently shrinks every total."""
    root = tmp_path / "locked"
    root.mkdir()
    private = root / "noaccess"
    private.mkdir()
    (private / "hidden.bin").write_bytes(b"x" * (_MIB * 30))
    os.chmod(private, 0)
    try:
        out = SF._run_skill({"path": str(root), "older_than_days": 365})
        # **The directory is named, not merely counted.** "0 files checked"
        # plus a number would still be an absence shaped like an answer; the
        # path is what lets someone act on it. The first version asserted the
        # phrase "could not be read" and the real message says "could not be
        # entered", so it failed on wording while the behaviour was right.
        assert "could not be entered" in out
        assert "noaccess" in out
        assert "not in that count" in out
    finally:
        os.chmod(private, 0o755)


def test_the_ceiling_stops_the_walk(tmp_path, monkeypatch):
    """A bounded walk must say it was bounded - a short list sorted
    largest-first is exactly the shape of a complete one."""
    root = tmp_path / "many"
    root.mkdir()
    old = time.time() - 800 * 86400
    for index in range(20):
        path = root / f"f{index}.bin"
        path.write_bytes(b"x" * (_MIB * 18))
        os.utime(path, (old, old))
    monkeypatch.setattr(SF, "_CEILING", 5)
    out = SF._run_skill({"path": str(root), "older_than_days": 365})
    assert "Stopped after 5 file(s)" in out
    assert "not a complete list" in out


def test_the_reclaimable_total_is_stated(tree):
    out = SF._run_skill({"path": str(tree), "older_than_days": 365})
    assert "40.0 MiB together" in out


def test_arguments_are_bounded(tree):
    assert "is not a number of days" in SF._run_skill(
        {"path": str(tree), "older_than_days": "soon"})
    assert "is shorter than this will report on" in SF._run_skill(
        {"path": str(tree), "older_than_days": 0})
    assert "is not a count" in SF._run_skill(
        {"path": str(tree), "limit": "lots"})


def test_a_non_directory_is_refused(tmp_path):
    target = tmp_path / "f.txt"
    target.write_text("x\n")
    assert "not a directory" in SF._run_skill({"path": str(target)})
