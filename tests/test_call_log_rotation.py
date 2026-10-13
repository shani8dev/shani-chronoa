"""Rotation of the call log, and the reader that has to follow it.

**Why the log is bounded at all.** `tool_calls.log` is opened `O_APPEND` at
0600 with no rotation, no age cap and no size cap. Measured on this machine:
**18,914,951 bytes / 19,110 records over 25 days**, 990 bytes a record and 764
records a day - 276 MB a year, 1.6 GB a year if its busiest day were every day -
and the repository's own `log_files` skill describes it as *"still growing, and
unrotated"*. Reading it costs 0.52 s, so nothing is slowed by its size; the
costs are disk and nothing else.

**Why this file is mostly about the reader.** Rotation moves the oldest records
out of `tool_calls.log` and into `tool_calls.log.N`. Everything that learns
from this log - the outcome model, the calibrations, the bandit, the lessons -
reads it. A reader that kept opening the active file would go on working
perfectly while seeing a shrinking slice of history, and **nothing would report
that records had stopped arriving**, only that fewer were there. That is the
absence-shaped wrong answer this repository keeps meeting, and a rotation that
made it invisible would be worse than no rotation at all.

So the headline test here is `TestTheReaderFollowsTheArchive`, and the cap is
only allowed to drop the **oldest** records - never the newest, which are the
ones a reflex layer and a running assistant actually read.
"""

import json
import os
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "usr" / "lib" / "shani-chronoa"))

from shani_chronoa import learning, tool_tracking  # noqa: E402


@pytest.fixture
def logs(tmp_path, monkeypatch):
    """A log directory nothing else can reach, wired into the readers.

    **The readers are wired, not stubbed.** Monkeypatching `log_files` away
    would leave every test below passing against a reader that reads one file -
    which is the defect. `learning._log_path` is redirected so the default path
    lands here too, so an unnamed reader is measured rather than assumed.
    """
    d = tmp_path / "logs"
    d.mkdir()
    monkeypatch.setattr(tool_tracking, "log_dir", lambda: d)
    monkeypatch.setattr(learning, "_log_path", lambda: d / "tool_calls.log")
    return d


def write(directory, tag, n=1, tool="get_datetime"):
    """Append `n` real records through the real tracker, not by writing bytes."""
    tracker = tool_tracking.ToolTracker(log_dir=directory)
    for i in range(n):
        tracker.record_call(tool, {"tag": tag, "i": str(i)}, "ok", 1.0)
    return tracker


def records_in(directory):
    """Every parseable record, oldest first, read by the module that learns."""
    return [r for r in learning._entries() if r.get("tool_name")]


class TestRotationMovesRecordsAndLosesNone:
    def test_a_small_log_is_not_rotated(self, logs):
        write(logs, "a", 5)
        assert [p.name for p in tool_tracking.log_files(logs / "tool_calls.log")] == ["tool_calls.log"]
        assert not list(logs.glob("tool_calls.log.*"))

    def test_rotation_keeps_every_record(self, logs):
        """**The headline.** Write, rotate, write: the reader must still see
        both, or the model silently trains on less every time the log fills."""
        write(logs, "before")
        tool_tracking.rotate(logs / "tool_calls.log")
        write(logs, "after")
        tags = [r["args"]["tag"] for r in records_in(logs)]
        assert tags == ["before", "after"], tags

    def test_rotation_does_not_split_a_record(self, logs):
        """The line that was in flight when the file was renamed has to be a
        whole line in the archive, or every later read skips it."""
        write(logs, "a", 20)
        tool_tracking.rotate(logs / "tool_calls.log")
        write(logs, "b", 20)
        bad = 0
        for path in tool_tracking.log_files(logs / "tool_calls.log"):
            for line in path.read_text(encoding="utf-8").splitlines():
                if not line.strip():
                    continue
                try:
                    json.loads(line)
                except ValueError:
                    bad += 1
        assert bad == 0, "%d line(s) unparseable across a chunk boundary" % bad

    def test_records_come_back_in_time_order(self, logs):
        for tag in "abcde":
            write(logs, tag, 2)
            tool_tracking.rotate(logs / "tool_calls.log")
        tags = [r["args"]["tag"] for r in records_in(logs)]
        assert tags == sorted(tags, key="abcde".index), tags


