"""The meeting has to survive a reboot, and be findable to be read again.

**The defect this file exists for.** `capture()` took whatever path the recorder
handed back, and `AudioRecorder` writes with `tempfile.mkstemp` — so every
meeting landed in `/tmp`. On a ShaniOS install `/tmp` is cleared on reboot and
`/var` is tmpfs by the image's own fstab, which means an hour of microphone was
discarded by the next restart. The feature's whole output was its own throwaway.

Found while building the Meetings panel, which could not list meetings because
there was no directory of meetings to list — the panel asked the right question
and the backend had no answer.
"""

from __future__ import annotations

import os
import pathlib
import time

import pytest

from shani_chronoa import meeting as M


@pytest.fixture(autouse=True)
def _isolated_state(tmp_path, monkeypatch):
    """Every path here resolves per call; pin the ones that resolve it."""
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state"))
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "data"))
    yield


def _wav(tmp_path, name="chronoa-rec-a.wav", body=b"RIFFmeeting"):
    path = tmp_path / name
    path.write_bytes(body)
    return path


class TestAMeetingIsKept:
    def test_it_lands_beside_the_other_state_not_in_the_temp_dir(self, tmp_path):
        source = _wav(tmp_path)
        kept = pathlib.Path(M._keep(str(source)))
        assert M.recordings_dir() in kept.parents, (
            f"{kept} is not under {M.recordings_dir()} - it will be cleared by "
            "the next reboot")
        assert kept.read_bytes() == b"RIFFmeeting"

    def test_the_temp_file_is_gone_afterwards(self, tmp_path):
        source = _wav(tmp_path)
        M._keep(str(source))
        assert not source.exists(), "the recording was copied, not moved"

    def test_the_directory_is_private(self):
        mode = M.recordings_dir().stat().st_mode & 0o777
        assert mode == 0o700, (
            f"0{mode:o} - `mkdir(mode=)` applies to the leaf only when "
            "parents=True, and leaves an existing directory as it was, so the "
            "mode has to be set separately")

    def test_a_move_that_cannot_happen_keeps_the_file(self, tmp_path, monkeypatch):
        """A failed rename must not lose an hour of somebody's meeting.

        The honest answer is the temporary path **with its bytes**, because a
        file the system will clear is still better than no file to point at -
        and worse than saying so plainly.
        """
        source = _wav(tmp_path)
        monkeypatch.setattr(M.shutil, "move",
                            lambda *a, **k: (_ for _ in ()).throw(OSError("no space")))
        kept = M._keep(str(source))
        assert kept == str(source), kept
        assert pathlib.Path(kept).read_bytes() == b"RIFFmeeting"

    def test_nothing_recorded_is_not_a_failure(self):
        assert M._keep(None) is None

    def test_a_path_that_is_not_a_file_is_not_kept(self, tmp_path):
        assert M._keep(str(tmp_path / "never-existed.wav")) is None


class TestTwoMeetingsInTheSameSecond:
    """The timestamp is the name, so two captures inside one second collide.

    And the loser here is not a duplicate row: it is an hour of somebody's
    meeting, overwritten by a later hour.
    """

    def test_neither_is_overwritten(self, tmp_path):
        stamp = time.time()
        kept = []
        for index in range(3):
            source = _wav(tmp_path, f"chronoa-rec-{index}.wav",
                          b"RIFFmeeting-number-%d" % index)
            os.utime(source, (stamp, stamp))
            kept.append(pathlib.Path(M._keep(str(source))))
        assert len(set(kept)) == 3, f"two meetings share a name: {kept}"
        assert sorted(p.read_bytes() for p in kept) == [
            b"RIFFmeeting-number-0", b"RIFFmeeting-number-1",
            b"RIFFmeeting-number-2"]

    def test_the_first_one_is_left_alone_by_the_second(self, tmp_path):
        stamp = time.time()
        first = _wav(tmp_path, "a.wav", b"RIFFfirst")
        os.utime(first, (stamp, stamp))
        one = pathlib.Path(M._keep(str(first)))
        second = _wav(tmp_path, "b.wav", b"RIFFsecond")
        os.utime(second, (stamp, stamp))
        two = pathlib.Path(M._keep(str(second)))
        assert one != two
        assert one.read_bytes() == b"RIFFfirst"


