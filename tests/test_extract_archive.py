"""The `extract_archive` zip-slip guard, against genuinely malicious archives.

This module used to exist and went when `skills/scan_archive.py` did. The guard
it covered survived in `extract_archive._check_members()`, so the coverage did
not: nothing in the suite stopped `extract_archive` from writing outside its
destination. `AGENTS.md` records that gap rather than papering over it. This is
the fix.

**Every archive here is real, built byte by byte, and no check is mocked.**
`zipfile.ZipFile` and `tarfile` write a genuine member listing that
`extract_archive` genuinely reads back. A mocked `_check_members` would only
prove the mock returns what the mock was told to return — which is the failure
this repo has already paid for once. A visual test suite here was deleted
because every assertion in it passed regardless of the state it was meant to
catch (`AGENTS.md`, "Rendering the UI to a PNG"); a guard test that cannot fail
is the same artefact wearing a different hat.

**Every refusal asserts the escape target never came into existence, not just
that a string came back.** A refusal raised *after* the first write is a
partial extraction of a hostile archive, and it is the outcome the module's own
docstring calls worse than none, because it looks like success. Asserting only
the message would pass against a guard that wrote the file and then apologised.

**The destination is nested two deep, on purpose.** `../../escape` from
`work/dest` lands at `tmp_path/escape` — inside pytest's own cleanup, so a
*failing* guard cannot scatter debris across the filesystem while demonstrating
the bug. Asserting against `/tmp/escape` or `/etc/passwd` would mean a broken
guard wrote somewhere this test cannot clean up.

The one case here that is not in the obvious list is
`test_a_member_resolving_outside_the_destination_is_refused`. It is also the
only one where the standard library does **not** save you: `zipfile`'s
`_extract_member` strips `..` and leading `/`, and `tarfile`'s `filter="data"`
refuses them, so on a modern interpreter those two checks are defence in depth
— but neither library resolves symlinks *already present in the destination*.
A member named `sub/escape`, with `dest/sub` a symlink to somewhere else, is
written straight through it by `zipfile`, and `extract_archive` reports
success. Verified by running it: with the guard stubbed out, the file landed
outside the destination and `_run` returned
`a.zip unpacked into ...: 1 file(s)`. That is the check the deleted test file
was the only coverage of, and the reason the guard is not redundant.
"""

import io
import os
import tarfile
import zipfile
from pathlib import Path

import pytest

from shani_chronoa.skills import extract_archive as EA

# `_MAX_MEMBERS` is read as a module global at call time, so the cap test
# builds a real archive over the real threshold rather than shrinking the
# threshold to make the fixture cheap.
CAP = EA._MAX_MEMBERS


# --------------------------------------------------------------------------
# archive builders — real files on disk, no mocking anywhere below
# --------------------------------------------------------------------------


def _zip(where: Path, members, symlink_members=()) -> Path:
    """A real zip. `symlink_members` get the unix mode bit a zip stores a
    symlink as; `zipfile` will happily write one and `extract_archive` has to
    read it back out of `external_attr` rather than assume."""
    path = where / "payload.zip"
    with zipfile.ZipFile(path, "w", zipfile.ZIP_STORED) as zf:
        for name in members:
            zf.writestr(name, b"PWNED\n")
        for name in symlink_members:
            info = zipfile.ZipInfo(name)
            info.create_system = 3                       # unix
            info.external_attr = 0o120777 << 16          # S_IFLNK
            zf.writestr(info, b"/etc/passwd")
    return path


def _tar(where: Path, members) -> Path:
    """A real tar. `members` are `TarInfo` objects, so symlink/hardlink/device
    /FIFO types are genuine tar type codes in a genuine header."""
    path = where / "payload.tar"
    with tarfile.open(path, "w") as tf:
        for info in members:
            if info.isreg():
                info.size = len(b"PWNED\n")
                tf.addfile(info, io.BytesIO(b"PWNED\n"))
            else:
                tf.addfile(info)
    return path


def _regular(name: str) -> tarfile.TarInfo:
    info = tarfile.TarInfo(name)
    info.type = tarfile.REGTYPE
    return info


