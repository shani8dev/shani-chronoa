"""`list_archive`: what is inside an archive, before extracting it.

`extract_archive` unpacks. Nothing listed - and listing is both the more common
question and the safer one: a user who has just downloaded something has no
reason to extract it first, and the reason to list is to decide.

**It reuses `extract_archive`'s own guard rather than inventing a second one.**
That module exists because tar and zip will write outside the directory they
were given (the manual's "archive extraction vulnerability", CVE-2007-4559),
and an archive that would be *refused on extraction* is exactly the one a
person most wants to know about **before** asking for extraction.

**tar and zip are read with Python's stdlib, never with `tar`/`unzip`/`7z`** -
the stdlib gives a member list with no extraction step at all, and it cannot
write anything because this skill never asks it to.
"""

from __future__ import annotations

import pathlib
import sys
import tarfile
import zipfile

import pytest

_REPO = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_REPO / "usr" / "lib" / "shani-chronoa"))

from shani_chronoa.skills import list_archive as LA  # noqa: E402


@pytest.fixture
def tree(tmp_path):
    src = tmp_path / "src"
    (src / "sub").mkdir(parents=True)
    (src / "a.txt").write_text("hello\n")
    (src / "sub" / "b.txt").write_text("deep\n")
    return src


def _zip(path, tree):
    with zipfile.ZipFile(path, "w") as zf:
        for item in sorted(tree.rglob("*")):
            zf.write(item, item.relative_to(tree))
    return path


def _tar(path, tree, mode="w:gz"):
    with tarfile.open(path, mode) as tar:
        tar.add(tree, arcname=".")
    return path


def test_a_zip_lists_its_members(tmp_path, tree):
    out = LA._run({"path": str(_zip(tmp_path / "t.zip", tree))})
    assert "a.txt" in out
    assert "sub/b.txt" in out or "sub\\b.txt" in out
    assert "item(s)" in out


def test_a_tar_lists_its_members(tmp_path, tree):
    out = LA._run({"path": str(_tar(tmp_path / "t.tar.gz", tree))})
    assert "a.txt" in out
    assert "b.txt" in out


def test_nothing_is_extracted(tmp_path, tree):
    """The whole point of listing: the archive is opened for reading and
    nothing appears on disk.

    Asserted by **snapshotting the directory**, which is the only version of
    this claim a stray temp dir cannot satisfy. My first version enumerated
    `tmp_path` and found pytest's own `home` directory, then tried to explain
    it away - an assertion about the wrong thing, made to pass.
    """
    archive = _zip(tmp_path / "t.zip", tree)
    before = sorted(p.name for p in tmp_path.iterdir())
    LA._run({"path": str(archive)})
    after = sorted(p.name for p in tmp_path.iterdir())
    assert after == before, f"listing changed the directory: {set(after) - set(before)}"


def test_an_escaping_member_is_called_out_before_extraction(tmp_path, tree):
    """**The reason this skill is not just a nicer `tar -t`.** An archive
    containing `../../.config/autostart/evil.desktop` is reported here as
    something extraction would refuse - which is the whole point of listing
    before opening.
    """
    archive = tmp_path / "evil.tar.gz"
    with tarfile.open(archive, "w:gz") as tar:
        tar.add(tree / "a.txt", arcname="a.txt")
        info = tarfile.TarInfo("../../.config/autostart/evil.desktop")
        info.size = 5
        import io
        tar.addfile(info, io.BytesIO(b"nope\n"))
    out = LA._run({"path": str(archive)})
    assert "refused" in out
    assert "evil.desktop" in out
    assert "CVE-2007-4559" in out


def test_an_escaping_member_in_a_zip_is_also_caught(tmp_path, tree):
    archive = tmp_path / "evil.zip"
    with zipfile.ZipFile(archive, "w") as zf:
        zf.write(tree / "a.txt", "a.txt")
        zf.writestr("../../etc/cron.d/anything", "nope\n")
    out = LA._run({"path": str(archive)})
    assert "refused" in out
    assert "anything" in out


