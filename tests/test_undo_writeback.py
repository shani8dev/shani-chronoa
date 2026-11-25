"""An undo is part of the call's history, not a separate story about it.

A change is recorded when a skill runs; the undo happens later. Marking the
record "done, then undone" is what lets the audit trail and the learning layer
see the whole life of the actuation. It is best effort: a restore must never
fail because there is no log to annotate.
"""

from __future__ import annotations

import json


from shani_chronoa import tool_tracking, undo_ring


def test_annotate_last_undo_marks_only_the_newest_record(tmp_path):
    tracker = tool_tracking.ToolTracker(log_dir=tmp_path)
    first = tracker.record_call("get_datetime", {}, "noon", 1.0)
    last = tracker.record_call("write_text_file", {}, "saved", 2.0, verdict="verified")
    assert tool_tracking.annotate_last_undo(tracker=tracker, log_dir=tmp_path)

    lines = [
        json.loads(line)
        for line in (tmp_path / "tool_calls.log").read_text().splitlines()
    ]
    assert lines[0]["undone"] is False
    assert lines[1]["undone"] is True
    assert last.undone is True
    assert first.undone is False

    record = tool_tracking.ToolCallRecord("t", {}, "r", 1.0)
    assert record.to_dict()["undone"] is False
    assert record.to_log_dict()["undone"] is False


def test_a_missing_log_is_not_an_excuse_to_fail_an_undo(tmp_path):
    tracker = tool_tracking.ToolTracker(log_dir=tmp_path)
    tracker.record_call("write_text_file", {}, "saved", 1.0, verdict="verified")
    (tmp_path / "tool_calls.log").unlink()
    assert not tool_tracking.annotate_last_undo(tracker=tracker, log_dir=tmp_path)
    assert tracker.get_calls()[-1].undone is False


def test_consuming_a_preimage_marks_the_call_as_undone(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path))
    entry = {
        "kind": "file",
        "path": str(tmp_path / "a.txt"),
        "at": 1.0,
        "content": "before",
    }
    undo_ring._append(entry)
    calls = []
    monkeypatch.setattr(
        tool_tracking, "annotate_last_undo", lambda **kw: calls.append(kw) or True
    )
    assert undo_ring.consume(entry) is True
    assert len(calls) == 1
    assert undo_ring.consume(entry) is False
    assert len(calls) == 1  # nothing was consumed the second time


def test_a_revert_of_a_turn_marks_the_call_as_undone(tmp_path, monkeypatch):
    from shani_chronoa.skills import undo_last_change

    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path))
    monkeypatch.setenv(undo_ring.TURN_ENV, "turn-1")
    target = tmp_path / "a.txt"
    target.write_text("after")
    undo_ring.record_file(target, b"before")
    calls = []
    monkeypatch.setattr(
        tool_tracking, "annotate_last_undo", lambda **kw: calls.append(kw) or True
    )
    restored, skipped = undo_last_change.revert_turn("turn-1")
    assert restored == [str(target)] and skipped == []
    assert len(calls) == 1
    assert target.read_text() == "before"


def _record(tracker, tag):
    return tracker.record_call(
        "write_text_file", {"tag": tag}, "saved", 1.0, verdict="verified"
    )


def _records(path):
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]


def test_a_call_rotated_out_of_the_active_log_is_still_annotated(tmp_path):
    """Rotation moved the call into `.1` and left the active log empty.

    The old search opened only the active file, found nothing, and reported the
    undo as unmarked - a restore whose call record stayed silent about being put
    back. The newest kept chunk now answers, and the ring agrees with it.
    """
    tracker = tool_tracking.ToolTracker(log_dir=tmp_path)
    call = _record(tracker, "rotated")
    active = tmp_path / "tool_calls.log"
    tool_tracking.rotate(active)
    assert not active.exists()
    archive = tool_tracking.archive_path(active, 1)
    assert archive.exists()

    assert tool_tracking.annotate_last_undo(tracker=tracker, log_dir=tmp_path)
    assert _records(archive)[-1]["undone"] is True
    assert call.undone is True


def test_the_newest_of_several_archives_is_annotated(tmp_path):
    """With calls in `.1`, `.2` and `.3`, only the newest is marked undone.

    Searching every chunk but stopping at the first is what keeps this from
    annotating the *oldest* record (a plain iteration of `log_files()` order)
    or all three of them. Multiple archives are the case a single-archive fix
    cannot reach.
    """
    tracker = tool_tracking.ToolTracker(log_dir=tmp_path)
    calls = []
    for tag in ("old", "middle", "new"):
        calls.append(_record(tracker, tag))
        tool_tracking.rotate(tmp_path / "tool_calls.log")
    active = tmp_path / "tool_calls.log"
    assert not active.exists()

    assert tool_tracking.annotate_last_undo(tracker=tracker, log_dir=tmp_path)
    for n, tag in ((1, "new"), (2, "middle"), (3, "old")):
        record = _records(tool_tracking.archive_path(active, n))[-1]
        assert record["args"]["tag"] == tag
        assert record["undone"] is (tag == "new")
    assert [call.undone for call in calls] == [False, False, True]


def test_an_active_record_outranks_an_archived_one(tmp_path):
    """Rotation is not an excuse to skip a call that *is* in the active log."""
    tracker = tool_tracking.ToolTracker(log_dir=tmp_path)
    old = _record(tracker, "old")
    active = tmp_path / "tool_calls.log"
    tool_tracking.rotate(active)
    new = _record(tracker, "new")

    assert tool_tracking.annotate_last_undo(tracker=tracker, log_dir=tmp_path)
    assert _records(active)[-1]["undone"] is True
    assert _records(tool_tracking.archive_path(active, 1))[-1]["undone"] is False
    assert (new.undone, old.undone) == (True, False)


def test_a_restore_keeps_its_durable_journal_with_no_log_to_annotate(
    tmp_path, monkeypatch
):
    """The log is an index; the undo ring is the record.

    With no chunk at all, annotation returns False and the pre-image is still
    consumed - a restore must never fail because there was no call log to mark.
    """
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path))
    entry = {
        "kind": "file",
        "path": str(tmp_path / "a.txt"),
        "at": 1.0,
        "content": "before",
    }
    undo_ring._append(entry)
    (tmp_path / "a.txt").write_text("after")
    assert not list(tmp_path.glob("tool_calls.log*"))

    assert tool_tracking.annotate_last_undo(log_dir=tmp_path) is False
    assert undo_ring.consume(entry) is True
    assert undo_ring.consume(entry) is False
