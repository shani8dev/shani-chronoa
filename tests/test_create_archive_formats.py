"""`create_archive`: the three formats that need no binary.

`create_archive` refused `tar.bz2`, `tar.xz` and `tar.zst` with *"Format must be
zip, tar.gz or 7z"* - on a machine that could have written all three, because
`tarfile` handles bz2 and xz and `compression.zstd` is stdlib from Python 3.14.
A skill that needs nothing refused formats it could produce.
"""
from __future__ import annotations

import pathlib
import sys
import tarfile
import tempfile
from pathlib import Path

import pytest

_REPO = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_REPO / "usr" / "lib" / "shani-chronoa"))

from shani_chronoa.skills import create_archive as CA  # noqa: E402

#: The formats that cost nothing, and the tarfile mode each maps to.
_STDLIB = {
    "tar.bz2": "bz2", "tar.bz": "bz2",
    "tar.xz": "xz",
    "tar.zst": "zst", "tar.zstd": "zst",
}


@pytest.fixture
def source(tmp_path):
    root = tmp_path / "src"
    root.mkdir()
    (root / "a.txt").write_bytes(b"first\n")
    (root / "nested").mkdir()
    (root / "nested" / "b.txt").write_bytes(b"second\n")
    return root


def _run(action, source_path=None, destination=None, fmt=None):
    arguments = {"action": action}
    if source_path is not None:
        arguments["source"] = str(source_path)
    if destination is not None:
        arguments["destination"] = str(destination)
    if fmt is not None:
        arguments["format"] = fmt
    return CA._run(arguments)


# --- they are accepted, and they are written by tarfile -----------------------

@pytest.mark.parametrize("fmt,mode", sorted(_STDLIB.items()))
def test_each_format_is_accepted_and_written(tmp_path, source, fmt, mode):
    target = tmp_path / ("out." + fmt)
    out = _run("create", source, target, fmt)
    assert "Format must be" not in out, out
    assert target.exists(), out
    # **Read back with tarfile's own reader**, not with the skill's - so the
    # assertion does not agree with a wrong writer by construction.
    with tarfile.open(target, "r:" + mode) as archive:
        names = archive.getnames()
    # **Prefixed with the source directory's name**, which is the existing and
    # intended behaviour - the archive holds the directory, not its contents.
    # Asserted as the real arcname so a change to that prefix is visible.
    assert "src/a.txt" in names, names
    assert "src/nested/b.txt" in names, names


@pytest.mark.parametrize("fmt,mode", sorted(_STDLIB.items()))
def test_the_mode_matches_the_table(tmp_path, source, fmt, mode):
    """**The writer keys on the table, not on the `tar` prefix.** A plain tar and
    three compressions share that prefix, so prefix-keying silently gzipped a
    `tar.bz2` request - a file whose name says one thing and whose bytes say
    another."""
    target = tmp_path / ("mode." + fmt)
    _run("create", source, target, fmt)
    assert CA._COMPRESSION[fmt] == mode
    if mode == "bz2":
        assert target.read_bytes()[:3] == b"BZh", target.read_bytes()[:8]
    elif mode == "xz":
        assert target.read_bytes()[:6] == b"\xfd7zXZ\x00", target.read_bytes()[:8]
    elif mode == "zst":
        assert target.read_bytes()[:4] == b"\x28\xb5\x2f\xfd", target.read_bytes()[:8]


def test_a_plain_tar_is_still_uncompressed(tmp_path, source):
    target = tmp_path / "plain.tar"
    _run("create", source, target, "tar")
    assert CA._COMPRESSION["tar"] is None
    assert target.read_bytes()[:2] != b"\x1f\x8b"


def test_tar_gz_still_gzips(tmp_path, source):
    target = tmp_path / "keep.tar.gz"
    _run("create", source, target, "tar.gz")
    assert target.read_bytes()[:2] == b"\x1f\x8b"


# --- the round trip through the skill itself ----------------------------------

@pytest.mark.parametrize("fmt", sorted(_STDLIB))
def test_list_and_extract_agree(tmp_path, source, fmt):
    """**Through the skill, not through tarfile.** `tarfile.open(path)` without a
    mode sniffs the compression, so a reader that works for all three is not
    evidence the writer produced the right one - the table check above is, and
    this is the answer's own claim."""
    target = tmp_path / ("round." + fmt)
    _run("create", source, target, fmt)

    listing = _run("list", target)
    assert "src/a.txt" in listing, listing
    assert "src/nested/b.txt" in listing, listing

    out_dir = tmp_path / ("out-" + fmt)
    _run("extract", target, out_dir)
    extracted = sorted(p.relative_to(out_dir).as_posix()
                       for p in out_dir.rglob("*") if p.is_file())
    assert extracted, "nothing was extracted"
    extracted_root = out_dir / "src"
    assert (extracted_root / "a.txt").read_bytes() == b"first\n", extracted


def test_an_unknown_format_lists_what_is_accepted(tmp_path, source):
    """The refusal names the whole set, so the answer to "what can you write" is
    in the error."""
    out = _run("create", source, tmp_path / "x.rar", "rar")
    assert "Format must be" in out
    for fmt in _STDLIB:
        assert fmt in out, f"the refusal does not mention {fmt}"
    assert "zip" in out and "7z" in out


# --- and the old behaviour is preserved ---------------------------------------

def test_zip_still_works(tmp_path, source):
    target = tmp_path / "still.zip"
    _run("create", source, target, "zip")
    assert target.read_bytes()[:2] == b"PK"


def test_7z_still_needs_the_binary(tmp_path, source, monkeypatch):
    """7z is the one format here that shells out, and its absence must be a
    refusal naming the package rather than a silent empty archive."""
    monkeypatch.setattr(CA.shutil, "which", lambda name: None)
    out = _run("create", source, tmp_path / "seven.7z", "7z")
    assert "7z" in out
    assert "not installed" in out or "tool_missing" in out.lower(), out


def test_a_format_needing_no_binary_works_with_an_empty_path(tmp_path, source,
                                                            monkeypatch):
    """**The whole point, stated as a test.** Three formats that need no binary
    must not be refused for a binary's absence.
    """
    monkeypatch.setattr(CA.shutil, "which", lambda name: None)
    target = tmp_path / "nobin.tar.xz"
    out = _run("create", source, target, "tar.xz")
    assert "not installed" not in out, out
    assert target.exists(), out
