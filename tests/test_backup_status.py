"""`backup_status`: is my backup actually working?

`shani-backup` covers btrfs **snapshots** - the local undo net. That is not
what anyone means when they lose a disk, and nothing answered the other half:
is there a copy off this machine, and when was it last taken?

`restic` is in `shani-tools-extra`, so it is on both images by design.

**The whole question is whether the last backup happened and is intact**, so
this reports what restic can *prove* - the snapshot table and `restic check` -
and never reports "your backup is fine" from anything less.

**Both tables were measured on a real `@blue` slot (restic 0.19.1):**

    ID        Time                 Host        Tags        Paths                       Size
    -----------------------------------------------------------------------------------
    7ab8163c  2026-10-10 06:14:01  shanios                 /var/tmp/.../tobackup       8 B
    -----------------------------------------------------------------------------------
    Timestamps shown in UTC

and with no repository:

    Fatal: Please specify repository location (-r or --repository-file)    rc=1

**The first regex matched nothing.** The parse is a split on runs of two or
more spaces, which is what the table actually is, after a two-space gap
pattern failed on the real row - `Paths` can be several paths wide, so the gap between Time and Host
is not reliably two spaces. A parser written by regex and validated only
against hand-typed rows would have shipped.
"""

from __future__ import annotations

import os
import pathlib
import subprocess
import sys

import pytest

_REPO = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_REPO / "usr" / "lib" / "shani-chronoa"))

from shani_chronoa.skills import backup_status as BS  # noqa: E402

#: The captured table, verbatim, including the rule and the trailing note.
#: **Ordered by id, which is what restic does** - and `4f2a1b90` sorts before
#: `7ab8163c` while being the *older* snapshot, so `rows[0]` names the wrong
#: one. The table captured from `@blue` happened to list them the other way
#: round, which is exactly why the first version of this test could not see
#: the defect it was written for.
SNAPSHOTS = """ID        Time                 Host        Tags        Paths                                         Size
---------------------------------------------------------------------------------------------------------
4f2a1b90  2026-10-09 22:11:00  shanios                 /home/u/photos /home/u/docs                     1.204 GiB
7ab8163c  2026-10-10 06:14:01  shanios                 /var/tmp/chronoa-cli-formats-pQqnkv/tobackup  8 B
---------------------------------------------------------------------------------------------------------
Timestamps shown in UTC
"""

NO_REPO = "Fatal: Please specify repository location (-r or --repository-file)\n"


def _fake_restic(tmp_path, monkeypatch, snapshots=SNAPSHOTS, snap_rc=0,
                 snap_err="", check_rc=0, check_out="no errors were found\n"):
    bindir = tmp_path / "bin"
    bindir.mkdir(exist_ok=True)
    (bindir / "restic").write_text(
        "#!/usr/bin/env python3\n"
        "import sys\n"
        "a = sys.argv[1:]\n"
        f"if a and a[0] == 'snapshots':\n"
        f"    sys.stdout.write({snapshots!r})\n"
        f"    sys.stderr.write({snap_err!r})\n"
        f"    raise SystemExit({snap_rc})\n"
        f"if a and a[0] == 'check':\n"
        f"    sys.stdout.write({check_out!r})\n"
        f"    raise SystemExit({check_rc})\n"
        "raise SystemExit(0)\n")
    (bindir / "restic").chmod(0o755)
    monkeypatch.setenv("PATH", f"{bindir}:{os.environ['PATH']}")


def test_the_table_is_parsed_into_snapshots(tmp_path, monkeypatch):
    _fake_restic(tmp_path, monkeypatch)
    out = BS._run({})
    assert "2 backup snapshot(s)" in out


def test_the_header_rule_and_trailing_note_are_not_snapshots(tmp_path,
                                                             monkeypatch):
    """Three lines of that output are not snapshots: the header, the dashed
    rule, and `Timestamps shown in UTC`. Counting any of them makes the
    snapshot count wrong - which is the one number the answer leads with.
    """
    _fake_restic(tmp_path, monkeypatch)
    rows, problem = BS._snapshots(None)
    assert problem == ""
    assert len(rows) == 2, rows
    assert all(row["id"] not in ("ID", "Timestamps") for row in rows)


