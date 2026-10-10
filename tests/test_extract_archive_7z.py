"""`extract_archive` and the formats Python's stdlib cannot read.

tar and zip were the whole answer: anything else - `.7z`, `.rar`, `.cab` -
was refused with *"it is neither a tar archive nor a zip file"*, a true
sentence and a dead end. `7z` ships on both images and reads all of them.

**The reason this is not a two-line delegation was measured on a real slot**
(`shani-testbed` `chronoa-cli-formats.sh`, `@blue`, 2026-10-10): `7z x`
extracts a **symlink member with exit 0 and no warning at all**. So handing
the archive to 7z without checking it first would write outside the
destination - the exact thing the tar and zip branches already refuse. The
whole archive is therefore listed through `7z l -slt` and checked by the same
model before anything is extracted.

**`-slt`'s output has a trap the same measurement caught.** It prints one
`Key = value` block per member **and a first block describing the archive
itself**, whose `Path =` is the archive:

    --
    Path = /tmp/round.7z      <- the archive, NOT a member
    Type = 7z
    Physical Size = 208
    ----------
    Path = seven              <- members start here
    Attributes = D drwxr-xr-x

Parsing every `Path =` as a member reports the archive as its own first member.
"""

from __future__ import annotations

import os
import pathlib
import sys

import pytest

_REPO = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_REPO / "usr" / "lib" / "shani-chronoa"))

from shani_chronoa.skills import extract_archive as E  # noqa: E402

#: `7z l -slt`'s real output, captured on the slot: the archive's own block,
#: then a directory, then two files. 7z 26.03 on the image.
CAPTURED_SLT = """7-Zip 26.03 (x64) : Copyright (c) 1999-2026 Igor Pavlov : 2026-09-03
 64-bit locale=C.UTF-8 Threads:8 OPEN_MAX:4096, ASM

Scanning the drive for archives:
 1 file, 208 bytes (1 KiB)

Listing archive: /var/tmp/round.7z

--
Path = /var/tmp/round.7z
Type = 7z
Physical Size = 208
Headers Size = 175
Method = LZMA2:12
Solid = +
Blocks = 1

----------
Path = seven
Size = 0
Packed Size = 0
Modified = 2026-10-10 03:17:41.0189012
Attributes = D drwxr-xr-x
CRC =
Encrypted = -
Method =
Block =

Path = seven/one.txt
Size = 17
Packed Size = 33
Modified = 2026-10-10 03:17:41.0189012
Attributes = A -rw-r--r--
CRC = 05808A9F
Encrypted = -
Method = LZMA2:12
Block = 0

Path = seven/two.txt
Size = 17
Packed Size = 33
Modified = 2026-10-10 03:17:41.0189012
Attributes = A -rw-r--r--
CRC = 05808A9F
Encrypted = -
Method = LZMA2:12
Block = 0
"""

#: The same listing with a **named** symlink member appended, as the hostile
#: case measures it. Built by appending a whole member block rather than by
#: editing one in place: the first version of this fixture rewrote a file's
#. `Attributes` to `lrwxrwxrwx` but left its name, so the refusal correctly
#: named `seven/one.txt` and the assertion for `escape` failed against a
#: fixture that had never made one.
CAPTURED_SLT_WITH_LINK = CAPTURED_SLT + """
Path = seven/escape
Size = 11
Packed Size = 11
Modified = 2026-10-10 03:17:41.0189012
Attributes = A lrwxrwxrwx
Encrypted = -
Method = LZMA2:12
Block = 0
"""


@pytest.fixture
def fake_7z(tmp_path, monkeypatch):
    """A stand-in `7z` answering `l -slt` from a file, recording its argv.

    **The listing file is rewritten in place by `set_listing`**, not captured
    when the fixture is installed: the first version wrote the text into the
    script's own state at install time, so a test that changed the listing
    afterwards changed nothing and every "refuses X" case passed vacuously.
    """
    bindir = tmp_path / "bin"
    bindir.mkdir(exist_ok=True)
    data = tmp_path / "slt.txt"
    data.write_text(CAPTURED_SLT)
    script = bindir / "7z"
    script.write_text(
        "#!/usr/bin/env python3\n"
        "import json, os, sys\n"
        f"open({str(tmp_path / 'state.json')!r}, 'a').write(json.dumps(sys.argv[1:]) + '\\n')\n"
        f"if sys.argv[1:3] == ['l', '-slt']:\n"
        f"    print(open({str(data)!r}).read(), end='')\n"
        f"    raise SystemExit(int(os.environ.get('FAKE_7Z_RC', '0')))\n"
        "raise SystemExit(0)\n"
    )
    script.chmod(0o755)
    (tmp_path / "state.json").write_text("")
    monkeypatch.setenv("PATH", f"{bindir}:{os.environ['PATH']}")

    def set_listing(text, code=0):
        data.write_text(text)
        monkeypatch.setenv("FAKE_7Z_RC", str(code))

    return set_listing


