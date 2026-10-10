"""`broken_symlinks`: which shortcuts point at nothing?

Nothing answered it. `find_files` searches names, `get_file_info` describes one
object, and `cleanup_report` counts size - a dangling symlink costs no space, so
every size-based answer misses it while being the usual reason a launcher stops
working.

**Four shapes, and only two are broken.** A link can resolve, resolve to
nothing, cycle, or resolve to something *outside the tree that was searched* -
which is not broken and must not be counted as such.
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

from shani_chronoa.skills import broken_symlinks as BS  # noqa: E402


@pytest.fixture
def four(tmp_path):
    """One link of each kind, all under `root`."""
    root = tmp_path / "tree"
    (root / "sub").mkdir(parents=True)
    (root / "outside.txt").write_bytes(b"outside\n")
    # resolves, and inside the searched tree
    (root / "sub" / "real.txt").write_bytes(b"real\n")
    (root / "sub" / "works.txt").symlink_to("real.txt")
    # resolves to nothing
    (root / "sub" / "dangling.txt").symlink_to("nothere.txt")
    # a two-link cycle
    (root / "sub" / "cyc-a").symlink_to("cyc-b")
    (root / "sub" / "cyc-b").symlink_to("cyc-a")
    # resolves, but to a path OUTSIDE the searched root
    (root / "sub" / "escapes.txt").symlink_to("../outside.txt")
    return root / "sub"


def _bykind(four):
    return BS._walk(four)[0]


# --- the classification --------------------------------------------------------

def test_all_four_kinds_are_distinguished(four):
    by = _bykind(four)
    assert [p.name for p in by["works"]] == ["works.txt"]
    assert [p.name for p in by["dangling"]] == ["dangling.txt"]
    assert set(p.name for p in by["cycle"]) == {"cyc-a", "cyc-b"}
    assert [p.name for p in by["escapes"]] == ["escapes.txt"]


def test_a_link_escaping_the_tree_is_not_broken(four):
    """**The whole safety argument.** `../outside.txt` resolves - to a
    directory the walk did not enter. Counting it as dangling reports a broken
    link for a working one, and the fix would delete something that works."""
    by = _bykind(four)
    assert len(by["escapes"]) == 1
    out = BS._run_skill({"path": str(four)})
    assert "not broken" in out
    # And it is not listed under either broken heading. Asserted on the
    # structured counts rather than on a substring of the prose, because
    # "escapes" appears in the output legitimately.
    assert len(by["dangling"]) + len(by["cycle"]) == 3
    assert "escapes.txt" in out


def test_the_escape_is_never_scheduled_for_removal(four):
    """**It appears, and it is marked not-broken.** A tool that lists it under
    a "Dangling - the target does not exist" heading has just told someone to
    delete a working link."""
    by = _bykind(four)
    assert by["escapes"] == [four / "escapes.txt"]
    out = BS._run_skill({"path": str(four)})
    # **The slice stops at the next heading**, not at the end of the answer:
    # the escapes section legitimately follows the dangling one and does list
    # the link, so a slice to the end matches it and fails for a reason that is
    # not a defect.
    for heading in ("**Dangling**", "**Cyclic**"):
        if heading not in out:
            continue
        body = out.split(heading, 1)[1]
        body = body.split("**", 2)[0]
        assert "escapes.txt" not in body, (
            f"{heading} lists a link that resolves outside the tree")


def test_the_broken_count_excludes_the_escape(four):
    """5 links, of which 3 are broken (1 dangling + 2 cyclic) and 1 escapes the
    tree. **The escape is in neither total.** The first version of this test
    asserted `"3 broken" not in out`, which is backwards - 3 is the right count.
    """
    out = BS._run_skill({"path": str(four)})
    assert "**3 broken**" in out
    assert "(1 pointing at something that does not exist, 2 in a cycle)" in out
    assert "1 point outside it" in out


def test_a_cycle_is_broken_and_says_why(four):
    out = BS._run_skill({"path": str(four)})
    assert "Cyclic" in out
    assert "cyc-a" in out and "cyc-b" in out


def test_a_symlink_to_a_directory_is_seen(four):
    """**`os.walk` puts a symlink that points at a directory in `dirs`, not
    `names`.** Scanning only `names` never sees it - measured on the first
    fixture, a `ln -s .` self-cycle was invisible while every sibling link was
    found. Fixed by walking both lists.
    """
    (four / "dirlink").symlink_to(".")
    seen = BS._walk(four)[0]
    walked = [p.name for kind in seen for p in seen[kind]]
    assert "dirlink" in walked, walked


def test_a_link_to_an_absolute_path_resolves(four):
    target = four / "real.txt"
    (four / "abs.txt").symlink_to(str(target))
    assert "abs.txt" in [p.name for p in _bykind(four)["works"]]


# --- the output shape ----------------------------------------------------------

def test_the_counts_are_stated_before_the_list(four):
    out = BS._run_skill({"path": str(four)})
    assert "symbolic link(s) under" in out
    assert "broken" in out
    assert "not broken" in out
    # The escape count is a number, not a vague phrase.
    assert "1 point outside it" in out


def test_the_target_is_shown_next_to_the_link(four):
    out = BS._run_skill({"path": str(four)})
    assert "dangling.txt  ->  nothere.txt" in out


def test_no_links_is_an_answer_not_a_failure(tmp_path):
    empty = tmp_path / "empty"
    empty.mkdir()
    out = BS._run_skill({"path": str(empty)})
    assert "no symbolic links" in out
    assert "different answer from" in out


def test_a_non_directory_is_refused(tmp_path):
    target = tmp_path / "f.txt"
    target.write_text("x\n")
    assert "not a directory" in BS._run_skill({"path": str(target)})


# --- it removes nothing --------------------------------------------------------

def test_it_removes_nothing(four):
    before = sorted(p.name for p in four.iterdir())
    BS._run_skill({"path": str(four)})
    after = sorted(p.name for p in four.iterdir())
    assert before == after, before


def test_it_says_it_removed_nothing(four):
    assert "has been removed" in BS._run_skill({"path": str(four)})


def test_a_real_system_directory_works(tmp_path, monkeypatch):
    """Run it on a real tree, not only on a fixture - the `dirs` bug was
    invisible on a names-only fixture and visible on /usr/share/doc."""
    monkeypatch.setattr(BS.files, "expand",
                        lambda raw, **kw: Path("/usr/share"))
    out = BS._run_skill({"path": "/usr/share"})
    assert "symbolic link(s) under" in out
    assert "broken" in out


# --- a target that could not be read is not a missing one ----------------------

def test_a_target_that_cannot_be_read_is_not_broken(tmp_path, monkeypatch):
    """EACCES on the target is a fourth state. **The first version compared
    `errno == ELOOP` inline and left `_BROKEN` as dead documentation**, so
    removing ELOOP from it changed nothing and a permission failure read as
    "the target is missing" - which would tell someone to delete a link whose
    target exists and is simply not theirs to read."""
    four_names = ("dangling", "cycle", "unreadable", "escapes", "works")
    root = tmp_path / "perm"
    root.mkdir()
    private = root / "private"
    private.mkdir()
    (private / "target.txt").write_bytes(b"x" * 4096)
    os.chmod(private, 0)
    try:
        (root / "link.txt").symlink_to("private/target.txt")
        by = BS._walk(root)[0]
        assert by["unreadable"], by
        assert by["dangling"] == [], by
        out = BS._run_skill({"path": str(root)})
        assert "could not be read" in out
        # **Not listed as broken, and not listed as dangling.** The first
        # version of this assertion was `"**0 broken**" not in out or "1 point
        # outside" in out` - true of nothing in particular, and it failed only
        # by accident when both halves were false.
        assert "**0 broken**" in out
        assert "Dangling" not in out
        assert "link.txt" not in out.split("unreadable")[0].split("**")[-1]
    finally:
        os.chmod(private, 0o755)


def test_the_ceiling_stops_the_walk_and_says_so(tmp_path, monkeypatch):
    """An unbounded walk over a large tree returns a short list that looks
    complete - the absence-shaped green this package keeps recording."""
    root = tmp_path / "many"
    root.mkdir()
    for index in range(12):
        (root / f"l{index}").symlink_to("nothing-here")
    monkeypatch.setattr(BS, "_CEILING", 5)
    out = BS._run_skill({"path": str(root)})
    assert "Stopped at 5 links" in out
    assert "not a complete list" in out
    # **And asserted on the walk itself, not only on the message.** The ceiling
    # is enforced in two places - the walk stops, and the answer reports - so
    # removing either one alone leaves the suite green. The first version of
    # this test only checked the sentence, which is the reporting half, and the
    # walk-side half was therefore equivalent to nothing.
    by, seen = BS._walk(root)
    assert seen == 5, f"the walk did not stop: it saw {seen}"
