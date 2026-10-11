"""`temp_files`: what junk, temporary or leftover files are in here?

`find_files` answers what a *name* matches and `stale_files` what is big and
untouched; this answers the question a person has when they open a folder they
have not tidied in a year. The whole answer rests on names, so the suite's job
is to pin the two places that resting is wrong - **a name is a guess** and
**an unreadable path is not a clean one** - plus every rule the answer prints.

Measured on the dev box this was written on, which is where the two caveats
come from rather than from imagination:

- `~/.gnupg/pubring.kbx~` is GnuPG's backup of the keyring and the only copy
  of it, and a `*~` rule reports it.
- 50 files under `$HOME` are named `core` or `core.<something>` and **none**
  is a core dump - they are `core.js`, `core.ts`, `core.cjs` and directories
  called `core`. That is why the core family reads the ELF header instead of
  stopping at the name, and why "could not read" is kept apart from "not a
  dump".
"""
from __future__ import annotations

import os
import pathlib
import struct
import sys
from pathlib import Path

import pytest

_REPO = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_REPO / "usr" / "lib" / "shani-chronoa"))

from shani_chronoa.skills import temp_files as TF  # noqa: E402


# --- helpers -------------------------------------------------------------------

def _elf(e_type: int, ei_class: int = 2, ei_data: int = 1) -> bytes:
    """A minimal ELF header with `e_type` set, in the class and endianness
    given. Built from the published layout so the test can state which one it
    is exercising; the parser's own offsets were measured against real ELF
    files on this box (`/bin/ls` reads ET_DYN, `/usr/bin/python3` ET_EXEC).
    """
    head = bytearray(64)
    head[0:4] = b"\x7fELF"
    head[4] = ei_class
    head[5] = ei_data
    head[6] = 1                                    # EI_VERSION
    struct.pack_into(("<" if ei_data == 1 else ">") + "H", head, 16, e_type)
    return bytes(head)


@pytest.fixture
def tree(tmp_path):
    """A directory holding one file for every name family, plus the four
    near-misses that must not be reported.

    **Not `tmp_path / "home"`** - the autouse `_hermetic_env` fixture already
    makes `$HOME` that directory and creates `.config` inside it, so a fixture
    rooted there walks a directory it did not create. The first version of this
    file did exactly that and `test_nothing_is_deleted_moved_or_changed` failed
    with `IsADirectoryError` on `.config`, which reads as a leak in the skill.
    """
    root = tmp_path / "junk"
    root.mkdir(parents=True, exist_ok=True)
    junk = [
        "a.tmp", "b.swp", "c.swo", "d.kate-swp", "e~",      # editor
        ".#f", "~$g.docx", ".goutputstream-ABC123",          # lock / atomic save
        ".DS_Store", "thumbs.db", "desktop.ini",             # OS metadata
        "h.crdownload", "i.part", "j.filepart",              # downloads
        "k.orig", "l.rej",                                   # merge leftovers
    ]
    for name in junk:
        (root / name).write_bytes(b"x" * 32)
    # `core.tmp` is an editor leftover by name and never a dump candidate: the
    # first matching family claims it, so no ELF read happens.
    (root / "core.tmp").write_bytes(b"x" * 8)
    for name in ("notes.txt", "readme"):
        (root / name).write_bytes(b"x" * 8)
    return root


def _listed(out):
    """The paths the answer actually listed, from the `- <size>  <path>` rows.

    The **order** is not asserted from this, because `os.walk` yields names in
    directory order and the filesystem does not guarantee it;
    `test_the_biggest_come_first` pins the size ordering with distinct sizes.
    """
    out_names = []
    for line in out.splitlines():
        if not line.startswith("- "):
            continue
        fields = [part for part in line[2:].split("  ") if part.strip()]
        if len(fields) >= 2:
            out_names.append(os.path.basename(fields[1].strip()))
    return out_names


# --- every name family is found -------------------------------------------------

def test_every_name_family_is_reported(tree):
    out = TF._run_skill({"path": str(tree)})
    assert set(_listed(out)) == {
        "a.tmp", "b.swp", "c.swo", "d.kate-swp", "e~", "core.tmp",
        ".#f", "~$g.docx", ".goutputstream-ABC123",
        ".DS_Store", "thumbs.db", "desktop.ini",
        "h.crdownload", "i.part", "j.filepart",
        "k.orig", "l.rej",
    }, out
    assert len(_listed(out)) == 17