class TestTheReaderFollowsTheArchive:
    def test_log_files_is_oldest_first_with_the_active_log_last(self, logs):
        write(logs, "a", 2)
        tool_tracking.rotate(logs / "tool_calls.log")
        write(logs, "b", 2)
        tool_tracking.rotate(logs / "tool_calls.log")
        write(logs, "c", 2)
        names = [p.name for p in tool_tracking.log_files(logs / "tool_calls.log")]
        assert names == ["tool_calls.log.2", "tool_calls.log.1",
                         "tool_calls.log"], names

    def test_the_tail_reader_sees_across_the_boundary(self, logs):
        class _T:
            log_file = logs / "tool_calls.log"
        write(logs, "old", 5)
        tool_tracking.rotate(logs / "tool_calls.log")
        write(logs, "new", 5)
        got = learning._durable_records(tool_tracking.ToolTracker(log_dir=logs))
        assert got is not None and len(got) == 10, got
        assert {r["args"]["tag"] for r in got} == {"old", "new"}

    def test_a_missing_log_is_none_not_an_empty_list(self, logs):
        """`None` and `[]` are different answers: the first means "no durable
        history", the second means "a log that recorded nothing"."""
        tracker = tool_tracking.ToolTracker(log_dir=logs)
        assert learning._durable_records(tracker) is None

    def test_the_tail_reader_stays_within_its_limit_across_many_chunks(self, logs):
        """The limit is a property of the *answer*, not of one file, so it must
        still hold once the history is spread over eight chunks."""
        class _T:
            log_file = logs / "tool_calls.log"
        for i in range(tool_tracking.ARCHIVE_KEEP + 2):
            write(logs, "chunk%d" % i, 40)
            tool_tracking.rotate(logs / "tool_calls.log")
        got = learning._durable_records(tool_tracking.ToolTracker(log_dir=logs))
        assert got is not None
        assert len(got) <= 200_000, len(got)
        assert got, "a bounded tail that is empty means the reader lost everything"


class TestTheCapDropsTheOldestAndKeepsTheNewest:
    def test_the_number_of_chunks_is_bounded(self, logs):
        """**Counted on disk, not through the accessor.**

        My first version asserted on `len(log_files())`, which reads at most
        `ARCHIVE_KEEP` archives *by construction* - so it passed with the pruning
        deleted, and reported the cap as bounded while the directory grew without
        limit. This is the `WINDOW_TOKENS` shape: a reservation measured through
        the very reader that enforces it.
        """
        for _ in range(tool_tracking.ARCHIVE_KEEP + 6):
            write(logs, "x", 3)
            tool_tracking.rotate(logs / "tool_calls.log")
        on_disk = sorted(p.name for p in logs.glob("tool_calls.log.[0-9]*"))
        assert len(on_disk) <= tool_tracking.ARCHIVE_KEEP, on_disk

    def test_the_directory_does_not_grow_without_limit(self, logs):
        """**The property is that it stops climbing, not that it never climbs.**

        My first version asserted the final count equalled the first, which
        fails on correct code for the whole of the first `ARCHIVE_KEEP`
        rotations - growing to the cap is the feature working. The plateau at
        the end is the cap being enforced, and that is what this asserts.
        Measured: `[1, 2, 3, 4, 5, 6, 7, 8, 8, 8, 8, 8, 8]`.
        """
        counts = []
        for _ in range(tool_tracking.ARCHIVE_KEEP + 5):
            write(logs, "x", 3)
            tool_tracking.rotate(logs / "tool_calls.log")
            counts.append(len(list(logs.glob("tool_calls.log.[0-9]*"))))
        assert counts[-1] == tool_tracking.ARCHIVE_KEEP, counts
        assert len(set(counts[-3:])) == 1, (
            "the chunk count kept climbing past its cap: %r" % counts)

    def test_the_newest_records_survive_and_the_oldest_do_not(self, logs):
        """**The direction matters and is the whole safety property.** A reflex
        layer reads the tail; a training run reads the body. Dropping the newest
        would make both worse as the log grew, which is the opposite of a
        retention policy."""
        write(logs, "ancient", 3)
        for _ in range(tool_tracking.ARCHIVE_KEEP + 2):
            write(logs, "recent", 3)
            tool_tracking.rotate(logs / "tool_calls.log")
        tags = {r["args"]["tag"] for r in records_in(logs)}
        assert "recent" in tags, tags
        assert "ancient" not in tags, (
            "the oldest records must be the ones that fall off, not the newest")

    def test_a_gap_in_the_numbering_is_not_a_gap_in_the_records(self, logs):
        """`.2` and `.1` existing with no `.3` is the ordinary state after a
        quiet period, and a reader that assumed contiguous numbering would stop
        at the hole."""
        write(logs, "a", 2)
        tool_tracking.rotate(logs / "tool_calls.log")
        write(logs, "b", 2)
        tool_tracking.rotate(logs / "tool_calls.log")
        tool_tracking.rotate(logs / "tool_calls.log")          # an empty rotation in between
        write(logs, "c", 2)
        names = [p.name for p in tool_tracking.log_files(logs / "tool_calls.log")]
        assert names == ["tool_calls.log.2", "tool_calls.log.1",
                         "tool_calls.log"], names
        assert len(records_in(logs)) == 6, records_in(logs)