def test_a_clean_archive_says_nothing_would_be_refused(tmp_path, tree):
    out = LA._run({"path": str(_zip(tmp_path / "t.zip", tree))})
    assert "CVE-2007-4559" not in out
    assert "refused" not in out


def test_a_long_listing_is_capped_and_says_so(tmp_path):
    import zipfile
    archive = tmp_path / "many.zip"
    with zipfile.ZipFile(archive, "w") as zf:
        for i in range(80):
            zf.writestr(f"file{i:03d}.txt", "x")
    out = LA._run({"path": str(archive)})
    assert "80 item(s)" in out
    assert "and 40 more" in out


def test_a_non_archive_without_7z_is_honest(tmp_path, monkeypatch):
    plain = tmp_path / "notes.txt"
    plain.write_text("just text\n")
    monkeypatch.setattr(LA.shutil, "which", lambda name: None)
    out = LA._run({"path": str(plain)})
    assert "neither a tar archive nor a zip" in out
    assert "Nothing was extracted" in out


def test_7z_is_used_for_other_formats(tmp_path, monkeypatch):
    """A `.7z`/`.rar`/`.cab` is not tar and not zip, so the stdlib declines -
    and the answer must go to 7z rather than stopping at a true dead end.
    """
    archive = tmp_path / "thing.7z"
    archive.write_bytes(b"7z\xbc\xaf\x27\x1c" + b"\x00" * 64)
    calls = {}

    def fake_sevenzip(path):
        calls["path"] = str(path)
        return [("inside.txt", 12)], ""

    monkeypatch.setattr(LA.shutil, "which", lambda name: "/usr/bin/7z")
    monkeypatch.setattr(LA, "_via_sevenzip", fake_sevenzip)
    out = LA._run({"path": str(archive)})
    assert "inside.txt" in out
    assert calls["path"] == str(archive)


def test_7zs_own_words_are_used_when_it_fails(tmp_path, monkeypatch):
    """A wrong password and a corrupt archive are different facts, and
    guessing from the exit status is how one gets reported as the other.
    """
    archive = tmp_path / "thing.7z"
    archive.write_bytes(b"not an archive")
    monkeypatch.setattr(LA.shutil, "which", lambda name: "/usr/bin/7z")
    monkeypatch.setattr(LA, "_via_sevenzip",
                        lambda path: ([], "ERROR: Wrong password"))
    out = LA._run({"path": str(archive)})
    assert "Wrong password" in out
    assert "Nothing was extracted" in out


def test_a_missing_file_is_refused(tmp_path):
    out = LA._run({"path": str(tmp_path / "nothing.zip")})
    assert "no file at" in out
    assert "Nothing was guessed" in out


def test_a_folder_is_not_an_archive(tmp_path):
    out = LA._run({"path": str(tmp_path)})
    assert "folder, not an archive" in out


def test_an_lzo_is_listed_by_lzop_not_refused(tmp_path, monkeypatch):
    """**Measured on `@blue`: `7z l -slt` returns rc=2 on a `.lzo`**, against
    an archive `lzop` had just created, while `lzop` is installed in the same
    image. So a `.lzo` used to be a dead end with the tool sitting in
    /usr/sbin.
    """
    archive = tmp_path / "big.lzo"
    archive.write_bytes(b"LZO1X" + b"\x00" * 40)
    fake = [("/home/user/big.txt", 4096)]
    monkeypatch.setattr(LA.archives, "members",
                        lambda path: (fake, "") if path.name.endswith(".lzo")
                        else ([], ""))
    out = LA._run({"path": str(archive)})
    assert "big.txt" in out
    assert "lzop" in out


def test_an_lrz_is_listed_without_reaching_for_lrzip_t(tmp_path, monkeypatch):
    """**`lrzip -t` is a test, not a listing** - measured: it decompresses to
    measure and prints `Decompressing... 100%`. The only streaming reader is
    `lrzcat`, so a parser that reached for `lrzip -t` would decompress a file
    in order to answer a question about it.
    """
    seen = []

    def fake_run(command):
        seen.append(command[0])
        return ("payload", "", 0)

    monkeypatch.setattr(LA.archives, "_run", fake_run)
    monkeypatch.setattr(LA.archives.shutil, "which", lambda n: "/usr/sbin/lrzcat")
    rows, refusal = LA.archives.lrzip_members(pathlib.Path("/tmp/thing.lrz"))
    assert refusal == ""
    assert seen == ["lrzcat"], seen
    assert rows[0][0] == "thing"


