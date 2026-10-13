"""The Calendar panel listed events without saying where a new one would go.

`eds_calendar.default_calendar_name()` existed and had **no caller in the tree**.
Its docstring is one line: *"Display name of the calendar new events go to."*

That matters because `create_event()` writes to the **default** calendar - not to
any particular one - and raises when there is none:

    raise CalendarUnavailable("there is no default calendar to add to")

So a person could ask Chronoa to add an event, have it land in a calendar they
did not choose, or get a failure with no warning coming. The panel had nothing to
say about either.

**Three states, because they are three different problems with three different
fixes**, and merging them is how "there is no default calendar" gets reported as
"nothing to show":

| state | meaning |
|---|---|
| a name | the question is answerable without leaving Chronoa |
| `CalendarUnavailable` | asking for an event **would fail** - say so here, not on request |
| anything else | *could not read* - not the same as *no calendar* |

**Writability is deliberately not claimed.** `create_event()` checks
`client.is_readonly()` on a live connection; this row is one synchronous read of a
display name, and saying "writable" without having asked would be a claim it
cannot support.

The row is placed in `refresh()` before every early return. Placed in
`_show_events` it appeared only when there were events - and "no events" is
exactly when a person would ask for one.
"""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "usr/lib/shani-chronoa"))

gi = pytest.importorskip("gi")
gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gtk  # noqa: E402

Adw.init()

from shani_chronoa.gui.surfaces import calendar  # noqa: E402


def _walk(node, out=None):
    out = [] if out is None else out
    if isinstance(node, Gtk.Widget):
        out.append(node)
    child = node.get_first_child()
    while child is not None:
        _walk(child, out)
        child = child.get_next_sibling()
    return out


class _Config:
    def get(self, key, default=""):
        return str(default)

    def get_bool(self, key, default=False):
        return bool(default)

    def get_double(self, key, default=0.0):
        return float(default)

    def sense_allowed(self, name):
        return False


class _App:
    def __init__(self):
        self.config = _Config()
        self.window = None
        self.tts = None
        self.stt = None

    def activate_action(self, name, arg):
        pass


def _said(answer, monkeypatch):
    from shani_chronoa import eds_calendar

    if isinstance(answer, Exception):
        def boom(*a, **k):
            raise answer
        monkeypatch.setattr(eds_calendar, "default_calendar_name", boom)
    else:
        monkeypatch.setattr(eds_calendar, "default_calendar_name",
                            lambda *a, **k: answer)

    out = []
    for w in _walk(calendar.build(_App())):
        if isinstance(w, Gtk.Label) and w.get_text():
            out.append(w.get_text())
    return " ".join(out)


def test_the_default_calendar_is_named(monkeypatch):
    said = _said("Work", monkeypatch)
    assert "New events go to 'Work'" in said, said[:220]


def test_no_default_calendar_is_reported_as_a_failure_not_an_empty_one(monkeypatch):
    """**The state that would otherwise be discovered by asking.**"""
    from shani_chronoa import eds_calendar

    said = _said(
        eds_calendar.CalendarUnavailable("there is no default calendar"),
        monkeypatch)
    assert "no default calendar to add events to" in said, said[:220]
    assert "would fail" in said, (
        f"the panel does not say that asking for an event would fail: {said[:220]!r}")


def test_an_unreadable_service_is_not_reported_as_no_calendar(monkeypatch):
    """**The distinction that matters.** "Could not read" and "there is none" are
    two different problems, and merging them tells a person their calendar is
    empty when the truth is that Chronoa could not look."""
    said = _said(RuntimeError("the EDS service is not here"), monkeypatch)

    assert "could not be read" in said, said[:220]
    assert "no default calendar to add events to" not in said, (
        f"an unreadable service is being reported as a missing calendar: {said[:220]!r}")


def test_the_row_renders_when_there_are_no_events(monkeypatch):
    """**The placement.** This panel's permission is off by default, so `refresh()`
    returns before `_show_events` - and "no events" is exactly when a person
    would ask for one. The row was first placed in `_show_events` and therefore
    appeared only when there was something to list."""
    said = _said("Work", monkeypatch)
    assert "New events go to 'Work'" in said, (
        "the row did not render on the no-events path")


def test_writability_is_not_claimed(monkeypatch):
    """`create_event()` checks `client.is_readonly()` on a live connection. This
    row is one synchronous read of a display name, so claiming writability would
    be a claim it cannot support.

    **Scoped to this row, not the page.** The first version asserted over every
    string on the panel and failed on a row that has nothing to do with it -
    "Read-only. The 'calendar' trigger is a separate permission", which is about
    the trigger and is correct. A page-wide substring test measures the page.
    """
    said = _said("Work", monkeypatch)
    row = next((s for s in said.split("  ") if "New events go to" in s), "")
    assert row, "the default-calendar row was not found"

    # the row and whatever follows it, up to the next sentence about calendars
    around = said[said.index("New events go to"):][:200]
    for word in ("writable", "read-only", "read only", "readonly"):
        assert word not in around.lower(), (
            f"the row claims {word!r}, which it never asked about: {around!r}")