class TestTheShelf:
    def test_it_lists_what_is_there_newest_first(self):
        older = M.recordings_dir() / "meeting-20260101-000000.wav"
        newer = M.recordings_dir() / "meeting-20260102-000000.wav"
        older.write_bytes(b"R" * 100)
        newer.write_bytes(b"R" * 200)
        os.utime(older, (1_700_000_000, 1_700_000_000))
        os.utime(newer, (1_700_000_100, 1_700_000_100))
        found = M.list_recordings()
        assert [item["name"] for item in found] == [
            "meeting-20260102-000000.wav", "meeting-20260101-000000.wav"]
        assert found[0]["bytes"] == 200

    def test_an_empty_shelf_is_an_empty_list(self):
        assert M.list_recordings() == []

    def test_something_that_is_not_a_meeting_is_not_listed(self):
        (M.recordings_dir() / "notes.txt").write_text("not a recording")
        (M.recordings_dir() / "meeting-20260101-000000.wav").write_bytes(b"R")
        assert [i["name"] for i in M.list_recordings()] == [
            "meeting-20260101-000000.wav"]

    def test_capture_puts_it_there(self, monkeypatch, tmp_path):
        """**The whole defect, end to end:** what `capture()` returns is a path
        under the recordings directory, not a temp file."""
        source = _wav(tmp_path)

        class _Recorder:
            _auto_stop_thread = None

            def start_auto_stop(self, on_done, **_kw):
                on_done(str(source))
                return True

        class _Thread:
            def join(self, timeout=None):
                return None

        result = M.capture(timeout=5, recorder=_Recorder())
        assert result.wav, result
        assert M.recordings_dir() in pathlib.Path(result.wav).parents, result.wav
        assert [i["path"] for i in M.list_recordings()] == [result.wav]

class TestTheShelfIsOrderedHonestly:
    """`tar` preserves mtimes, so a restored backup gives every meeting *byte
    identical* ones. A sort on mtime alone then falls back to directory order,
    while the docstring still says "newest first" - and the panel's status row
    calls the first entry "newest" by name.
    """

    def _two_with_the_same_mtime(self):
        older = M.recordings_dir() / "meeting-20260101-090000.wav"
        newer = M.recordings_dir() / "meeting-20260102-140000.wav"
        older.write_bytes(b"R" * 10)
        newer.write_bytes(b"R" * 20)
        same = 1_700_000_000
        os.utime(older, (same, same))
        os.utime(newer, (same, same))       # what `tar -x` does
        return older, newer

    def test_two_meetings_with_one_mtime_are_still_newest_first(self):
        self._two_with_the_same_mtime()
        found = [item["name"] for item in M.list_recordings()]
        assert found == ["meeting-20260102-140000.wav", "meeting-20260101-090000.wav"], (
            f"{found} - the two share an mtime, so this is being decided by "
            "something that does not know which meeting is newer")

    def test_each_row_carries_the_time_its_name_encodes(self):
        self._two_with_the_same_mtime()
        found = M.list_recordings()
        assert found[0]["when"] == "20260102-140000", found
        assert found[1]["when"] == "20260101-090000", found

    def test_a_copy_that_changes_the_mtime_cannot_reorder_the_shelf(self):
        """**The mtime is not the capture time.** It is when the file was last
        written, so `cp` without `-p` changes it and `tar` restores every one of
        them to the same second. The name is the capture time by construction -
        `_keep()` puts it there so two meetings in one second cannot collide.

        This is the defect measured: two meetings whose mtimes were 50
        microseconds apart came back in the wrong order while the panel named the
        first of them "newest".
        """
        older = M.recordings_dir() / "meeting-20260101-090000.wav"
        newer = M.recordings_dir() / "meeting-20260102-140000.wav"
        older.write_bytes(b"R")
        newer.write_bytes(b"R")
        # The newer meeting was *copied*, so its mtime is older than the
        # original's - which is what any restore or move does.
        os.utime(older, (1_700_090_000, 1_700_090_000))
        os.utime(newer, (1_700_000_000, 1_700_000_000))
        assert [i["name"] for i in M.list_recordings()] == [
            "meeting-20260102-140000.wav", "meeting-20260101-090000.wav"], (
            "a copy reordered the shelf; the mtime is not the capture time")

    def test_a_file_named_something_else_still_orders_stably(self):
        """The name is read, not assumed: a recording somebody renamed by hand
        still lists, and two of them do not swap places between runs."""
        # A recording somebody renamed by hand: it matches `meeting-*.wav`, so
        # it is a meeting, and its name carries no timestamp - which must make it
        # sort stably rather than blow up or fall out of the shelf.
        (M.recordings_dir() / "meeting-notatimestamp.wav").write_bytes(b"R")
        (M.recordings_dir() / "zz-notes.wav").write_bytes(b"R")
        listed = [i["name"] for i in M.list_recordings()]
        # **The renamed one is listed.** The first version of this test only
        # compared two consecutive runs, so it passed with the glob narrowed to
        # `meeting-2*` and the file it was written for silently vanished - a
        # mutation run reported as a survivor, which is why this line is here.
        assert "meeting-notatimestamp.wav" in listed, listed
        # And a file that is not a meeting is not one, whatever else is true of
        # it - the glob is the whole of that rule.
        assert "zz-notes.wav" not in listed, listed
        second = [i["name"] for i in M.list_recordings()]
        assert listed == second, (listed, second)