def test_the_biggest_come_first(tmp_path):
    """Size descending, so the listing is useful as a work list. Asserted with
    distinct sizes because directory order is not something to rely on."""
    root = tmp_path / "sized"
    root.mkdir()
    (root / "small.tmp").write_bytes(b"x" * 100)
    (root / "big.tmp").write_bytes(b"x" * 900)
    (root / "middle.tmp").write_bytes(b"x" * 400)
    out = TF._run_skill({"path": str(root)})
    assert _listed(out) == ["big.tmp", "middle.tmp", "small.tmp"], out


def test_the_family_of_each_hit_is_labelled(tree):
    """A reader has to be able to tell which rule fired, because the rule is
    the only evidence and they are invited to disagree with it."""
    out = TF._run_skill({"path": str(tree)})
    assert "a.tmp  [editor/save leftovers]" in out
    assert ".#f  [lock and atomic-save leftovers]" in out
    assert ".DS_Store  [OS metadata]" in out
    assert "i.part  [unfinished downloads]" in out
    assert "k.orig  [merge leftovers]" in out


def test_the_family_counts_add_up_to_the_total(tree):
    """One file, one family: the first rule that matches wins, so the tally
    can never double-count a file into two buckets."""
    out = TF._run_skill({"path": str(tree)})
    assert "editor/save leftovers 6, lock and atomic-save leftovers 3" in out
    assert "OS metadata 3, unfinished downloads 3, merge leftovers 2" in out


def test_a_plain_file_is_not_junk(tree):
    out = TF._run_skill({"path": str(tree)})
    assert "notes.txt" not in _listed(out)


def test_a_file_named_core_tmp_is_an_editor_leftover_not_a_dump(tree):
    """`core.tmp` ends in `.tmp`, so the first rule claims it and no ELF read
    happens. The alternative - checking the core rule first - would open a file
    for no reason and report it under a family its name does not claim."""
    out = TF._run_skill({"path": str(tree)})
    assert "core.tmp" in _listed(out)
    assert "[editor/save leftovers]" in out
    assert "Core dumps" not in out


# --- the core-dump rule reads bytes, and says when the name was wrong -----------

def test_a_real_core_dump_is_reported(tmp_path):
    root = tmp_path / "dumps"
    root.mkdir()
    (root / "core.4211").write_bytes(_elf(4))          # ET_CORE
    (root / "core.so").write_bytes(_elf(3))            # ET_DYN: an ELF, not a dump
    (root / "core.o").write_bytes(_elf(1))             # ET_REL: an object file
    (root / "core.js").write_text("module.exports = {}\n")
    out = TF._run_skill({"path": str(root)})
    assert _listed(out) == ["core.4211"], out
    assert "[core dumps (bytes checked)]" in out
    assert "1 of the 4 file(s) named like one are ELF core files" in out
    assert "disagreed with the name on the other 3" in out


def test_a_core_dump_is_read_big_endian_too(tmp_path):
    """`e_ident[5]` selects the byte order; a parser that assumes little-endian
    reads a garbage `e_type` from a big-endian file and says 'not a dump'."""
    root = tmp_path / "be"
    root.mkdir()
    (root / "core.7").write_bytes(_elf(4, ei_data=2))
    out = TF._run_skill({"path": str(root)})
    assert _listed(out) == ["core.7"], out


def test_a_directory_called_core_is_not_a_dump(tmp_path):
    """The measured false positive: 26 name matches on this box are
    directories or links, and reporting them as junk files would be wrong in
    kind, not just in number."""
    root = tmp_path / "proj"
    (root / "core").mkdir(parents=True)
    (root / "core" / "index.js").write_text("x\n")
    (root / "core.js").write_text("module.exports = {}\n")
    out = TF._run_skill({"path": str(root)})
    assert _listed(out) == [], out
    assert "are directories or links rather than files" in out
    # One: the directory. `core.js` is a plain file, so it is counted by the
    # core rule instead - the two exclusions are kept apart because they are
    # different findings.
    assert "1 path(s) matched a name above" in out
    assert "none of the 1 file(s) named like one is an ELF core file" in out


