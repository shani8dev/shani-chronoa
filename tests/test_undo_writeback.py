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
