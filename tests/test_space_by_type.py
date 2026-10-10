"""`space_by_type`: what is eating my disk, by *kind* of file?

`disk_usage` answers per **directory**, `stale_files` per **age**. The question
after either is by *kind* - "is it the videos or the documents?" - and that cuts
across directories. A 180 GB `Documents` that is 95% `.pdf` is a different
problem from one that is 95% `.docx`.
"""
from __future__ import annotations

import os
import pathlib
import sys
from pathlib import Path

import pytest

_REPO = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_REPO / "usr" / "lib" / "shani-chronoa"))

from shani_chronoa.skills import space_by_type as ST  # noqa: E402

_MIB = 1024 * 1024


@pytest.fixture
def tree(tmp_path):
    """One file in several categories, at known sizes."""
    root = tmp_path / "home"
    root.mkdir(parents=True, exist_ok=True)
    cases = {
        "holiday.mp4": _MIB * 500,
        "notes.pdf": _MIB * 40,
        "photo.jpg": _MIB * 20,
        "song.mp3": _MIB * 8,
        "code.py": _MIB * 2,
        "backup.tar.gz": _MIB * 30,
        "mystery.xyz": _MIB * 10,      # unrecognised suffix
        "noextension": _MIB * 4,       # no suffix at all
    }
    for name, size in cases.items():
        (root / name).write_bytes(b"x" * size)
    return root


def _line_for(out, needle):
    for line in out.splitlines():
        if needle in line:
            return line
    return ""


# --- the categories, and what a suffix maps to --------------------------------

@pytest.mark.parametrize("suffix,category", [
    ("mp4", "video"), ("mkv", "video"),
    ("mp3", "audio"), ("flac", "audio"),
    ("jpg", "image"), ("png", "image"),
    ("pdf", "document"), ("docx", "document"),
    ("tar", "archive"), ("zip", "archive"),
    ("py", "code"), ("json", "code"),
    ("xyz", "other"), ("", "other"),
])
def test_a_suffix_maps_to_its_category(suffix, category):
    assert ST._category(suffix) == category


def test_the_last_suffix_is_the_one_counted():
    """`notes.2024.txt` is text - the last suffix is the claim the name is
    making, and the first would have counted it as `.2024`."""
    assert ST._suffix(Path("notes.2024.txt")) == "txt"
    assert ST._suffix(Path("archive.tar.gz")) == "gz"
    assert ST._suffix(Path("plainfile")) == ""


def test_a_dotfile_has_no_suffix():
    """`.bashrc` is not a file called `bashrc` of type `bashrc`."""
    assert ST._suffix(Path(".bashrc")) == ""


# --- the answer -----------------------------------------------------------------

def test_every_file_is_counted_once(tree):
    """The categories plus `other` must sum to the total, or something was
    counted twice or dropped."""
    sizes, counts, seen, skipped, truncated, unentered = ST._walk(tree)
    assert seen == 8, seen
    assert unentered == [], unentered
    assert sum(counts.values()) == seen, counts
    assert sum(sizes.values()) == sum(
        (tree / n).stat().st_size for n in
        ("holiday.mp4", "notes.pdf", "photo.jpg", "song.mp3", "code.py",
         "backup.tar.gz", "mystery.xyz", "noextension"))


def test_the_largest_category_is_named(tree):
    out = ST._run_skill({"path": str(tree)})
    assert "**video** is the largest category" in out
    assert "500.0 MiB" in out


def test_a_category_with_no_files_is_absent_not_zero(tree):
    """A zero row for every category nobody has is noise; the answer leads with
    what is there."""
    out = ST._run_skill({"path": str(tree)})
    assert "database" not in out.split("`other`")[0]


def test_other_is_reported_loudly_not_hidden(tree):
    """**The category a reader should distrust is the one they must see.** A
    machine whose files are mostly unlabelled should say so, because that is
    where the answer is least useful."""
    out = ST._run_skill({"path": str(tree)})
    assert "`other`" in out
    assert "no suffix this recognises" in out
    assert "not a fact about those files" in out