@pytest.fixture
def work(tmp_path):
    """`tmp_path/work/dest` — nested so `../../escape` stays inside tmp_path."""
    root = tmp_path / "work"
    dest = root / "dest"
    dest.mkdir(parents=True)
    return root, dest


def _nothing_written(dest: Path) -> list:
    """Everything under the destination, links included."""
    return sorted(str(p.relative_to(dest)) for p in dest.rglob("*"))


# --------------------------------------------------------------------------
# 1. `..` components — the classic zip slip
# --------------------------------------------------------------------------


def test_a_parent_directory_component_is_refused(work):
    """`../../escape` is CVE-2007-4559's zip form and the reason any of this
    exists. Asserted against the file on disk, not just the refusal string."""
    root, dest = work
    archive = _zip(root, ["../../escape"])
    escape = root.parent / "escape"

    result = EA._run({"archive": str(archive), "destination": str(dest)})

    assert "Refusing to unpack" in result
    assert not escape.exists(), (
        f"the escape target was created anyway: {escape} — the guard fired after "
        f"writing, which is a partial extraction of a hostile archive"
    )
    assert _nothing_written(dest) == []


def test_the_refusal_names_the_offending_member(work):
    """A user who cannot act on a message concludes the assistant is broken.
    The module's own rule is that a refusal names the member that caused it.
    A benign member is listed *first*, so this also proves the message names
    the culprit rather than just echoing the first entry."""
    root, dest = work
    archive = _zip(root, ["innocent.txt", "../../escape"])

    result = EA._run({"archive": str(archive), "destination": str(dest)})

    assert "Refusing to unpack" in result
    assert "../../escape" in result, f"refusal does not name the member: {result!r}"
    assert not (root.parent / "escape").exists()
    assert _nothing_written(dest) == []


def test_a_backslash_parent_component_is_also_a_parent_component(work):
    """`name.replace("\\\\", "/")` normalises before the check, so a Windows-style
    `..\\..\\escape` is caught by the same rule. A test that only fed `/`
    separators would not notice if that normalisation were dropped."""
    root, dest = work
    archive = _zip(root, ["..\\..\\escape"])
    escape = root.parent / "escape"

    result = EA._run({"archive": str(archive), "destination": str(dest)})

    assert "Refusing to unpack" in result
    assert not escape.exists()
    assert _nothing_written(dest) == []


def test_a_parent_component_that_normalises_back_inside_is_still_refused(work):
    """The one case that separates the `..` check from the resolved-path check,
    and the reason both exist.

    `a/../b` normalises to `<dest>/b` — safely inside. The resolved-path check
    at `extract_archive.py:108` would wave it through, so *only* the `..`
    component check at `:101` refuses it. Deleting the `..` check and running
    this: 1 failed. The rule is a blanket "no `..` in any member", not
    "no `..` that escapes", which is the right shape — what a member with a
    `..` means can depend on extraction order and on what else is in the
    archive, neither of which the resolved-path check can see.

    Without this test the `..` check looked like dead code: the resolved-path
    check catches every escaping archive, so removing `..` left the whole
    escape suite green. It is not dead, it is just narrower than the obvious
    case."""
    root, dest = work
    archive = _zip(root, ["a/../b"])

    result = EA._run({"archive": str(archive), "destination": str(dest)})

    assert "Refusing to unpack" in result
    assert "'..'" in result, (
        "refused, but not by the '..' rule — the resolved-path check caught it, "
        "so this test is not covering the check it claims to cover"
    )
    assert _nothing_written(dest) == []
    assert not (dest / "b").exists()


# --------------------------------------------------------------------------
# 2. absolute paths
# --------------------------------------------------------------------------


def test_an_absolute_path_member_is_refused(work):
    """`/etc/passwd` as a member name. The target is a path under tmp_path
    rather than a real system file, so a failing guard is demonstrable and
    cleanable; the code path is identical — the check is on the leading `/`."""
    root, dest = work
    escape = root.parent / "abs_escape"
    archive = _zip(root, [str(escape)])

    result = EA._run({"archive": str(archive), "destination": str(dest)})

    assert "Refusing to unpack" in result
    assert "it names an absolute path" in result
    assert not escape.exists()
    assert _nothing_written(dest) == []


