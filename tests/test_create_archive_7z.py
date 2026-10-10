"""`create_archive`: writing a .7z, and the zip and tar paths beside it.

`7z` was read (extract) and not written, so "compress this folder as .7z"
was refused with *"Format must be zip or tar.gz"* while `extract_archive`
could already read one back. The two halves of one format now exist.

**The invocation is the one a real slot measured**, not a remembered one:
`chronoa-cli-formats.sh` built an archive on `@blue` with `7z a -t7z
<archive> <paths...>` and unpacked it again with `7z x`, so both the
creation and the round-trip shapes are observed. The exit contract is
`extract_archive`'s own: 0 success, 1 non-fatal warning, 2+ fatal.

This file had no test at all before the 7z path, which is worth recording:
the zip and tar branches were green because a hand-run demo passed once, not
because anything asserted them. They are asserted here now.
"""

from __future__ import annotations

import os
import pathlib
import sys
import zipfile

import pytest

_REPO = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_REPO / "usr" / "lib" / "shani-chronoa"))

from shani_chronoa.skills import create_archive as C  # noqa: E402


@pytest.fixture
def home(tmp_path, monkeypatch):
    """A home directory, because `files.resolve` refuses paths outside it."""
    h = tmp_path / "home"
    h.mkdir(exist_ok=True)
    monkeypatch.setenv("HOME", str(h))
    return h


@pytest.fixture
def source_dir(home):
    d = home / "docs"
    d.mkdir()
    (d / "one.txt").write_text("first\n")
    (d / "two.txt").write_text("second\n")
    return d


@pytest.fixture
def fake_7z(tmp_path, monkeypatch):
    """A stand-in `7z a` that records argv and writes a small archive.

    **It writes a real file**, because the point of the success test is that
    the skill verifies the archive exists and is non-empty rather than trusting
    the exit code - a stub that writes nothing would make that assertion pass
    for the wrong reason.
    """
    argv_file = tmp_path / "argv.json"

    def _install(writes=True, code=0, stderr=""):
        bindir = tmp_path / "bin"
        bindir.mkdir(exist_ok=True)
        script = bindir / "7z"
        script.write_text(
            "#!/usr/bin/env python3\n"
            "import json, os, sys\n"
            f"open({str(argv_file)!r}, 'w').write(json.dumps(sys.argv[1:]))\n"
            f"if {writes!r} and os.environ.get('FAKE_7Z_WRITES') != '0':\n"
            "    # The archive is argv[3]: a, -t7z, -w<tmp>, <archive>, members...\n"
            "    out = [a for a in sys.argv if not a.startswith('-') and a.endswith('.7z')]\n"
            "    if out:\n"
            "        open(out[0], 'wb').write(b\"7z\\xbc\\xaf'\\x1c\" + b'\\x00' * 32)\n"
            f"if {stderr!r}:\n"
            f"    print({stderr!r}, file=sys.stderr)\n"
            f"raise SystemExit({code})\n"
        )
        script.chmod(0o755)
        monkeypatch.setenv("PATH", f"{bindir}:{os.environ['PATH']}")
        return argv_file
    return _install


def _argv(recorded):
    import json
    return json.loads(pathlib.Path(recorded).read_text())


def test_a_7z_archive_is_created(source_dir, fake_7z, home):
    recorded = fake_7z()
    target = home / "backup.7z"
    out = C._run({"action": "create", "source": str(source_dir),
                  "destination": str(target), "format": "7z"})
    assert "Created" in out
    assert target.exists() and target.stat().st_size > 0
    assert ".7z" in out
    # The measured invocation shape: `a -t7z`, the scratch dir beside the
    # archive, then the archive, then each member by name.
    argv = _argv(recorded)
    assert argv[:2] == ["a", "-t7z"]
    assert argv[2].startswith("-w") and str(target.parent) in argv[2]
    assert argv[3] == str(target)
    assert str(source_dir / "one.txt") in argv and str(source_dir / "two.txt") in argv


def test_a_missing_7z_names_the_package(source_dir, tmp_path, monkeypatch, home):
    empty = tmp_path / "no-7z"
    empty.mkdir()
    monkeypatch.setenv("PATH", str(empty))
    target = home / "backup.7z"
    out = C._run({"action": "create", "source": str(source_dir),
                  "destination": str(target), "format": "7z"})
    assert "7z is not installed" in out
    assert "7zip" in out
    assert not target.exists()


def test_a_fatal_exit_leaves_nothing_behind(source_dir, fake_7z, home):
    """**A control that can actually fail.** The first version ran 7z with
    `writes=False`, so there was no archive to leave behind and the discard
    path was never exercised; the stub now writes the file *and then fails*,
    which is the case the discard exists for.
    """
    fake_7z(writes=True, code=2, stderr="ERROR: cannot open")
    target = home / "backup.7z"
    out = C._run({"action": "create", "source": str(source_dir),
                  "destination": str(target), "format": "7z"})
    assert "7z refused" in out and "cannot open" in out
    assert not target.exists(), "a half-written archive was left on disk"


def test_a_zero_byte_archive_is_not_reported_as_created(source_dir, fake_7z, home):
    """**Exit 0 and no file is a failure.** The tar and zip branches remove a
    half-written archive; a 7z that exits clean having written nothing would
    otherwise read as success and leave the user with no archive and no error.
    """
    fake_7z(writes=False, code=0)
    target = home / "backup.7z"
    out = C._run({"action": "create", "source": str(source_dir),
                  "destination": str(target), "format": "7z"})
    assert "NOT created" in out
    assert not target.exists()


def test_the_format_enum_and_description_name_7z():
    """Advertised, or the model is never offered it - the same class as
    `git_inspect`'s stash and reflog, found in the same pass."""
    schema = C.SCHEMA["function"]["parameters"]["properties"]["format"]
    assert "7z" in schema["description"]
    assert C._run({"action": "create", "source": ".", "format": "rar"}).startswith(
        "Format must be zip, tar.gz or 7z")


class TestTheStdlibPathsStillWork:
    """**A skill with no test file at all.** These were exercised by a hand-run
    demo once and asserted nowhere; they are asserted now, because the 7z path
    sits beside them and displacing one would have been silent."""

    def test_a_zip_is_created_and_reads_back(self, source_dir, home):
        target = home / "backup.zip"
        out = C._run({"action": "create", "source": str(source_dir),
                      "destination": str(target), "format": "zip"})
        # **The real wording, observed rather than assumed.** The zip and tar
        # branches say "Archived", and the 7z one says "Created" - the first
        # version of this test asserted "Created" for all three and learned the
        # difference by failing, which is the only way it was going to be
        # learned at all on a skill with no test file before today.
        assert "Archived" in out
        with zipfile.ZipFile(target) as z:
            assert set(z.namelist()) == {"docs/one.txt", "docs/two.txt"}

    def test_a_tar_gz_is_created_and_reads_back(self, source_dir, home):
        import tarfile
        target = home / "backup.tar.gz"
        out = C._run({"action": "create", "source": str(source_dir),
                      "destination": str(target), "format": "tar.gz"})
        assert "Archived" in out
        with tarfile.open(target) as t:
            assert sorted(m.name for m in t.getmembers() if m.isfile()) == \
                ["docs/one.txt", "docs/two.txt"]

    def test_an_existing_destination_is_refused(self, source_dir, home):
        target = home / "backup.zip"
        target.write_bytes(b"already here")
        out = C._run({"action": "create", "source": str(source_dir),
                      "destination": str(target), "format": "zip"})
        assert "Refusing to overwrite" in out
        assert target.read_bytes() == b"already here"