def test_a_core_file_that_cannot_be_read_is_not_counted_as_clean(tmp_path):
    """**The load-bearing honesty test.** A file named `core` this cannot open
    is not a file with no junk in it, so it must land in "could not be read"
    and never in "the bytes said it is not a dump". The first version folded
    the two together, which turns a permission failure into a clean result -
    the absence-shaped green this package keeps refusing.
    """
    root = tmp_path / "locked"
    root.mkdir()
    victim = root / "core.99"
    victim.write_bytes(_elf(4))
    os.chmod(victim, 0)
    try:
        out = TF._run_skill({"path": str(root)})
        assert _listed(out) == [], out
        assert "Core dumps" not in out, out
        assert "could not be read" in out
        assert "is not a file that does not exist" in out
    finally:
        os.chmod(victim, 0o600)


def test_a_core_file_with_no_elf_header_is_not_a_dump(tmp_path):
    root = tmp_path / "plain"
    root.mkdir()
    (root / "core").write_bytes(b"just some text, definitely not ELF\n")
    out = TF._run_skill({"path": str(root)})
    assert _listed(out) == []
    assert "none of the 1 file(s) named like one is an ELF core file" in out


def test_a_non_elf_file_whose_type_bytes_say_core_is_still_not_a_dump(tmp_path):
    """**The negative control for the magic check.** The two bytes at offset 16
    say `ET_CORE` here, so a reader that skips the `\\x7fELF` magic and parses
    `e_type` directly reports a dump. Without this fixture the magic check is
    untested: every other non-ELF fixture is either too short to reach offset
    16 or decodes to some other type, so removing the magic changes nothing the
    suite measures.
    """
    root = tmp_path / "fake"
    root.mkdir()
    fake = bytearray(b"\x00" * 18)
    fake[5] = 1                                   # EI_DATA = little-endian
    struct.pack_into("<H", fake, 16, 4)          # ET_CORE, but no ELF magic
    (root / "core.5").write_bytes(bytes(fake))
    out = TF._run_skill({"path": str(root)})
    assert _listed(out) == [], out
    assert "none of the 1 file(s) named like one is an ELF core file" in out


def test_the_core_verdict_distinguishes_the_three_answers():
    """`True` is a dump, `False` is a file the name was wrong about, and
    `None` is "could not read" - three states, because two cannot carry the
    difference between a wrong guess and an absent answer."""
    here = Path(os.path.abspath(__file__)).parent
    assert TF._core_verdict(here / "does-not-exist-at-all") is None
    dumps = Path("/tmp") / "chronoa-temp-files-verdict-probe"
    dumps.mkdir(parents=True, exist_ok=True)
    try:
        (dumps / "real").write_bytes(_elf(4))
        (dumps / "short").write_bytes(b"\x7fELF")
        (dumps / "text").write_bytes(b"hello\n")
        assert TF._core_verdict(dumps / "real") is True
        assert TF._core_verdict(dumps / "short") is False
        assert TF._core_verdict(dumps / "text") is False
        os.chmod(dumps / "real", 0)
        try:
            assert TF._core_verdict(dumps / "real") is None
        finally:
            os.chmod(dumps / "real", 0o600)
    finally:
        import shutil
        shutil.rmtree(dumps, ignore_errors=True)


# --- the guess is stated, and nothing is touched ---------------------------------

def test_the_answer_says_the_name_is_a_guess(tree):
    out = TF._run_skill({"path": str(tree)})
    assert "A name is a guess, and it is the only thing that was checked" in out
    assert "because of what it is called" in out


def test_the_answer_names_a_measured_way_the_name_is_wrong(tree):
    """The two measured false positives, so the caveat is a fact about a real
    machine rather than a disclaimer: GnuPG's keyring backup, and 50 files
    named `core` that are all JavaScript/TypeScript modules."""
    out = TF._run_skill({"path": str(tree)})
    assert "pubring.kbx~" in out
    assert "the only copy of it" in out
    assert "50 files there are named `core`" in out


def test_nothing_is_deleted_moved_or_changed(tree):
    before = {p.name: (p.read_bytes(), p.stat().st_mtime_ns) for p in tree.iterdir()}
    TF._run_skill({"path": str(tree)})
    after = {p.name: (p.read_bytes(), p.stat().st_mtime_ns) for p in tree.iterdir()}
    assert after == before


def test_the_name_rules_are_printed_with_the_answer(tree):
    """The rules are the whole basis for the answer, so a reader who thinks
    `*.tmp` is meaningless on their machine can see the judgement rather than
    inherit it."""
    out = TF._run_skill({"path": str(tree)})
    assert "The name rules, so you can disagree with them:" in out
    assert "editor/save leftovers: *.tmp, *.swp, *.swo, *.kate-swp, *~" in out
    assert "OS metadata: .DS_Store, Thumbs.db, desktop.ini" in out
    assert "core dumps (bytes checked): core, core.*" in out