def test_a_windows_drive_letter_member_is_refused(work):
    """`cleaned[1] == ":"` catches `C:/...` — the absolute-path check is not
    only a leading-slash check, and a test feeding only `/` would pass even if
    the drive-letter half were gone."""
    _, dest = work
    archive = _zip(dest.parent.parent, ["C:/Windows/System32/drivers/etc/hosts"])

    result = EA._run({"archive": str(archive), "destination": str(dest)})

    assert "Refusing to unpack" in result
    assert "it names an absolute path" in result
    assert _nothing_written(dest) == []


def test_an_absolute_path_member_in_a_tar_is_refused(work):
    """The tar branch classifies members and calls the same `_check_members`,
    so the same three path checks apply. Covered from the tar side too,
    because the two branches build their `kinded` list by different code."""
    root, dest = work
    archive = _tar(root, [_regular("/etc/shadow")])

    result = EA._run({"archive": str(archive), "destination": str(dest)})

    assert "Refusing to unpack" in result
    assert "it names an absolute path" in result
    assert _nothing_written(dest) == []


# --------------------------------------------------------------------------
# 3. resolves outside the destination with no `..` and no leading `/`
# --------------------------------------------------------------------------


def test_a_member_resolving_outside_the_destination_is_refused(work):
    """The check neither `..` nor a leading `/` covers, and the one case where
    the standard library writes the file for you.

    `dest/sub` is a symlink to `outside` — it can be there from anything
    previously unpacked, not only from this archive. `sub/escape` contains no
    `..` and no leading slash, so the first two checks pass it. `zipfile`
    resolves no symlinks: it joins the name onto the destination and opens the
    result, following `sub` straight out.

    With the guard stubbed out, verified by running: `outside/escape` was
    created and `_run` returned `payload.zip unpacked into ...: 1 file(s)`. A
    clean success message for a file written outside the directory the caller
    named."""
    root, dest = work
    outside = root.parent / "outside"
    outside.mkdir()
    os.symlink(outside, dest / "sub")
    archive = _zip(root, ["sub/escape"])

    result = EA._run({"archive": str(archive), "destination": str(dest)})

    assert "Refusing to unpack" in result
    assert "it would be written outside the destination" in result
    assert "sub/escape" in result
    assert not (outside / "escape").exists(), (
        "the member was written through the destination's own symlink; this is "
        "the escape the deleted scan_archive test was the only coverage of"
    )
    assert _nothing_written(dest) == ["sub"]      # only the pre-existing symlink


# --------------------------------------------------------------------------
# 4. symlink / hardlink / device / FIFO members
# --------------------------------------------------------------------------


def test_a_tar_symlink_member_is_refused(work):
    """A link is a way of pointing outside the destination without ever writing
    there. `tarfile`'s `data` filter also refuses this one, so the check is
    defence in depth — but `extract_archive` is documented to check first and
    refuse the whole archive, and this is what makes that true rather than
    aspirational."""
    root, dest = work
    info = tarfile.TarInfo("innocent-looking")
    info.type = tarfile.SYMTYPE
    info.linkname = "/etc/passwd"
    archive = _tar(root, [info])

    result = EA._run({"archive": str(archive), "destination": str(dest)})

    assert "Refusing to unpack" in result
    assert "it contains a symlink member" in result
    assert _nothing_written(dest) == []


def test_a_tar_hardlink_member_is_refused(work):
    """Separately from the symlink: `islnk()` is its own branch at
    `extract_archive.py:146`, and a hardlink to a file outside the destination
    is the same class of trick."""
    root, dest = work
    info = tarfile.TarInfo("innocent-looking")
    info.type = tarfile.LNKTYPE
    info.linkname = "/etc/passwd"
    archive = _tar(root, [info])

    result = EA._run({"archive": str(archive), "destination": str(dest)})

    assert "Refusing to unpack" in result
    assert "it contains a hardlink member" in result
    assert _nothing_written(dest) == []


def test_a_tar_device_member_is_refused(work):
    """A character device. Nothing a person unpacks needs one, and it is the
    member type most likely to be used to touch a device node."""
    root, dest = work
    info = tarfile.TarInfo("evil-dev")
    info.type = tarfile.CHRTYPE
    info.devmajor, info.devminor = 1, 3
    archive = _tar(root, [info])

    result = EA._run({"archive": str(archive), "destination": str(dest)})

    assert "Refusing to unpack" in result
    assert "it contains a device member" in result
    assert _nothing_written(dest) == []


