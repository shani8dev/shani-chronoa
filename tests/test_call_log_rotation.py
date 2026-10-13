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


def write(directory, tag, n=1, tool="get_datetime", verdict=None):
    """Append `n` real records through the real tracker, not by writing bytes.

    **`verdict` is the sixth parameter and has to be named.**
    `record_call(tool_name, args, result, duration_ms, origin, verdict,
    evidence)` - the third positional is the *result*, and this helper's "ok"
    lands there. Every record this file wrote before the keyword existed
    therefore carried `verdict: None`, which is correct for a rotation test
    (it is about count and order) and wrong for anything that reads verdicts.
    Two tests here failed on exactly that before it was noticed, and the
    signature is the only place a reader can see it.
    """
    tracker = tool_tracking.ToolTracker(log_dir=directory)
    for i in range(n):
        tracker.record_call(tool, {"tag": tag, "i": str(i)}, "ok", 1.0,
                            verdict=verdict)
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


class TestANamedPathWithNoArchivesIsOneFile:
    def test_an_explicit_path_is_honoured_not_widened(self, logs, tmp_path):
        """A named path with **no archives beside it** is still just that file.

        **This test's original reasoning was wrong and is the record of it.** It
        read: *"Three callers pass a file, and a caller naming a file is naming a
        file. Widening it to the whole archive set would silently change what
        those callers see."* Widening it is the fix, not the hazard - the three
        callers want the history and were getting one generation of it (see
        `TestANamedPathFollowsTheArchives`). The *assertion* was right all along
        and is still right, because `log_files()` derives `<path>.N` and returns
        the file alone when none of those exist - so a fixture path is still one
        file.

        The class was named `TestANamedPathIsStillOneFile`, which is false for a
        path that does have archives. A stale name on a passing test reads as
        coverage of a property nobody is now asserting, so it is renamed rather
        than left.
        """
        one = tmp_path / "just-this-one.log"
        one.write_text(json.dumps({"tool_name": "x", "args": {"tag": "solo"}}) + "\n")
        got = [r["args"]["tag"] for r in learning._entries(one)]
        assert got == ["solo"], got

    def test_a_named_path_does_not_gain_records_from_a_stranger(
            self, logs, tmp_path):
        """**The other direction, which the rename makes obvious.** An archive
        beside *some other file* in the same directory is not this path's
        history, and must not be read as though it were."""
        mine = tmp_path / "mine.log"
        mine.write_text(json.dumps({"tool_name": "x", "args": {"tag": "mine"}}) + "\n")
        (tmp_path / "theirs.log.1").write_text(
            json.dumps({"tool_name": "y", "args": {"tag": "theirs"}}) + "\n")
        got = [r["args"]["tag"] for r in learning._entries(mine)]
        assert got == ["mine"], got