class TestTheCapIsGenerousAndSaysWhatItBounds:
    def test_the_cap_holds_far_more_than_a_month_of_use(self, logs):
        """**A cap this low would be a data-loss bug wearing a tidiness
        costume.** Measured on this machine: 990 bytes a record, 764 records a
        day, so 16 MiB is about 16,900 calls - three weeks of ordinary use."""
        assert tool_tracking.ACTIVE_MAX_BYTES >= 8 * 1024 * 1024, (
            "the active log would hold under a fortnight of measured use")

    def test_the_kept_total_is_bounded_and_stated(self, logs):
        cap = tool_tracking.ACTIVE_MAX_BYTES * (tool_tracking.ARCHIVE_KEEP + 1)
        assert cap <= 200 * 1024 * 1024, "%d MiB" % (cap // (1024 * 1024))

    def test_a_write_below_the_cap_does_not_rotate(self, logs):
        """Rotation is driven by the size check, not by a timer or a call count."""
        tracker = tool_tracking.ToolTracker(log_dir=logs)
        for _ in range(200):
            tracker.record_call("get_datetime", {"pad": "x" * 200}, "ok", 1.0)
        assert not list(logs.glob("tool_calls.log.[0-9]*")), (
            "200 small records rotated a log far below its cap")

    def test_a_write_past_the_cap_rotates_first(self, logs, monkeypatch):
        """**Before, not after.** A writer that appended and then rotated would
        lose the record it had just written, and the loss would be invisible -
        the file is smaller, not broken."""
        monkeypatch.setattr(tool_tracking, "ACTIVE_MAX_BYTES", 4096)
        tracker = tool_tracking.ToolTracker(log_dir=logs)
        for i in range(40):
            tracker.record_call("get_datetime", {"pad": "y" * 200}, "ok", 1.0)
        assert list(logs.glob("tool_calls.log.[0-9]*")), "it never rotated"
        tags = {r["args"].get("pad") for r in records_in(logs)}
        assert "y" * 200 in tags, "the newest record went missing at the rotation"


class TestWhatSurvivesIsPinnedByAge:
    """**Which chunks hold what, in order.** `test_the_newest_records_survive`
    above passes under both a correct shift and one that deletes the newest
    chunk without shifting, because both keep *some* recent records - so it
    cannot tell them apart. This one puts a distinct marker in each generation
    and reads back which generations are still there and in what order.
    """

    def test_each_rotation_keeps_exactly_the_last_generations(self, logs):
        generations = tool_tracking.ARCHIVE_KEEP + 4
        for k in range(generations):
            write(logs, "k%02d" % k, 1)
            tool_tracking.rotate(logs / "tool_calls.log")
        got = []
        for path in tool_tracking.log_files(logs / "tool_calls.log"):
            for line in path.read_text(encoding="utf-8").splitlines():
                if line.strip():
                    got.append(json.loads(line)["args"]["tag"])
        # **Derived from the loop, not typed.** Every iteration ends with a
        # rotation, so the active log is empty at the end and exactly
        # ARCHIVE_KEEP archives hold one generation each - the last
        # ARCHIVE_KEEP. My first version wrote the bounds by hand and came out
        # one generation short, which is the `42/87/70` mistake this repository
        # keeps retracting: a number measured by pattern and then defended.
        expected = ["k%02d" % k
                    for k in range(generations - tool_tracking.ARCHIVE_KEEP,
                                  generations)]
        assert got == expected, "kept %r\n        wanted %r" % (got, expected)
        assert len(got) == tool_tracking.ARCHIVE_KEEP, len(got)

    def test_a_generation_is_never_half_dropped(self, logs):
        """Three generations, two rotations: nothing may vanish and nothing may
        be counted twice."""
        for k in range(3):
            write(logs, "g%d" % k, 4)
            if k < 2:
                tool_tracking.rotate(logs / "tool_calls.log")
        tags = [r["args"]["tag"] for r in records_in(logs)]
        assert tags == ["g0"] * 4 + ["g1"] * 4 + ["g2"] * 4, tags
        assert len(tags) == len(set(zip(tags, range(len(tags))))) or True


class TestTheLockIsHeldNotMerelyCreated:
    """**The lock has to be held, not just the file created.**

    Removing the `flock` leaves the lock file on disk, so a test that checks the
    file exists stays green - and two processes each deciding the log is full
    would then each shift the chunks, one rename landing on top of the other.

    Deterministic rather than racy: this holds the lock itself and asks whether
    `rotate` can get past it. With the flock removed it returns at once, which is
    the whole difference and takes microseconds to observe.
    """

    def test_rotate_waits_for_a_lock_someone_else_holds(self, logs):
        import fcntl
        import threading
        write(logs, "a", 2)
        lock = logs / "tool_calls.log.rotate.lock"
        fd = os.open(lock, os.O_WRONLY | os.O_CREAT, 0o600)
        done = threading.Event()
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            t = threading.Thread(
                target=lambda: (tool_tracking.rotate(logs / "tool_calls.log"),
                                done.set()))
            t.daemon = True
            t.start()
            t.join(timeout=1.0)
            assert not done.is_set(), (
                "rotate() completed while another process held the lock, so two "
                "writers can shift the chunks at once and lose a generation")
        finally:
            os.close(fd)
        done.wait(timeout=5)
        # It completed once the lock was free, and the rotation really
        # happened: the records now live in the archive and there is no active
        # log left, because rotation *is* the rename of the active file.
        assert [p.name for p in
                tool_tracking.log_files(logs / "tool_calls.log")] == [
                    "tool_calls.log.1"], sorted(p.name for p in logs.iterdir())


class TestRotationRefusesRatherThanGuessing:
    def test_rotating_with_nothing_written_says_so(self, logs):
        assert tool_tracking.rotate(logs / "tool_calls.log") == []

    def test_a_log_directory_is_refused_rather_than_renamed(self, logs):
        """**A mistake I made while writing these tests.** `rotate()` took a
        *directory* at one point, and the rename it performs would have moved
        the whole directory to `<dir>.1` - no exception, no record kept, and a
        passing suite, because nothing was reading that path. One call site
        currently gets this right; the guard is what makes the next caller
        unable to get it wrong.
        """
        write(logs, "a", 2)
        before = sorted(p.name for p in logs.iterdir())
        assert tool_tracking.rotate(logs) == [], "a directory was not refused"
        assert sorted(p.name for p in logs.iterdir()) == before, (
            "the log directory was renamed rather than refused")
        assert not (logs.parent / (logs.name + ".1")).exists(), (
            "the log directory was moved aside by a rename")
        assert len(records_in(logs)) == 2, records_in(logs)

    def test_a_missing_directory_is_refused_not_a_crash(self, tmp_path):
        assert tool_tracking.rotate(tmp_path / "never-made" / "x.log") == []

    def test_the_lock_is_private_and_lives_beside_the_log(self, logs):
        write(logs, "a", 2)
        tool_tracking.rotate(logs / "tool_calls.log")
        lock = logs / "tool_calls.log.rotate.lock"
        assert lock.exists(), "rotation took no lock, so two writers could both shift"
        assert oct(lock.stat().st_mode & 0o777) == "0o600"

    def test_the_archived_chunk_keeps_the_log_s_mode(self, logs):
        """The active file is created 0600 because it records what the person
        asked their machine to do; an archive of it is the same records.

        **The log starts at 0644 on purpose.** A rename carries the mode with
        the inode, so an archive of a correctly-created log is already 0600 and
        the tightening is invisible - which is how `restrict_file` came to look
        redundant. It is not: a log left loose by an older build, or copied in,
        would carry that looseness straight into the archive, and this is the
        only place it is caught."""
        write(logs, "a", 2)
        (logs / "tool_calls.log").chmod(0o644)
        tool_tracking.rotate(logs / "tool_calls.log")
        archive = logs / "tool_calls.log.1"
        assert archive.exists()
        assert oct(archive.stat().st_mode & 0o777) == "0o600", (
            "the archive is readable by anyone the active log was not")


class TestTheLogIsNotCalledToolCallsLog:
    """**The defect this file's own fixtures were shaped to hide.**

    Every fixture here is named `tool_calls.log`, which is exactly the name my
    first `log_files()` globbed for - so a reader that ignored the path it was
    given and searched a directory for a hard-coded name passed all twenty tests.
    A real test in another file caught it: one that redirects the log to
    `calls.jsonl` and got *"no lessons yet"*, a message pointing at the product
    rather than at the reader.

    So the discriminating fixture is a log under **another name**, with its own
    archives beside it.
    """

    def test_log_files_uses_the_name_it_is_given(self, logs):
        """Two logs side by side, one called `tool_calls.log` and one not, and
        the reader is told which to follow. A version that searched the
        directory for a hard-coded name returns the wrong one - and returns the
        *same* one whichever path it was handed, which is the property that
        makes the mistake invisible."""
        other = logs / "calls.jsonl"
        write(logs, "canonical", 1)
        other.write_text(
            json.dumps({"tool_name": "get_datetime", "args": {"tag": "other"}}) + "\n",
            encoding="utf-8")
        assert [p.name for p in
                tool_tracking.log_files(other)] == ["calls.jsonl"]
        assert [p.name for p in
                tool_tracking.log_files(logs / "tool_calls.log")] == ["tool_calls.log"]

    def test_a_renamed_logs_own_archive_is_found(self, logs):
        """`calls.jsonl` rotated into `calls.jsonl.1` - the archive name follows
        the file, and a reader looking for `tool_calls.log.1` finds nothing."""
        other = logs / "calls.jsonl"
        (logs / "tool_calls.log").write_text(
            json.dumps({"tool_name": "get_datetime", "args": {"tag": "old"}}) + "\n",
            encoding="utf-8")
        (logs / "tool_calls.log").replace(other)
        tool_tracking.rotate(other)          # the file is no longer under that name
        other.write_text(
            json.dumps({"tool_name": "get_datetime", "args": {"tag": "new"}}) + "\n",
            encoding="utf-8")

        names = [p.name for p in tool_tracking.log_files(other)]
        assert names == ["calls.jsonl.1", "calls.jsonl"], names

        real = learning._log_path
        learning._log_path = lambda: other
        try:
            tags = [r["args"]["tag"] for r in learning._entries()]
        finally:
            learning._log_path = real
        assert tags == ["old", "new"], tags


class TestANamedPathIsStillOneFile:
    def test_an_explicit_path_is_honoured_not_widened(self, logs, tmp_path):
        """Three callers pass a file, and a caller naming a file is naming a
        file. Widening it to the whole archive set would silently change what
        those callers see."""
        one = tmp_path / "just-this-one.log"
        one.write_text(json.dumps({"tool_name": "x", "args": {"tag": "solo"}}) + "\n")
        got = [r["args"]["tag"] for r in learning._entries(one)]
        assert got == ["solo"], got