def test_a_tar_fifo_member_is_refused(work):
    """A FIFO. Same rule, different `isfifo()` branch."""
    root, dest = work
    info = tarfile.TarInfo("evil-fifo")
    info.type = tarfile.FIFOTYPE
    archive = _tar(root, [info])

    result = EA._run({"archive": str(archive), "destination": str(dest)})

    assert "Refusing to unpack" in result
    assert "it contains a fifo member" in result
    assert _nothing_written(dest) == []


def test_a_zip_symlink_member_is_refused(work):
    """A zip stores a symlink as a plain entry with the unix mode bit set, so
    it has to be read out of `ZipInfo.external_attr`. The mode bit is set
    here, not the filename — a test that named the member `link` would pass
    even if the whole `external_attr` read were deleted."""
    root, dest = work
    archive = _zip(root, ["harmless-name.txt"], symlink_members=["also-harmless"])

    result = EA._run({"archive": str(archive), "destination": str(dest)})

    assert "Refusing to unpack" in result
    assert "it contains a symlink member" in result
    assert "also-harmless" in result
    assert _nothing_written(dest) == []


# --------------------------------------------------------------------------
# 5. member-count cap
# --------------------------------------------------------------------------


def test_a_tar_over_the_member_cap_is_refused(work):
    """Over the real `_MAX_MEMBERS`, not a shrunken one. The cap is a
    resource limit, so a refusal here must also not have written any of the
    20,000 files before noticing."""
    root, dest = work
    archive = _tar(root, [_regular(f"f{i}.txt") for i in range(CAP + 1)])

    result = EA._run({"archive": str(archive), "destination": str(dest)})

    assert "Refusing to unpack" in result
    assert "more than" in result
    assert str(CAP) in result
    assert _nothing_written(dest) == []


def test_a_zip_over_the_member_cap_is_refused(work):
    """The cap is applied separately in each branch of `_plan` — the tar one
    at `extract_archive.py:137` and the zip one at `:161`. Testing only the
    tar branch would leave the zip cap uncovered, and they are separate
    comparisons in separate code."""
    root, dest = work
    archive = _zip(root, [f"f{i}.txt" for i in range(CAP + 1)])

    result = EA._run({"archive": str(archive), "destination": str(dest)})

    assert "Refusing to unpack" in result
    assert "more than" in result
    assert str(CAP) in result
    assert _nothing_written(dest) == []


# --------------------------------------------------------------------------
# 6. the whole archive is validated before the first file is written
# --------------------------------------------------------------------------


def test_a_hostile_member_late_in_the_list_leaves_nothing_behind(work):
    """The module's first honesty rule: *"The whole archive is validated before
    the first file is written."* An archive that is benign right up to the last
    member is the case that rule exists for, and it is invisible to a test
    whose only hostile member is first — a guard that checked one member and
    stopped would pass that. The innocent member here is real and would really
    be extracted, so its absence is meaningful."""
    root, dest = work
    archive = _zip(root, ["totally-fine.txt", "also-fine.txt", "../../escape"])
    escape = root.parent / "escape"

    result = EA._run({"archive": str(archive), "destination": str(dest)})

    assert "Refusing to unpack" in result
    assert not escape.exists()
    assert not (dest / "totally-fine.txt").exists(), (
        "a file from a refused archive was extracted; the guard is validating "
        "as it goes rather than before writing, so a partial unpack is left "
        "behind that looks like success"
    )
    assert _nothing_written(dest) == []


# --------------------------------------------------------------------------
# 7. the happy path — a guard that refuses everything is not a guard
# --------------------------------------------------------------------------


