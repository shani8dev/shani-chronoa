"""Activity rows: the two columns and the per-row Undo.

The panel used to show a digest of the arguments and no way to put a write
back. The diff is the arguments asked for beside what came back - the log holds
one call, not two versions of it, so a "before/after arguments" framing would
invent a second set. Undo is offered only on rows that name a file a writer
actually wrote, and goes through the skill's dispatch so its gate applies.
"""

from __future__ import annotations

import types

import pytest

import gi  # noqa: E402

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Gtk  # noqa: E402

from shani_chronoa import tool_tracking  # noqa: E402
from shani_chronoa.gui.surfaces import activity  # noqa: E402


class _App:
    def __init__(self, path):
        t = tool_tracking.ToolTracker(path)
        self.tool_tracker = t


def _rows(widget):
    out = []

    def walk(node):
        child = node.get_first_child()
        while child is not None:
            if "tool-call-row" in child.get_css_classes():
                out.append(child)
            walk(child)
            child = child.get_next_sibling()

    walk(widget)
    return out


def _labels(widget):
    out = []

    def walk(node):
        if isinstance(node, Gtk.Label) and node.get_label():
            out.append(node.get_label())
        child = node.get_first_child()
        while child is not None:
            walk(child)
            child = child.get_next_sibling()

    walk(widget)
    return out


def _buttons(widget):
    out = []

    def walk(node):
        if isinstance(node, Gtk.Button):
            out.append(node)
        child = node.get_first_child()
        while child is not None:
            following = child.get_next_sibling()
            walk(child)
            child = following

    walk(widget)
    return out


def test_arg_lines_are_the_logs_own_arguments():
    record = {"args": {"path": "~/a.txt", "text": "one & two"}}
    lines = dict(activity._arg_lines(record))
    assert lines["path"] == "~/a.txt"
    assert lines["text"] == "one & two"


def test_a_writer_row_has_undo_but_a_read_row_does_not(tmp_path):
    app = _App(tmp_path)
    app.tool_tracker.record_call(
        "write_text_file", {"path": str(tmp_path / "a.txt"), "text": "x"}, "wrote", 1.0
    )
    app.tool_tracker.record_call("get_datetime", {}, "12:00", 1.0)
    widget = activity.build(app)
    rows = _rows(widget)
    assert len(rows) == 2
    writer = next(r for r in rows if "write_text_file" in " ".join(_labels(r)))
    reader = next(r for r in rows if "get_datetime" in " ".join(_labels(r)))
    writer_tips = [b.get_tooltip_text() or "" for b in _buttons(writer)]
    reader_tips = [b.get_tooltip_text() or "" for b in _buttons(reader)]
    assert any("Put back the file" in t for t in writer_tips)
    assert not any("Put back the file" in t for t in reader_tips)


def test_a_row_that_wrote_no_named_file_has_no_undo(tmp_path):
    app = _App(tmp_path)
    app.tool_tracker.record_call("write_text_file", {"text": "x"}, "wrote", 1.0)
    widget = activity.build(app)
    row = _rows(widget)[0]
    assert not any("Put back" in (b.get_tooltip_text() or "") for b in _buttons(row))


def test_undo_goes_through_the_skill_dispatch(tmp_path, monkeypatch):
    called = []

    class Outcome:
        text = "put back"
        verdict = "VERIFIED"

    monkeypatch.setattr(
        "shani_chronoa.tools.execute_tool_outcome",
        lambda name, args, **kw: called.append((name, args, kw)) or Outcome(),
    )
    record = {"tool_name": "edit_file", "args": {"path": str(tmp_path / "a.txt")}}
    widget = Gtk.Label(label="row")
    activity._undo(record, widget)
    # The worker runs on a thread; wait for it to reach the seam.
    import time

    for _ in range(100):
        if called:
            break
        time.sleep(0.01)
    assert called, "Undo never reached the skill dispatch"
    assert called[0][0] == "undo_last_change"
    assert called[0][1] == {"path": str(tmp_path / "a.txt")}


def test_the_diff_column_headings_say_which_side_is_which():
    widget = activity._diff_widget(
        {"args": {"path": "~/a.txt"}, "verdict": "FAILED", "result": "no"}
    )
    labels = _labels(widget)
    assert "Arguments asked for" in labels
    assert "What came back" in labels
    assert any("path:" in t for t in labels)