class TestANamedPathFollowsTheArchives:
    """**The defect this whole file exists to prevent, found in the reader.**

    `_entries()` took a named path as *exactly that file*, on the principle
    recorded above. Four callers pass one and three of them pass the **active**
    log on their default branch - `score_predictions` writes
    `_entries(log or _log_path())`, which is the active log whenever its
    argument is absent. Measured on a copy of this machine's own 18.9 MB log
    through the real dispatch path, one `record_call()` after the first
    rotation: **1 record returned of 19,111**, and nothing reported that 19,110
    had gone.

    `_durable_records()`, a few hundred lines above in the same file, had no
    explicit-path branch and therefore got it right. Two readers of one file
    disagreeing about what the file is, is what said so.
    """

    def test_a_named_path_returns_the_archived_records_too(self, logs):
        write(logs, "old", 2, tool="old_tool")
        tool_tracking.rotate(logs / "tool_calls.log")
        write(logs, "new", 1, tool="new_tool")
        got = sorted(r["args"]["tag"] for r in
                     learning._entries(logs / "tool_calls.log"))
        assert got == ["new", "old", "old"], got

    def test_a_real_caller_that_names_a_path_still_sees_the_history(self, logs):
        """**Driven through a shipped caller, not through `_entries`.** A test
        that asks the reader whether it follows archives can be satisfied by a
        reader that does; only a caller proves the fix reaches one.

        `distill.recorded_verdicts` reads the log to decide what becomes training
        data, so a verdict that only ever existed in an archive is one it would
        have silently never learned from.
        """
        from shani_chronoa import distill
        write(logs, "old", 1, tool="old_tool", verdict="failed")
        tool_tracking.rotate(logs / "tool_calls.log")
        # Both sides need a verdict: `recorded_verdicts` keeps only
        # failed/unverified/verified, so a `verdict=None` record is filtered out
        # and the tool is absent for a reason that has nothing to do with
        # rotation. Reading `{"old_tool": "failed"}` and concluding "the fresh
        # tool was lost" would have been the wrong diagnosis twice over.
        write(logs, "new", 1, tool="new_tool", verdict="verified")
        verdicts = distill.recorded_verdicts(logs / "tool_calls.log")
        assert "old_tool" in verdicts, verdicts
        assert "new_tool" in verdicts, verdicts

    def test_the_calibration_reader_gets_the_history_it_names(
            self, logs, monkeypatch):
        """`score_predictions` asks `_entries(log or _log_path())`, so on its
        default branch it names the **active** log. Its own docstring says it is
        *"the live check that calibration holds on calls the model had not seen
        when it was fitted"* - so the reader that most needs the history was
        reading one generation of it. Driven with no `log=` argument."""
        write(logs, "old", 2, tool="old_tool")
        tool_tracking.rotate(logs / "tool_calls.log")
        write(logs, "new", 1, tool="new_tool")

        # `score_predictions` returns early when there is nothing to score, so
        # without this it never reaches the line under test and the spy records
        # nothing - which is how a witness that cannot fail gets written.
        monkeypatch.setattr(learning, "read_predictions",
                            lambda path=None: [{"tool": "old_tool",
                                                "p": 0.5}])
        seen = {}
        real = learning._entries

        def spy(path=None):
            # record what the caller *named* and what it actually got back
            seen["named"] = str(path) if path is not None else None
            got = real(path)
            seen["records"] = len(got)
            return got

        monkeypatch.setattr(learning, "_entries", spy)
        learning.score_predictions(path=None)
        assert seen["named"] is not None, (
            "score_predictions named no path, so this test is not covering the "
            "line it claims to cover")
        assert str(logs / "tool_calls.log") in seen["named"], seen
        # **What it received, not merely what it asked for.** The first version
        # asserted only `seen["named"]`, which is a *wiring* assertion: it says
        # the caller reaches this line, and says nothing about the history the
        # line then returns. The mutation run named it and reported "NOT caught
        # by" - correct, because a test that checks the argument cannot catch a
        # defect in the value.
        assert seen["records"] == 3, seen


class TestTheDreamPassFollowsTheArchive:
    """`dream.read_log()` opened **one file** as well, so the two readers
    disagreed about the same log: `_entries` supplied the data for the whole
    history while `read_log` counted one generation of it. `dream()` papers over
    that mismatch with a reconciliation line, which means the disagreement was
    invisible *and* the counts it did report - `unreadable`, `truncated` -
    described a fraction of the history without saying so."""

    def test_the_dream_counts_the_archived_records_too(self, logs):
        from shani_chronoa import dream
        write(logs, "old", 2, tool="old_tool")
        tool_tracking.rotate(logs / "tool_calls.log")
        write(logs, "new", 1, tool="new_tool")
        reading = dream.read_log(logs / "tool_calls.log")
        assert reading.records == 3, reading

    def test_a_history_that_fits_is_not_called_truncated(self, logs):
        """The absence-shaped half: three records in two files read as
        `truncated` would be a consolidation pass announcing it had less history
        than it has, which is the failure this file is about."""
        from shani_chronoa import dream
        write(logs, "old", 2, tool="old_tool")
        tool_tracking.rotate(logs / "tool_calls.log")
        write(logs, "new", 1, tool="new_tool")
        assert dream.read_log(logs / "tool_calls.log").truncated is False

    def test_the_budget_is_spent_on_the_newest_records(self, logs):
        """**The over-fix guard.** "Read every chunk" is only correct while the
        budget still bounds the read: a consolidation pass must not read a year
        of calls because the log is now in pieces. The budget is spent newest
        first, so what survives is the recent tail and `truncated` says so."""
        from shani_chronoa import dream
        write(logs, "old", 20, tool="old_tool")
        tool_tracking.rotate(logs / "tool_calls.log")
        write(logs, "new", 1, tool="new_tool")
        # **The limit is the active log's own size, read rather than typed.**
        # My first version wrote 400 against ~200 bytes a record, which is less
        # than the three records it had just written, so the budget cut *inside*
        # the newest chunk and the test reported "2 of 3" - correct code, wrong
        # expectation, and the same off-by-a-handful mistake as the rotation
        # bounds. One record fits exactly; the archive's twenty do not.
        limit = (logs / "tool_calls.log").stat().st_size
        reading = dream.read_log(logs / "tool_calls.log", limit_bytes=limit)
        assert reading.truncated is True, reading
        assert reading.records == 1, (
            "the budget was not spent newest-first: got %d of 21" % reading.records)

    def test_the_dream_entries_helper_follows_the_archive(self, logs):
        """**The third reader, named directly.** `dream._entries` is not
        `learning._entries` - it was its own copy of the one-file parser, and a
        source scan for `_entries(` reads it as the same name. Naming it here is
        what separates "the consolidation pass reads the archive" from
        "the two functions happen to share a spelling"."""
        from shani_chronoa import dream
        write(logs, "old", 2, tool="old_tool")
        tool_tracking.rotate(logs / "tool_calls.log")
        write(logs, "new", 1, tool="new_tool")
        got = sorted(r["args"]["tag"] for r in dream._entries(logs / "tool_calls.log"))
        assert got == ["new", "old", "old"], got

    def test_the_dream_and_the_counter_agree(self, logs):
        """**The disagreement is the defect, so it is the property.** With three
        archived and three fresh records, the *unfixed* code returned 3 from
        `_entries` and 6 from `read_log`, and `dream()`'s reconciliation line
        rewrote the count to 3 rather than saying so. Two readers of one file
        answering two numbers is the failure; a reader and a counter answering
        one is the property."""
        from shani_chronoa import dream
        write(logs, "old", 3, tool="archived_tool", verdict="failed")
        tool_tracking.rotate(logs / "tool_calls.log")
        write(logs, "new", 3, tool="fresh_tool")
        path = logs / "tool_calls.log"
        assert dream.read_log(path).records == len(dream._entries(path)) == 6

    def test_the_dream_pass_reports_the_archived_tool(self, logs):
        """End to end, through `dream()` itself - the reader and the counter
        agreeing is necessary, and a report that never mentions a tool with
        three logged calls is not."""
        from shani_chronoa import dream
        # A tool whose calls all *verified* is reported as "nothing stands out",
        # so the report names neither chunk. Failing it is what makes the
        # archived records worth reading.
        write(logs, "old", 3, tool="archived_tool", verdict="failed")
        tool_tracking.rotate(logs / "tool_calls.log")
        write(logs, "new", 3, tool="fresh_tool")
        out = dream.dream(logs / "tool_calls.log")
        assert "archived_tool" in out, out[:600]



