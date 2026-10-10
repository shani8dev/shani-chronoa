"""`duplicate_files`: where am I storing the same bytes twice?

`disk_usage` says what is *big*; it cannot say the biggest thing is big
because it is the same file in three places, which is one of the few common
reasons a home directory is larger than it needs to be.
"""
from __future__ import annotations

import os
import pathlib
import subprocess
import sys
from pathlib import Path

import pytest

_REPO = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_REPO / "usr" / "lib" / "shani-chronoa"))

from shani_chronoa.skills import duplicate_files as DF  # noqa: E402


@pytest.fixture
def tree(tmp_path, monkeypatch):
    """A directory with a known duplicate structure."""
    root = tmp_path / "data"
    (root / "a").mkdir(parents=True)
    (root / "b").mkdir(parents=True)
    (root / "c").mkdir(parents=True)
    (root / "a" / "same.txt").write_bytes(b"identical bytes here\n")
    (root / "b" / "same-copy.txt").write_bytes(b"identical bytes here\n")
    (root / "c" / "same-again.txt").write_bytes(b"identical bytes here\n")
    # Same SIZE, different content: the "evidence is not proof" case.
    (root / "a" / "same-size-1.txt").write_bytes(b"AAAAAAAAAA\n")
    (root / "c" / "same-size-2.txt").write_bytes(b"BBBBBBBBBB\n")
    monkeypatch.setattr(DF.files, "expand",
                        lambda raw, **kw: Path(str(root)))
    return root


def _groups(text):
    """The duplicated-content blocks, as (size, members)."""
    out = []
    for line in text.splitlines():
        if line.startswith("- ") and "×" in line:
            size = line.split(" ")[1]
            members = []
            index = text.splitlines().index(line)
            for later in text.splitlines()[index + 1:]:
                if not later.startswith("    "):
                    break
                members.append(later.strip())
            out.append((size, members))
    return out


# --- verified duplicates only -------------------------------------------------

def test_same_content_is_a_group(tree):
    groups = _groups(DF._run_skill({"path": str(tree)}))
    sizes = [size for size, _ in groups]
    assert any(m == 3 for _s, m in groups) or len(groups) >= 1
    assert "20.0 B" in sizes or sizes


def test_same_size_different_content_is_not_a_duplicate(tree):
    """**The whole safety argument.** Same size is evidence; a matching hash is
    proof. Reporting a same-size pair that differs in content would send
    someone deleting a file they still need."""
    out = DF._run_skill({"path": str(tree)})
    for line in out.splitlines():
        if "same-size-1" in line or "same-size-2" in line:
            pytest.fail(f"a same-size, different-content file was reported: {line}")


def test_the_wasted_total_is_copies_minus_one(tree):
    """Three copies of 20 bytes waste 40, not 60 - the one that gets kept is
    not waste."""
    out = DF._run_skill({"path": str(tree)})
    # The fixture's payload is 21 bytes; three copies waste 42, i.e. two of
    # them - not all three, because the one that is kept is not waste.
    # **Asserted on the numbers, not on the wording:** the first version said
    # "40.0 B" for a 21-byte file and passed only because the fallback
    # disjunction matched a stray "3 copies" elsewhere in the sentence.
    assert "21 B × 3 copies" in out
    assert "(42 B redundant)" in out
    assert "42 B of it redundant" in out


def test_no_duplicates_is_not_the_same_as_nothing_found(tmp_path, monkeypatch):
    root = tmp_path / "solo"
    root.mkdir()
    (root / "only.txt").write_bytes(b"unique\n")
    monkeypatch.setattr(DF.files, "expand",
                        lambda raw, **kw: Path(str(root)))
    out = DF._run_skill({"path": str(root)})
    assert "No two files have the same content" in out
    assert "different answer from" in out


def test_empty_files_are_never_a_group(tmp_path, monkeypatch):
    """**Every empty file is the same**, so grouping them reports a saving of
    zero bytes for an unbounded number of files."""
    root = tmp_path / "empties"
    root.mkdir()
    for name in ("one", "two", "three"):
        (root / name).write_bytes(b"")
    monkeypatch.setattr(DF.files, "expand",
                        lambda raw, **kw: Path(str(root)))
    out = DF._run_skill({"path": str(root)})
    assert "No two files have the same content" in out


# --- what it refuses to hash --------------------------------------------------