def test_a_multi_path_snapshot_keeps_both_paths(tmp_path, monkeypatch):
    """restic separates several paths inside the Paths column with a **single**
    space (measured), so a naive whitespace split on the whole line - the
    obvious way to write this - reports only the first path, which reads as
    "only my photos are backed up".

    Asserted on both spacings: the one the tool actually prints, and the
    padded one, because only the second tells you the join is load-bearing.
    """
    for paths in ("/home/u/photos /home/u/docs",
                  "/home/u/photos           /home/u/docs"):
        table = SNAPSHOTS.replace(
            "/home/u/photos /home/u/docs                     1.204 GiB",
            f"{paths:<51}1.204 GiB")
        _fake_restic(tmp_path, monkeypatch, snapshots=table)
        rows, _ = BS._snapshots(None)
        multi = next(row for row in rows if "/home/u/photos" in row["paths"])
        assert "/home/u/docs" in multi["paths"], paths


def test_the_newest_snapshot_is_chosen_not_the_first_row(tmp_path, monkeypatch):
    """The table is sorted by **id**, not by time - and a hex id does not sort
    chronologically. Taking `rows[0]` names the wrong snapshot, so the fixture
    puts the older one first and asserts the newer one is what the answer
    leads with. (The first version of this test only checked that a date
    appeared somewhere, which a `rows[0]` parser also satisfies.)
    """
    _fake_restic(tmp_path, monkeypatch)
    rows, _ = BS._snapshots(None)
    assert rows[0]["time"] < rows[1]["time"], "fixture must be oldest-first"
    out = BS._run({})
    assert "The most recent is 2026-10-10" in out
    assert "2026-10-09" not in out


def test_the_stored_size_is_not_called_the_size_of_the_files(tmp_path,
                                                              monkeypatch):
    """**8 B for a real directory is restic's de-duplicated stored size**, and
    reporting it as "your backup is 8 B" reads like a fault. Saying which it
    is, is the difference.
    """
    _fake_restic(tmp_path, monkeypatch)
    out = BS._run({})
    assert "de-duplication" in out
    assert "not the size of the files" in out


def test_no_repository_configured_means_no_backup(tmp_path, monkeypatch):
    """**The refusal is the common case and it is not "could not determine".**
    A machine with no restic repository has no off-machine backup, and an
    answer that only says the reader failed would be much milder than the
    truth.
    """
    _fake_restic(tmp_path, monkeypatch, snapshots="", snap_rc=1, snap_err=NO_REPO)
    out = BS._run({})
    assert "no off-machine backup" in out
    assert "Please specify repository location" in out


def test_a_repository_with_no_snapshots_is_not_a_working_backup(tmp_path,
                                                                monkeypatch):
    _fake_restic(tmp_path, monkeypatch, snapshots="")
    out = BS._run({})
    assert "no " in out and "snapshots" in out


def test_a_failing_check_marks_the_backup_unverified(tmp_path, monkeypatch):
    """**A snapshot existing is not a backup working.** restic's own integrity
    check is the only evidence of the second, so a non-clean result has to
    say the backup is unverified rather than lead with the snapshot count.
    """
    _fake_restic(tmp_path, monkeypatch, check_rc=1,
                 check_out="pack abc123: invalid data\n")
    out = BS._run({})
    assert "unverified" in out
    assert "invalid data" in out


def test_a_clean_check_is_reported(tmp_path, monkeypatch):
    _fake_restic(tmp_path, monkeypatch)
    out = BS._run({})
    assert "passed `restic check`" in out


def test_a_missing_restic_names_the_package(tmp_path, monkeypatch):
    monkeypatch.setenv("PATH", str(tmp_path / "empty"))
    out = BS._run({})
    assert "restic" in out


def test_a_hung_restic_says_so(tmp_path, monkeypatch):
    """A check that ran out of time has proved nothing about the backup."""
    _fake_restic(tmp_path, monkeypatch)

    def boom(*a, **k):
        raise subprocess.TimeoutExpired("restic", 60)

    monkeypatch.setattr(BS.subprocess, "run", boom)
    out = BS._run({})
    assert "did not answer" in out


def test_it_never_writes_to_the_repository(tmp_path, monkeypatch):
    """`restic init`, `backup`, `prune` and `forget` all appear in the
    verb list; a status reader that can reach one is a writer. Asserted on the
    source because the run-time list is not obviously the same thing.
    """
    source = (_REPO / "usr/lib/shani-chronoa/shani_chronoa/skills"
              / "backup_status.py").read_text()
    code = "\n".join(line for line in source.splitlines()
                     if not line.lstrip().startswith("#"))
    for verb in ('"init"', '"backup"', '"prune"', '"forget"', '"restore"'):
        assert verb not in code, f"backup_status must never invoke restic {verb}"