def test_the_archives_own_block_is_not_a_member(tmp_path, monkeypatch, fake_7z):
    """**The measured trap.** Reading every `Path =` reports the archive as its
    own first member - which then fails the safety check against the archive's
    real name, or worse, never fails at all if the name is harmless.
    """
    dest = tmp_path / "out"
    dest.mkdir()
    refusal, names, total = E._sevenzip_members(tmp_path / "round.7z", dest)
    assert refusal is None
    assert names == ["seven/one.txt", "seven/two.txt"], names
    assert total == 3, total          # two files plus the directory
    assert not any("round.7z" in n for n in names), names


def test_a_symlink_member_is_refused_before_anything_is_written(tmp_path, monkeypatch,
                                                                fake_7z):
    """**The reason the listing is read first.** `7z x` extracts a link with
    rc=0 and no warning, so the refusal has to come from here.
    """
    set_listing = fake_7z
    set_listing(CAPTURED_SLT_WITH_LINK)
    dest = tmp_path / "out"
    dest.mkdir()
    refusal, names, _total = E._sevenzip_members(tmp_path / "evil.7z", dest)
    assert refusal is not None, "a symlink member was accepted"
    assert "symlink" in refusal
    assert "escape" in refusal
    # Nothing was extracted by asking.
    assert list(dest.iterdir()) == []


def test_a_climbing_member_is_refused(tmp_path, monkeypatch, fake_7z):
    set_listing = fake_7z
    set_listing(CAPTURED_SLT.replace("Path = seven/one.txt", "Path = ../../escape.txt"))
    dest = tmp_path / "out"
    dest.mkdir()
    refusal, _names, _total = E._sevenzip_members(tmp_path / "evil.7z", dest)
    assert refusal is not None and ".." in refusal


def test_a_missing_7z_names_the_package(tmp_path, monkeypatch):
    empty = tmp_path / "no-7z"
    empty.mkdir()
    monkeypatch.setenv("PATH", str(empty))
    dest = tmp_path / "out"
    dest.mkdir()
    refusal, names, _total = E._sevenzip_members(tmp_path / "x.7z", dest)
    assert "7z is not installed" in refusal
    assert names == []


def test_a_failing_listing_reports_what_7z_said(tmp_path, monkeypatch, fake_7z):
    set_listing = fake_7z
    set_listing("ERROR: Cannot open archive", code=2)
    dest = tmp_path / "out"
    dest.mkdir()
    refusal, _names, _total = E._sevenzip_members(tmp_path / "x.7z", dest)
    assert "7z could not list it" in refusal
    assert "Cannot open archive" in refusal


def test_the_extraction_passes_the_destination_as_one_argument(tmp_path, monkeypatch,
                                                               fake_7z):
    """`-o<path>` with no space, or 7z reads a bare `-o` and an archive name -
    the form the slot's own round-trip used.
    """
    dest = tmp_path / "out"
    dest.mkdir()
    archive = tmp_path / "round.7z"
    archive.write_bytes(b"7z\xbc\xaf\x27\x1c")
    E._sevenzip_extract(archive, dest)
    calls = (tmp_path / "state.json").read_text().splitlines()
    import json
    argv = json.loads(calls[-1])
    assert argv[:4] == ["x", f"-o{dest}", "-y", str(archive)], argv


def test_a_fatal_exit_is_raised_so_a_partial_unpack_is_reported(tmp_path, monkeypatch):
    """A silent rc=2 would read as success while half an archive sits on disk.
    """
    # A separate bin directory from the fixture's, which uses `tmp_path/bin`.
    bindir = tmp_path / "bin-fatal"
    bindir.mkdir()
    script = bindir / "7z"
    script.write_text("#!/bin/sh\nprintf 'ERROR: data error\\n' >&2\nexit 2\n")
    script.chmod(0o755)
    monkeypatch.setenv("PATH", f"{bindir}:{os.environ['PATH']}")
    dest = tmp_path / "out"
    dest.mkdir()
    with pytest.raises(RuntimeError, match="data error"):
        E._sevenzip_extract(tmp_path / "bad.7z", dest)


def test_a_tar_archive_still_uses_the_stdlib_path(tmp_path, monkeypatch):
    """The new branch is reached only when tar and zip both say no, so the
    fully-checked stdlib path is not displaced.
    """
    import tarfile
    archive = tmp_path / "keep.tar"
    with tarfile.open(archive, "w") as tar:
        member = tmp_path / "f.txt"
        member.write_text("still a tar\n")
        tar.add(member, arcname="f.txt")
    dest = tmp_path / "out"
    refusal, names, total = E._plan(archive, dest, False)
    assert refusal is None
    assert names == ["f.txt"]
    assert total == 1