def test_the_rules_are_shown_with_the_answer(tree):
    """The mapping is a judgement, not a fact, so it is printed - a reader can
    disagree with it rather than trust it."""
    out = ST._run_skill({"path": str(tree)})
    assert "The categories come from these suffixes:" in out
    # **Asserted on suffixes the truncated list actually shows.** Each category
    # prints its first six *alphabetically*, so `.pdf` (8th in `document`) and
    # `.mp4` (7th in `video`) are both cut off. The first version asserted
    # `.pdf`, which failed on behaviour working exactly as designed - a test
    # that fails when the code is right will be weakened, not fixed.
    assert ".avi" in out, "the video line should be there"
    assert ".csv" in out, "the document line should be there"
    assert "..." in out, "the list should say it is truncated"
    assert "other" not in out.split("The categories come from")[1] or \
        "no suffix this recognises" in out


def test_the_percentages_sum_to_about_one_hundred(tree):
    out = ST._run_skill({"path": str(tree)})
    shares = []
    for line in out.splitlines():
        fields = line.split()
        # `  video           7.1 KiB    0.0%  2 file(s)` - the percentage is the
        # field ending in %, not a fixed index. The first version hardcoded
        # index 2 and read "MiB".
        for field in fields:
            if field.endswith("%"):
                shares.append(float(field[:-1]))
                break
    assert 99.0 <= sum(shares) <= 101.0, shares


# --- the honest limits --------------------------------------------------------

def test_an_unreadable_directory_is_counted(tmp_path):
    """A directory this cannot enter must not silently shrink the total."""
    root = tmp_path / "mixed"
    root.mkdir()
    (root / "real.mp4").write_bytes(b"x" * (_MIB * 10))
    private = root / "private"
    private.mkdir()
    (private / "hidden.mp4").write_bytes(b"x" * (_MIB * 50))
    os.chmod(private, 0)
    try:
        out = ST._run_skill({"path": str(root)})
        # **"could not be entered", not "could not be read".** The first version
        # asserted the latter - which is a *different* line, the skipped-paths
        # count - so the test passed whether or not the unentered-directory
        # tracking ran at all. Deleting that tracking left the suite green, and
        # the assertion it was written to protect was never really checking it.
        assert "could not be entered" in out, out
        assert "private" in out, "the directory must be named, not counted"
    finally:
        os.chmod(private, 0o755)


def test_no_files_is_not_the_same_as_empty(tmp_path):
    root = tmp_path / "none"
    root.mkdir()
    out = ST._run_skill({"path": str(root)})
    assert "not the same as it being empty" in out


def test_arguments_and_paths_are_handled(tmp_path):
    target = tmp_path / "f.txt"
    target.write_text("x\n")
    assert "not a directory" in ST._run_skill({"path": str(target)})


def test_a_symbolic_link_is_not_double_counted(tmp_path):
    """A link and its target are one file. Counting both would put a category's
    number up without the space changing."""
    root = tmp_path / "links"
    root.mkdir()
    (root / "real.mp4").write_bytes(b"x" * (_MIB * 10))
    (root / "link.mp4").symlink_to("real.mp4")
    sizes, counts, seen, _skipped, _trunc, _un = ST._walk(root)
    assert seen == 1, seen
    assert counts["video"] == 1
    # And the size is the one file's, not two.
    assert sizes["video"] == (root / "real.mp4").stat().st_size


# --- one equivalent mutant, recorded rather than hidden -----------------------
# **`lstat` versus `stat` makes no difference here, and a mutation proving it
# leaves the suite green.** The `islink` check runs first, so a symlink is
# skipped whichever call produced the `stat` result - and `lstat` is retained
# because a stat whose result is discarded on the next line should not follow a
# link in the first place, not because it changes the answer.
#
# The mutation that DOES matter is the one removing the `islink` check, which
# fails this file. The two edits were made together, and only one of them is
# load-bearing: that is the shape worth recording, because "I changed two things
# and the tests went green" is not the same as "both changes were needed".
