"""The egress log's own permissions, at the umask this machine actually runs.

The log records every destination the assistant talked to, which makes it an
audit trail: readable to another account on a shared machine, it tells that
account which model hosts a user's prompts go to and when. Restricting the
file is only half of that - the directory it lives in is not restricted at all
today, and `mkdir(parents=True)` with no mode lands at whatever the process
umask allows. `0755` on a shared box means the log is world-readable even with
the file itself at `0600`.

The other half is the creation path. Appending with `open(log, "a")` creates
the file at the umask and only tightens it afterwards, so between those two
statements the file is briefly group/world readable. Under umask `0002` - the
umask of this very session's shell - the create-then-chmod route passes through
`0664`. So these tests set umask to something permissive on purpose: on a
`0644`-by-default developer box the whole class of bug is invisible, and a test
that only ever runs under a friendly umask cannot fail for the reason it
exists.

`0600`/`0700` is safe for the reader: `shani-chronoa-sense egress` is the only
consumer of this log and it runs as the same user who recorded it. That is
verified against the real launcher, not assumed.
"""

import os
import sys

import pytest

sys.path.insert(0, "usr/lib/shani-chronoa")
from shani_chronoa import egress  # noqa: E402

# Deliberately permissive: the bug is invisible under a 0644-producing umask.
PERMISSIVE_UMASK = 0o002


@pytest.fixture
def isolated_log(tmp_path, monkeypatch):
    """Pin both paths at a temp location, never the developer's real log."""
    monkeypatch.setattr(egress, "EGRESS_DIR", tmp_path / "egress")
    monkeypatch.setattr(egress, "EGRESS_LOG", tmp_path / "egress" / "egress.jsonl")
    return egress


@pytest.fixture
def permissive_umask():
    """Run the body under a umask that grants group/other, then restore it."""
    previous = os.umask(PERMISSIVE_UMASK)
    try:
        yield PERMISSIVE_UMASK
    finally:
        os.umask(previous)


class TestEgressPermissions:
    def test_the_log_is_not_readable_by_group_or_other(self, isolated_log, permissive_umask):
        isolated_log.record("ollama", "http://127.0.0.1:11434/")
        log = isolated_log.EGRESS_LOG
        assert log.exists()
        assert log.stat().st_mode & 0o077 == 0, (
            f"log mode is {log.stat().st_mode & 0o777:04o} under umask "
            f"{permissive_umask:04o}: group or other can read the audit trail"
        )

    def test_the_directory_is_not_searchable_by_group_or_other(
        self, isolated_log, permissive_umask
    ):
        """The file mode alone does nothing if the directory lets others in.

        A `0600` file inside a `0755` directory is still readable by anyone who
        can guess the path, which is why this is a separate assertion rather
        than a comment on the previous one.
        """
        isolated_log.record("ollama", "http://127.0.0.1:11434/")
        directory = isolated_log.EGRESS_DIR
        assert directory.is_dir()
        assert directory.stat().st_mode & 0o007 == 0, (
            f"directory mode is {directory.stat().st_mode & 0o777:04o} under umask "
            f"{permissive_umask:04o}: other users can enumerate the log"
        )

    def test_a_directory_created_by_an_older_build_is_tightened_on_the_next_write(
        self, isolated_log, permissive_umask
    ):
        """Pre-existing loose state must not survive a write from this build.

        The modes only fix themselves if they are re-applied every time, not
        just at first creation: an `install -d`-style `0755` directory from an
        earlier version, or one someone widened deliberately, is exactly the
        case a per-creation `mkdir(mode=...)` would silently skip.
        """
        directory = isolated_log.EGRESS_DIR
        directory.mkdir(parents=True, exist_ok=True)
        directory.chmod(0o755)
        log = isolated_log.EGRESS_LOG
        log.touch()
        log.chmod(0o644)
        assert directory.stat().st_mode & 0o007 != 0, "precondition: dir is loose"

        isolated_log.record("ollama", "http://127.0.0.1:11434/")

        assert directory.stat().st_mode & 0o007 == 0
        assert log.stat().st_mode & 0o077 == 0

    def test_appending_still_works_after_the_permissions_change(
        self, isolated_log, restrictive_umask
    ):
        """`open(..., "a")` on the real path must keep working.

        This is the shape `tests/test_egress.py` uses to plant a corrupt line.
        If tightening the creation path made the log unwritable by its own user,
        the corrupt-line test would stop being able to reach the file at all -
        so the append path is pinned here explicitly, at the mode a caller
        actually gets, rather than assumed.
        """
        isolated_log.record("ollama", "http://127.0.0.1:1/")
        with open(isolated_log.EGRESS_LOG, "a", encoding="utf-8") as handle:
            handle.write("{not json\n")
        assert len(isolated_log.read_events()) == 1

    def test_the_only_reader_of_this_log_can_still_read_it(self, isolated_log):
        """Same-user-only must not mean unreadable.

        `shani-chronoa-sense egress` is the sole consumer of this log, it runs
        as the user who recorded it, and it reads through the same public
        `summary()`/`read_events()` path every other caller uses.
        """
        isolated_log.record("ollama", "http://127.0.0.1:11434/", bytes_out=10)
        stats = isolated_log.summary()
        assert stats["total"] == 1
        assert stats["log"] == str(isolated_log.EGRESS_LOG)
        assert [e["host"] for e in isolated_log.read_events()] == ["127.0.0.1"]


@pytest.fixture
def restrictive_umask():
    """The ordinary case, so the append test is not umask-dependent."""
    previous = os.umask(0o022)
    try:
        yield 0o022
    finally:
        os.umask(previous)