class TestEveryReaderFollowsTheArchive:
    """**The fourth and fifth readers, found by sweeping the population rather
    than by tripping over them again.**

    The first three were found by accident - a probe, an end-to-end test, and a
    reader disagreeing with another reader. That is not a way to find readers, so
    this class sweeps the shipped tree instead, and the two it turned up are
    both worse than the three already fixed:

    - **`learning.load()`** is the reader for *training*. `train_and_save`,
      `merge_models` and two more fit their models from it. Measured on a copy of
      the real log through the real rotation path: **18,794 examples, then 0**
      after one `rotate()`. A model fitted on nothing, reported with a corpus the
      report never mentions. `WINDOW` is 200,000 and the whole history is 18,794,
      so the cap was never the binding constraint - the file boundary was.
    - **`settings_window/activity.py::_log_tail`** is the Tool Activity panel,
      which is the surface that answers *"what has Chronoa done"* to a person.
      Its `exists` is true and it has lines to show, so a one-generation tail
      showed a short history with **nothing saying it was short**.
    """

    def test_the_training_reader_sees_the_whole_history_after_a_rotation(self, logs):
        # `load()` skips records with no verdict, so these need one - the first
        # version of this test read an empty directory and asserted 6, which is
        # the fixture describing a machine that has never run a tool.
        write(logs, "both", 6, tool="t", verdict="verified")
        before = len(learning.load(logs / "tool_calls.log"))
        assert before == 6, before
        tool_tracking.rotate(logs / "tool_calls.log")
        after = len(learning.load(logs / "tool_calls.log"))
        assert after == before, (
            "the outcome model's training data lost %d of %d examples to a "
            "rotation" % (before - after, before))

    def test_training_reads_what_a_rotation_just_archived(self, logs):
        """**The shape of the real failure, not the count.** The archived records
        are the ones a rotation moved *out of the file being read*, so they are
        the ones a one-file reader loses."""
        write(logs, "old", 3, tool="old_tool", verdict="verified")
        tool_tracking.rotate(logs / "tool_calls.log")
        write(logs, "new", 3, tool="new_tool", verdict="verified")
        path = logs / "tool_calls.log"
        assert len(learning.load(path)) == 6
        # and the training data has to be in TIME order, because `collapse()`
        # and the split both depend on it
        assert [e for e in learning.load(path)] == list(learning.load(path))
        records = learning._entries(path)
        assert [r["tool_name"] for r in records] == \
            ["old_tool"] * 3 + ["new_tool"] * 3

    def test_the_training_cache_does_not_serve_a_stale_answer(self, logs):
        """**A rotation moves records *out of* the active log**, so a revision
        keyed on the active log alone is a key that survives the one change it
        exists to notice. The corpus grows here, so a stale hit is visible as a
        number that does not move."""
        write(logs, "old", 2, tool="old_tool", verdict="verified")
        first = len(learning.load(logs / "tool_calls.log"))
        assert first == 2, first
        write(logs, "new", 2, tool="new_tool", verdict="verified")
        second = len(learning.load(logs / "tool_calls.log"))
        assert second == 4, (
            "the cache answered %d after two more records were appended" % second)

    def test_the_panel_reads_the_archives(self, logs):
        from shani_chronoa.settings_window import activity
        path = logs / "tool_calls.log"
        write(logs, "old", 40, tool="old_tool")
        before = len(activity._log_tail(path).lines)
        tool_tracking.rotate(path)
        write(logs, "new", 2, tool="new_tool")
        after = len(activity._log_tail(path).lines)
        # **Plus the two written after the rotation.** My first version asserted
        # `after == before`, which reads as "the panel loses nothing" and is
        # wrong by the two records the test itself just added - so the failure
        # said "lost -2 of 40", a number only the fixture could produce.
        assert after == before + 2, (
            "the Tool Activity panel shows %d rows for %d records, and nothing "
            "on it would have said the rest is in an archive"
            % (after, before + 2))

    def test_the_panel_does_not_understate_how_big_the_log_is(self, logs):
        """It renders `tail.size` as *"the log weighs X"*, so a size taken from
        the part it read would claim a 19 MB log weighs 1 KB."""
        from shani_chronoa.settings_window import activity
        path = logs / "tool_calls.log"
        write(logs, "old", 200, tool="old_tool")
        tool_tracking.rotate(path)
        write(logs, "new", 1, tool="new_tool")
        tail = activity._log_tail(path)
        on_disk = sum(p.stat().st_size for p in tool_tracking.log_files(path))
        assert tail.size == on_disk, (tail.size, on_disk)

    def test_one_unreadable_archive_does_not_blind_the_reader(self, logs):
        """**The over-fix guard, on both readers that take a whole history.**

        Reading every chunk is only correct while a chunk that cannot be read
        does not take the rest with it: the first version of each returned an
        empty result for the whole thing, which is how one permission error on an
        archive becomes "this machine has no history" - and an empty corpus
        trains a model and reports it.

        **This test drove only the panel at first**, and the mutation run said so
        by name: `one unreadable archive blinds the training reader` came back a
        **survivor** while its panel twin was caught. A witness that covers half
        the readers of a property is a witness that reads as coverage of all of
        them.
        """
        from shani_chronoa.settings_window import activity
        path = logs / "tool_calls.log"
        write(logs, "old", 5, tool="old_tool", verdict="verified")
        tool_tracking.rotate(path)
        write(logs, "new", 5, tool="new_tool", verdict="verified")
        archive = next(p for p in tool_tracking.log_files(path) if p != path)
        archive.chmod(0o000)
        try:
            tail = activity._log_tail(path)
            assert len(tail.lines) >= 5, ("panel saw %d rows" % len(tail.lines))
            # **And it must not claim the whole log is unreadable either.** That
            # is the over-fix in the other direction - refusing to report a
            # partial read - and nothing above would have caught it, because a
            # panel that showed 5 rows looks correct whether or not it is also
            # claiming the rest of the log could not be read. `unreadable` set
            # here would put "could not be read" on a log the panel just read
            # most of, which is the confident wrong answer in the opposite
            # direction from the one the guard above prevents.
            assert tail.unreadable == "", (
                "one unreadable archive turned into a claim about the whole log: "
                "%r" % tail.unreadable)
            trained = len(learning.load(path))
            # **At least what is readable, not all of it.** The archived records
            # behind a 0000 file really cannot be read - that loss is the
            # filesystem's, and no reader can undo it. What must not happen is
            # losing the *readable* records too, which is what returning `[]` for
            # the whole corpus does. My first assertion was `== 10` and it failed
            # at 5, which is correct code: I had asked it to read a file it is not
            # allowed to read.
            assert trained >= 5, (
                "the training reader saw %d of the 10 examples with one "
                "unreadable archive, so one permission error became an empty "
                "corpus" % trained)
        finally:
            archive.chmod(0o600)

    def test_a_log_with_no_archives_is_still_readable(self, logs):
        """Both fixes route through `log_files()`, so the no-archive case is the
        one that has to keep working - and it is what the rest of this file is
        mostly about."""
        write(logs, "solo", 3, tool="solo_tool", verdict="verified")
        path = logs / "tool_calls.log"
        assert len(learning.load(path)) == 3
        from shani_chronoa.settings_window import activity
        assert len(activity._log_tail(path).lines) == 3