def test_a_legitimate_nested_archive_extracts(tmp_path):
    """The counterweight. Every test above passes against a `_check_members`
    that returns a refusal unconditionally, which is a perfectly green suite
    that has stopped the skill working. Nested, because a flat single-file
    archive would not prove the destination tree is built."""
    root = tmp_path / "work"
    dest = root / "dest"
    dest.mkdir(parents=True)
    archive = _zip(root, ["pkg/readme.md", "pkg/src/main.py", "pkg/src/util.py"])

    result = EA._run({"archive": str(archive), "destination": str(dest)})

    assert "unpacked into" in result
    for name in ("pkg/readme.md", "pkg/src/main.py", "pkg/src/util.py"):
        assert (dest / name).is_file(), f"{name} missing after a clean extraction"
    assert (dest / "pkg/src/main.py").read_bytes() == b"PWNED\n"


def test_a_legitimate_tar_with_nested_directories_extracts(tmp_path):
    """Same from the tar branch, including the directory members the tar path
    filters out of the extracted-names list at `extract_archive.py:156`."""
    root = tmp_path / "work"
    dest = root / "dest"
    dest.mkdir(parents=True)
    directory = tarfile.TarInfo("pkg")
    directory.type = tarfile.DIRTYPE
    nested = tarfile.TarInfo("pkg/inner")
    nested.type = tarfile.DIRTYPE
    archive = _tar(root, [directory, nested, _regular("pkg/inner/thing.txt")])

    result = EA._run({"archive": str(archive), "destination": str(dest)})

    assert "unpacked into" in result
    assert (dest / "pkg" / "inner" / "thing.txt").is_file()


def test_a_legitimate_dotted_name_is_not_mistaken_for_a_parent_component(tmp_path):
    """`..` as a *component*, not as a substring. `a..b` and `...` are ordinary
    filenames and must still extract; a guard that tested `'..' in name`
    instead of `part == '..'` would refuse these, and this is the only test
    that would notice.

    `sub/..` is deliberately absent from this archive even though it looks
    similar: it is a real `..` component and the guard is right to refuse it.
    (Found by running this — the first draft of this test included it and went
    red, which is the fixture being wrong, not the guard.)"""
    root = tmp_path / "work"
    dest = root / "dest"
    dest.mkdir(parents=True)
    archive = _zip(root, ["notes..txt", "...", "v1..2/ok.txt"])

    result = EA._run({"archive": str(archive), "destination": str(dest)})

    assert "unpacked into" in result
    assert (dest / "notes..txt").is_file()
    assert (dest / "...").is_file()
    assert (dest / "v1..2" / "ok.txt").is_file()


# --------------------------------------------------------------------------
# 8. the assertions themselves are not vacuous
# --------------------------------------------------------------------------


def test_a_refusal_message_does_not_merely_contain_the_test_name(tmp_path):
    """A guard on the tests themselves, written after a real one was caught.

    Every refusal here embeds the destination path in its message, and pytest
    builds `tmp_path` from the *test function's name*. So an assertion of the
    form `assert "absolute" in result` is satisfied by the path
    `.../test_an_absolute_path_member_i0/abs_escape` even when the guard
    refuses for a completely different reason — which is exactly what happened:
    with the absolute-path check deleted, this file's absolute-path tests still
    passed, because the reason had fallen through to the resolved-path check
    and the assertion was matching the directory name instead of the message.

    The lesson is general enough to assert: **a substring assertion over a
    message that contains a path is a substring assertion over the path.** The
    fix is to assert on a phrase with a space in it, which no path can contain.
    This test re-checks that, so a future edit that shortens one of those
    assertions back to a bare word fails here rather than silently passing
    forever."""
    root = tmp_path / "work"
    dest = root / "dest"
    dest.mkdir(parents=True)
    # A member this test's own name does not contain, to keep this test honest.
    archive = _zip(root, ["xzyw"])

    result = EA._run({"archive": str(archive), "destination": str(dest)})

    assert str(dest) in result, "precondition: the success message embeds the path"
    # pytest truncates the tmp_path component, so the whole test name is not
    # present -- but its first 30 characters are, and that is the point: those
    # characters are enough to satisfy a bare-word assertion about any word
    # appearing early in a test name. `test_a_refusal_message_does_no0` is the
    # same shape as `test_an_absolute_path_member_i0`, which is what made
    # `assert "absolute" in result` pass with the absolute-path check deleted.
    embedded = tmp_path.name[:30]
    assert embedded in result, (
        f"precondition broke: tmp_path name {embedded!r} is no longer in the "
        f"message, so this test is no longer demonstrating anything"
    )