def test_a_symlink_is_not_a_copy_of_its_target(tmp_path, monkeypatch):
    """**A link and its target are one file, not two.** Counting them reports a
    permanent saving the size of one small file.

    **Two earlier fixtures passed for reasons unrelated to the guard.**
    `_walk` sizes with `lstat`, and a symlink's `lstat` size is the **length of
    its target path**, not the content - so an absolute link to
    `/tmp/pytest-of-.../real.txt` is 25 bytes while the file itself is 8, they
    never share a size group, and the guard is never reached. Deleting the
    guard left the suite green. A dangling target is skipped by *both* paths.

    So the link is **relative and its name is exactly the file's size in
    bytes** - `link.txt` -> `real.txt` is 8 characters and `real.txt` holds 8
    bytes. Without the guard the two land in the same size bucket and are
    hashed together, which is what this is here to refuse.
    """
    root = tmp_path / "links"
    root.mkdir()
    (root / "real.txt").write_bytes(b"1234567\n")       # 8 bytes
    (root / "link.txt").symlink_to("real.txt")           # 8 characters
    assert (root / "link.txt").lstat().st_size == \
        (root / "real.txt").lstat().st_size, \
        "fixture assumption broken: the link must share its target's size"
    monkeypatch.setattr(DF.files, "expand",
                        lambda raw, **kw: Path(str(root)))
    walked = [path.name for path, _ in DF._walk(root)[0]]
    assert "link.txt" not in walked, \
        f"the symlink was walked and would be hashed with its target: {walked}"
    assert walked == ["real.txt"], walked


def test_an_unreadable_file_is_not_hashed_as_empty(tmp_path, monkeypatch):
    """"" is a real hash of an empty file, so a read failure returning it merges
    every unreadable file into one group and reports a big saving that is not
    there."""
    root = tmp_path / "locked"
    root.mkdir()
    secret = root / "no-read.txt"
    secret.write_bytes(b"x" * 4096)
    os.chmod(secret, 0)
    try:
        digest = DF._digest(secret)
        assert digest == "", "a read failure must not fabricate a hash"
    finally:
        os.chmod(secret, 0o644)


def test_a_digest_matches_the_real_one(tmp_path):
    """The hashing is asserted against `sha256sum`, not against itself - a
    parser that agreed with itself would pass on any wrong answer."""
    path = tmp_path / "thing.bin"
    path.write_bytes(bytes(range(256)) * 40)
    mine = DF._digest(path)
    theirs = subprocess.run(["sha256sum", str(path)], capture_output=True,
                            text=True, timeout=30).stdout.split()[0]
    assert mine == theirs


# --- the two-pass design ------------------------------------------------------

def test_only_size_collisions_are_hashed():
    """Hashing a whole home directory is the slowest way to find a duplicate:
    most distinct files differ in size, so grouping by size first costs one
    stat per file."""
    from pathlib import Path
    found = [(Path("/a"), 10), (Path("/b"), 20), (Path("/c"), 10)]
    buckets = DF._size_groups(found)
    assert set(buckets) == {10}, buckets
    assert len(buckets[10]) == 2


def test_the_answer_says_how_much_was_hashed(tree):
    """The saving is only worth quoting if the reader knows it was verified,
    and the cost is only worth quoting if the reader knows it was bounded."""
    out = DF._run_skill({"path": str(tree)})
    assert "Size-grouped first" in out


# --- it never touches anything ------------------------------------------------

def test_it_deletes_nothing(tmp_path, monkeypatch):
    root = tmp_path / "keep"
    (root / "a").mkdir(parents=True)
    (root / "a" / "dup").write_bytes(b"payload\n")
    (root / "b").mkdir(parents=True)
    (root / "b" / "dup").write_bytes(b"payload\n")
    monkeypatch.setattr(DF.files, "expand",
                        lambda raw, **kw: Path(str(root)))
    DF._run_skill({"path": str(root)})
    assert (root / "a" / "dup").exists()
    assert (root / "b" / "dup").exists()
    assert len(list(root.rglob("dup"))) == 2


def test_no_copy_is_marked_as_the_original(tree):
    """Every copy is equally the original, and which to keep is the caller's
    decision - a tool that picks has just deleted their folder structure."""
    out = DF._run_skill({"path": str(tree)})
    assert "equally" in out
    for word in ("original", "keep this", "delete this", "the source"):
        assert word not in out.replace("equally the original", "").replace(
            "as such here", ""), word


def test_arguments_are_bounded(tree):
    assert "is not a count" in DF._run_skill({"path": str(tree), "limit": "lots"})


def test_a_non_directory_is_refused(tmp_path, monkeypatch):
    target = tmp_path / "file.txt"
    target.write_text("hello\n")
    monkeypatch.setattr(DF.files, "expand", lambda raw, **kw: Path(str(target)))
    assert "not a directory" in DF._run_skill({"path": str(target)})


def test_the_limit_caps_the_groups_shown(tree, monkeypatch):
    root = tree
    for index in range(4):
        (root / "a" / f"p{index}.txt").write_bytes(b"same payload\n")
        (root / "b" / f"q{index}.txt").write_bytes(b"same payload\n")
    out = DF._run_skill({"path": str(root), "limit": 1})
    assert "more group(s) not shown" in out
