"""`stopwatch` kept its run in `stopwatch.json` via a plain
`p.write_text(json.dumps(...))`.

That truncates the file first and only then writes, so a crash, a power loss,
or two writers at once leaves a truncated JSON document - and the next read
quietly returns `{}`, which reads as "the stopwatch is not running". The run
was never going to come back either way; this made sure you lose it *even
without* a crash being interesting.

Same shape for `set_sleep_inhibit`'s PID record, which then could not release
the inhibitor early by its recorded pid.

The fix writes a temp file and `os.replace`s it - the state is either the old
complete one or the new complete one, never the half of one.
"""

import json
import stat

import pytest

from shani_chronoa.skills import stopwatch


class TestWriteIsAtomic:
    def test_a_failed_write_keeps_the_old_state(self, tmp_path, monkeypatch):
        state = tmp_path / "stopwatch.json"
        state.write_text(json.dumps({"start": 1.0, "laps": [2.0]}))
        monkeypatch.setattr(stopwatch, "_path", lambda: state)

        def boom(fd, mode, encoding=None):
            raise OSError("disk full")

        # Make the *write* fail after the open; the temp file is the thing that
        # must absorb the failure.
        monkeypatch.setattr(stopwatch.os, "fdopen", boom)
        with pytest.raises(OSError):
            stopwatch._write({"start": 9.0}, state)
        # The visible file is untouched - the old run is not a truncated
        # document sitting where the next reader looks.
        assert json.loads(state.read_text()) == {"start": 1.0, "laps": [2.0]}, (
            "a failed atomic write must not leave the destination truncated")

    def test_a_successful_write_replaces_cleanly(self, tmp_path):
        state = tmp_path / "stopwatch.json"
        stopwatch._write({"start": 1.0, "laps": []}, state)
        stopwatch._write({"start": 1.0, "laps": [5.0]}, state)
        assert json.loads(state.read_text()) == {"start": 1.0, "laps": [5.0]}
        assert not state.with_suffix(".json.tmp").exists(), (
            "the temp file is left behind, so a later reclaim or listing sees it")

    def test_the_state_file_is_owner_only(self, tmp_path):
        state = tmp_path / "stopwatch.json"
        stopwatch._write({"start": 1.0}, state)
        assert stat.S_IMODE(state.stat().st_mode) == 0o600

    def test_round_trip_through_run(self, tmp_path, monkeypatch):
        state = tmp_path / "stopwatch.json"
        monkeypatch.setattr(stopwatch, "_path", lambda: state)
        stopwatch._run({"action": "start"}, now=100.0)
        stopwatch._run({"action": "lap"}, now=130.0)
        read = stopwatch._run({"action": "read"}, now=160.0)
        # **`read` reports only elapsed time, not laps.** My first version
        # asserted "lap" in the `read` output, which fails on correct code for
        # the right reason - laps only come back on `stop`.
        assert "1 min" in read, read
        stopped = stopwatch._run({"action": "stop"}, now=160.0)
        assert "lap 1" in stopped, stopped
