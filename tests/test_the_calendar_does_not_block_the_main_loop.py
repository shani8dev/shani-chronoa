"""The Calendar panel froze the whole window while it read the calendar.

`refresh()` called `eds_calendar.events_between()` - a synchronous
`SourceRegistry.new_sync` and a `connect_sync(timeout=10)` per calendar - from
`__init__`, which runs while the main window is being built. Measured in a
shani-testbed slot (2026-10-23) with faulthandler: the main thread sat in
`events_between` in 5 of 9 dumps, every AT-SPI request to the app timed out,
and the setup wizard's buttons never appeared in the accessibility tree.

The read now runs on a thread and the answer is applied on the main loop.
These tests use the real `_in_background`, captured at import - before the
repo-wide fixture swaps it for an in-line call - with a reader that does not
return until the test lets it.
"""

from __future__ import annotations

import threading
import time
import types

import pytest

gi = pytest.importorskip("gi")
gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import GLib  # noqa: E402

from shani_chronoa import eds_calendar  # noqa: E402
from shani_chronoa.gui.surfaces import calendar as surface  # noqa: E402

THREADED = surface._in_background
NOW = time.mktime((2026, 10, 2, 9, 0, 0, 0, 0, -1))


class _Config:
    def get_bool(self, key, default=False):
        return True


def _app():
    return types.SimpleNamespace(calendar_now=NOW, config=_Config())


def _event(summary):
    return eds_calendar.Event(start=NOW + 3600, end=NOW + 7200, summary=summary,
                              location="", calendar="Personal", uid=summary,
                              all_day=False)


def _pump_until(condition, timeout=5.0):
    context = GLib.MainContext.default()
    deadline = time.monotonic() + timeout
    while not condition() and time.monotonic() < deadline:
        context.iteration(False)
        time.sleep(0.01)
    return condition()


@pytest.fixture
def held_reader(monkeypatch):
    """A calendar that answers only when the test sets `release`."""
    monkeypatch.setattr(surface, "_in_background", THREADED)
    release = threading.Event()

    def _reader(start, end, timeout=10):
        release.wait(10)
        return [_event("Standup")]

    monkeypatch.setattr(eds_calendar, "events_between", _reader)
    monkeypatch.setattr(eds_calendar, "default_calendar_name", lambda: "Personal")
    return release


def test_building_the_panel_does_not_wait_for_the_calendar(held_reader):
    release = held_reader
    started = time.monotonic()
    page = surface.build(_app())
    took = time.monotonic() - started
    try:
        assert took < 1.0, f"build waited {took:.1f}s for a calendar that had not answered"
        assert page.state() == surface.STATE_READING
        assert page.row_count() == 0
    finally:
        release.set()
    assert _pump_until(lambda: page.row_count() == 1), page.state()
    assert page.state() not in (surface.STATE_READING, "")


def test_the_sidebar_dot_is_told_when_the_read_lands(held_reader):
    """The window reads the dot right after building the panel - mid-read."""
    release = held_reader
    told = []
    sidebar = types.SimpleNamespace(set_status=lambda name, status: told.append((name, status)))
    app = _app()
    app.window = types.SimpleNamespace(_sidebar_page=sidebar)
    page = surface.build(app)
    assert told == [] and page.status() == "unknown"
    release.set()
    assert _pump_until(lambda: told), "the dot was never updated after the read"
    assert told[-1] == ("calendar", page.status())


def test_the_reading_placeholder_is_replaced_not_kept(held_reader):
    release = held_reader
    page = surface.build(_app())
    release.set()
    assert _pump_until(lambda: page.row_count() == 1)
    texts = []

    def walk(widget):
        if hasattr(widget, "get_text") and not hasattr(widget, "get_buffer"):
            texts.append(widget.get_text())
        for name in ("get_title", "get_label"):
            if hasattr(widget, name):
                texts.append(getattr(widget, name)() or "")
        child = widget.get_first_child()
        while child is not None:
            walk(child)
            child = child.get_next_sibling()

    walk(page)
    joined = "\n".join(t for t in texts if t)
    assert surface.READING_TITLE not in joined
    assert "Personal" in joined, "the where-row did not arrive with the read"


def test_a_later_refresh_wins_over_an_earlier_one_still_reading(monkeypatch):
    """The first read answers *last*; its answer must not replace the second's."""
    monkeypatch.setattr(surface, "_in_background", THREADED)
    monkeypatch.setattr(eds_calendar, "default_calendar_name", lambda: "Personal")
    first_may_answer = threading.Event()
    calls = []

    def _reader(start, end, timeout=10):
        calls.append(None)
        if len(calls) == 1:
            first_may_answer.wait(10)
            return [_event("stale")]
        return [_event("fresh")]

    monkeypatch.setattr(eds_calendar, "events_between", _reader)
    page = surface.build(_app())
    page.surface.refresh()
    assert _pump_until(lambda: page.row_count() == 1)
    assert [e.summary for e in page.events()] == ["fresh"]
    first_may_answer.set()
    _pump_until(lambda: False, timeout=0.5)     # let the stale answer arrive
    assert [e.summary for e in page.events()] == ["fresh"]
