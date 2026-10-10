"""`permission_audit`: can anyone else on this machine read my files?

`get_file_info` reports one file's mode and `security_status` reads the
boot/firewall senses, so "is anything private readable by other users" had no
answer here.
"""
from __future__ import annotations

import os
import pathlib
import sys
from pathlib import Path
from stat import S_IROTH, S_IWOTH

import pytest

_REPO = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_REPO / "usr" / "lib" / "shani-chronoa"))

from shani_chronoa.skills import permission_audit as PA  # noqa: E402


@pytest.fixture
def tree(tmp_path):
    root = tmp_path / "home"
    root.mkdir(parents=True, exist_ok=True)
    private = root / "private.txt"
    private.write_text("mine\n")
    os.chmod(private, 0o600)
    open_ = root / "open.txt"
    open_.write_text("anyone\n")
    os.chmod(open_, 0o644)                  # world-readable
    writable = root / "writable.sh"
    writable.write_text("#!/bin/sh\n")
    os.chmod(writable, 0o777)               # world-writable
    wdir = root / "shared"
    wdir.mkdir()
    os.chmod(wdir, 0o777)
    (root / "id_rsa").write_text("key\n")
    os.chmod(root / "id_rsa", 0o644)        # secret name, world-readable
    (root / "server.pem").write_text("k\n")
    os.chmod(root / "server.pem", 0o640)    # not world-readable
    return root


def _run(path):
    return PA._run_skill({"path": str(path)})


# --- the rate, not the count ---------------------------------------------------

def test_the_rate_comes_before_the_count(tree):
    """**The raw count is the confidently wrong answer.** Measured on a real
    home: 3,558 of 33,407 files world-readable - 10.6%, entirely normal for a
    working tree. A reader leading with the count would send someone fixing
    nothing."""
    out = _run(tree)
    assert "%" in out
    assert "readable by any user" in out
    # And the rate is stated, not only the number.
    assert "are readable by any user" in out


def test_a_low_rate_is_described_as_low(tree):
    """**The fixture has to actually be low-rate.** The first version deleted
    `open.txt` and expected a low proportion - but `writable.sh` (0777) and
    `id_rsa` (0644) are both world-readable too, so the rate stayed at 50% and
    the assertion failed on a fixture that was not measuring what it claimed."""
    for name in ("open.txt", "writable.sh", "id_rsa"):
        (tree / name).unlink()
    for index in range(12):
        path = tree / f"private{index}.txt"
        path.write_text("x\n")
        os.chmod(path, 0o600)
    out = _run(tree)
    assert "low proportion" in out, out


def test_a_high_rate_says_it_is_common(tree):
    for name in ("a", "b", "c", "d"):
        path = tree / f"{name}.txt"
        path.write_text("x\n")
        os.chmod(path, 0o644)
    out = _run(tree)
    assert "high proportion" in out


# --- world-writable is a different question -----------------------------------

def test_world_writable_is_reported_separately(tree):
    """Another user being able to *replace* content is a wider permission than
    reading it, and mixing the two would hide the rarer one."""
    out = _run(tree)
    assert "**1 file(s) and 1 directory(ies) are world-writable**" in out, out
    assert "writable.sh" in out
    assert "directory: " in out


def test_a_world_writable_directory_is_seen(tree):
    """`os.walk` puts directory entries in `dirs`, not `names`, so a
    names-only scan never sees one - and a world-writable *directory* is a wider
    hole than any single file."""
    readable, wfiles, wdirs, sensitive, seen, skipped, truncated, un = \
        PA._walk(tree, PA._CEILING)
    assert [p.name for p in wdirs] == ["shared"], wdirs


def test_a_readable_file_is_not_called_writable(tree):
    readable, wfiles, wdirs, *_ = PA._walk(tree, PA._CEILING)
    # `readable` holds `(path, size)` pairs, not paths - the first version of
    # this called `.name` on the tuple and reported an AttributeError about its
    # own fixture rather than about the behaviour.
    assert "open.txt" not in [p.name for p in wfiles]
    assert "private.txt" not in [path.name for path, _size in readable]


# --- the name-based guess, and its limits -------------------------------------

def test_a_secret_name_that_is_readable_is_flagged(tree):
    out = _run(tree)
    assert "names that suggest secrets" in out
    assert "id_rsa" in out
    assert "matched the **names**, never the contents" in out


def test_a_secret_name_that_is_not_readable_is_not_flagged(tree):
    (tree / "id_rsa").unlink()
    out = _run(tree)
    assert "id_rsa" not in out


def test_it_claims_to_match_names_not_contents(tree):
    out = _run(tree)
    assert "nothing here can tell which from its permissions" in out
    assert "may be a real export or a test fixture" in out


@pytest.mark.parametrize("name,expected", [
    ("id_rsa", "id_rsa"),
    ("server.pem", ".pem"),
    ("Chrome Passwords.csv", "password"),
    ("notes.txt", ""),
    # **The false positive that drove the two-table split**: `.key` is a suffix,
    # not a substring, or `README.keyctl` is a secret-named file.
    ("README.keyctl", ""),
    ("a.pemphigus", ""),
    ("keyboard.conf", ""),
    ("backup.p12", ".p12"),
])
def test_the_sensitive_match_is_suffix_aware(name, expected):
    assert PA._looks_sensitive(name) == expected, name


# --- it changes nothing -------------------------------------------------------

def test_nothing_is_chmoded(tree):
    before = {p.name: oct(p.stat().st_mode & 0o777)
              for p in tree.iterdir()}
    _run(tree)
    after = {p.name: oct(p.stat().st_mode & 0o777)
             for p in tree.iterdir()}
    assert before == after, before


def test_it_says_nothing_was_changed(tree):
    assert "Nothing was changed" in _run(tree)


# --- the walk's limits --------------------------------------------------------

def test_an_unentered_directory_is_named(tmp_path):
    root = tmp_path / "locked"
    root.mkdir()
    (root / "open.txt").write_text("x\n")
    os.chmod(root / "open.txt", 0o644)
    private = root / "private"
    private.mkdir()
    (private / "id_rsa").write_text("k\n")
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


def test_no_files_is_not_the_same_as_empty(tmp_path):
    root = tmp_path / "none"
    root.mkdir()
    assert "not the same as it being empty" in _run(root)