def test_nothing_found_is_a_statement_about_names(tmp_path):
    """Not a promise the tree is clean: a leftover called `notes.txt` is
    invisible to every rule here."""
    root = tmp_path / "tidy"
    root.mkdir()
    (root / "notes.txt").write_text("x\n")
    (root / "important.py").write_text("x\n")
    out = TF._run_skill({"path": str(root)})
    assert "No file under" in out
    assert "not a promise the tree is clean" in out
    assert "A leftover called `notes.txt` is invisible to this" in out
    assert "file(s) examined" in out


# --- a link and its target are one file ------------------------------------------

def test_a_symlink_named_like_junk_is_not_counted_twice(tmp_path):
    """`space_by_type` measured this: `stat` follows the link, so a link and
    its target are counted as two files with the same size."""
    root = tmp_path / "links"
    root.mkdir()
    (root / "plain.txt").write_text("x\n")
    (root / "backup.tmp").symlink_to(root / "plain.txt")
    out = TF._run_skill({"path": str(root)})
    assert _listed(out) == [], out
    assert "1 path(s) matched a name above" in out


# --- the walk's own limits -------------------------------------------------------

def test_an_unentered_directory_is_named_not_dropped(tmp_path):
    """A directory this cannot enter must not shrink the answer into
    "nothing here" - the mode-000 case `stale_files` measured at 10 MiB for a
    tree holding 50."""
    root = tmp_path / "locked"
    root.mkdir()
    private = root / "noaccess"
    private.mkdir()
    (private / "hidden.tmp").write_bytes(b"x" * 32)
    os.chmod(private, 0)
    try:
        out = TF._run_skill({"path": str(root)})
        assert "could not be entered" in out
        assert "noaccess" in out
        assert "not in these figures" in out
        # The same failure is also counted as an unreadable path, so a run
        # that reports the directory but forgets the count is caught here.
        assert "path(s) could not be read" in out
    finally:
        os.chmod(private, 0o755)


def test_the_ceiling_stops_the_walk(tmp_path, monkeypatch):
    """A bounded walk must say it was bounded: a short list is exactly the
    shape of a complete one."""
    root = tmp_path / "many"
    root.mkdir()
    for index in range(9):
        (root / f"f{index}.tmp").write_bytes(b"x" * 16)
    monkeypatch.setattr(TF, "_CEILING", 5)
    out = TF._run_skill({"path": str(root)})
    assert "Stopped after 5 file(s)" in out
    assert "not a complete list" in out


def test_a_non_directory_is_refused(tmp_path):
    target = tmp_path / "f.txt"
    target.write_text("x\n")
    assert "is not a directory" in TF._run_skill({"path": str(target)})


def test_argument_bounds(tree):
    assert "is not a count" in TF._run_skill(
        {"path": str(tree), "limit": "lots"})
    # An explicit `0` is **not** the default: `0 or 25` would turn "show me
    # nothing" into the default with nothing said, which is the trap
    # `stale_files` records for `older_than_days`. It is clamped to 1 instead,
    # because a listing of zero rows is the shape of "there is no junk here" -
    # the absence this package refuses to present as an answer. The withheld
    # count still says what was left out, so nothing is hidden either way.
    for nothing in (0, -3):
        out = TF._run_skill({"path": str(tree), "limit": nothing})
        assert len(_listed(out)) == 1, out
        assert "and 16 more files not shown" in out


def test_the_limit_is_clamped(tree, monkeypatch):
    monkeypatch.setattr(TF, "_LIMIT_MAX", 3)
    out = TF._run_skill({"path": str(tree), "limit": 500})
    assert len(_listed(out)) == 3
    assert "and 14 more files not shown" in out


def test_the_limit_is_honoured_within_bounds(tree):
    out = TF._run_skill({"path": str(tree), "limit": 4})
    assert len(_listed(out)) == 4
    assert "and 13 more files not shown" in out
    assert "Raise limit, or narrow the path." in out


def test_the_default_limit_is_used_when_the_argument_is_absent(tmp_path):
    root = tmp_path / "many"
    root.mkdir()
    for index in range(TF._DEFAULT_LIMIT + 4):
        (root / f"f{index}.tmp").write_bytes(b"x" * 16)
    out = TF._run_skill({"path": str(root)})
    assert len(_listed(out)) == TF._DEFAULT_LIMIT
    assert "and 4 more files not shown" in out