def test_a_lzop_row_is_parsed_from_the_real_captured_shape():
    """The row `lzop -l` actually prints, captured on `@blue` 2026-10-10:

        method      compressed  uncompr. ratio uncompressed_name
        LZO1X-1            21        21 100.0% /var/tmp/chron

    The name is the last column and the sizes are integers - a parser that
    assumed fixed widths would read `21` as part of the name.
    """
    row = "LZO1X-1            21        21 100.0% /var/tmp/chronoa-cli-formats/x.txt"
    parsed = LA.archives._LZOP_ROW.match(row)
    assert parsed, "the real captured row did not match"
    assert parsed.group(5) == "/var/tmp/chronoa-cli-formats/x.txt"
    assert int(parsed.group(3)) == 21


def test_extract_archive_also_consults_the_measured_exceptions(tmp_path, monkeypatch):
    """The same routing has to be in `extract_archive`, and **a mutation that
    removed it entirely left every test green** - because nothing here ever
    asked that module about a `.lzo`. That is the gap this closes: two skills
    reading the same archive must not disagree about whether it can be opened.
    """
    from shani_chronoa.skills import extract_archive as EA

    seen = []

    def fake_members(path):
        seen.append(path.name)
        return [("inside.txt", 10)], ""

    monkeypatch.setattr(EA.archives, "members", fake_members)
    archive = tmp_path / "big.lzo"
    archive.write_bytes(b"LZO1X" + b"\x00" * 32)
    dest = tmp_path / "out"
    refusal, names, count = EA._plan(archive, dest, overwrite=False)
    assert seen == ["big.lzo"], "extract_archive never asked about the format"
    assert refusal is None, refusal
    assert names == ["inside.txt"]
    assert count == 1


def test_rar_and_cab_are_left_to_sevenzip(tmp_path, monkeypatch):
    """`.rar` is NOT one of the two exceptions: this image's 7z is 7-Zip 26.03
    and lists Rar/Rar1/Rar2/Rar3/Rar5. Routing it to lzcat on the strength of
    "rar is an old format" would be exactly the guess the measurement removed.
    """
    for name in ("thing.rar", "thing.cab", "thing.7z"):
        assert not LA.archives.needs_own_tool(pathlib.Path(name)), name


def test_a_missing_lzop_says_so_rather_than_pretending(tmp_path, monkeypatch):
    monkeypatch.setattr(LA.archives.shutil, "which", lambda n: None)
    rows, refusal = LA.archives.lzop_members(pathlib.Path("/tmp/x.lzo"))
    assert rows == []
    assert "7z cannot read .lzo" in refusal
    assert "rc=2" in refusal


def test_no_path_asks_for_one():
    out = LA._run({})
    assert "need the path" in out


def test_it_never_shells_out_for_tar_or_zip(tmp_path, tree, monkeypatch):
    """**tar and zip are read with the stdlib, not with `tar`/`unzip`.** The
    stdlib gives a member list with no extraction step, and cannot write
    anything - which is the property this skill depends on. A regression that
    reached for a binary would still pass every assertion above.

    `subprocess` is imported *inside* `_via_sevenzip`, so the module has no
    `subprocess` attribute to patch - my first version's
    `monkeypatch.setattr(LA.subprocess, ...)` raised `AttributeError` and the
    check never ran. The real module's `run` is patched instead, which fails
    loudly on either path.
    """
    import subprocess as real_subprocess

    def forbidden(*a, **k):
        raise AssertionError("list_archive shelled out for a tar/zip listing")

    monkeypatch.setattr(real_subprocess, "run", forbidden)
    out = LA._run({"path": str(_zip(tmp_path / "t.zip", tree))})
    assert "a.txt" in out
    out = LA._run({"path": str(_tar(tmp_path / "t.tar.gz", tree))})
    assert "a.txt" in out