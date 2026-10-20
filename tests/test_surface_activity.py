"""The activity surface as a built widget.

Pinned here because the properties are the ones a reader cannot see wrong
without opening the window: the rows must come from a caller-supplied
tmp-dir `ToolTracker` (never the live singleton), the rows must be newest
first, the filter must actually narrow, and a missing log file must be an
empty state rather than an exception.

The filter is driven the way a user drives it, not by poking `set_text()`.
Verified against GTK4: `Gtk.SearchEntry.set_text()` emits neither `changed`
nor `search-changed`, and `search-changed` is debounced by
`get_search_delay()` (150 ms by default). So a test that calls `set_text()`
and immediately counts rows is asserting nothing about filtering - it is
counting the render that was already on screen, and it passes for the wrong
reason. Typing and pumping the main context past the delay is the path the
signal actually takes.
"""

from __future__ import annotations

import pathlib
import time
import types

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, GLib, Gtk  # noqa: E402

Adw.init()

from shani_chronoa import tool_tracking  # noqa: E402
from shani_chronoa.gui.surfaces import activity  # noqa: E402

THREE_RECORDS = [
    ("get_datetime", {"tz": "utc"}, "2026-10-02 08:00"),
    ("get_battery_status", {}, "87%"),
    ("screenshot", {"path": "/tmp/x.png"}, "saved"),
]


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
            walk(child)
            child = child.get_next_sibling()

    walk(widget)
    return out


def _tracker(tmp_path, records=THREE_RECORDS):
    tracker = tool_tracking.ToolTracker(log_dir=pathlib.Path(tmp_path))
    for name, args, result in records:
        tracker.record_call(name, args, result, 12.0)
    return tracker


def _pump(ms=500):
    """Run the main context for `ms` so the debounce can expire."""
    context = GLib.MainContext.default()
    deadline = time.monotonic() + ms / 1000.0
    while time.monotonic() < deadline:
        while context.pending():
            context.iteration(False)
        time.sleep(0.005)


def _filter_by(widget, text):
    """Type `text` into the filter entry and wait for the render it causes."""
    # set_text() is silent by design here - it only clears, and the insert
    # below is what emits search-changed once the search delay expires.
    widget._search.set_text("")
    widget._search.insert_text(text, -1)
    _pump()


class TestImports:
    def test_module_exports(self):
        assert activity.TITLE
        assert activity.ICON
        assert callable(activity.build)


class TestBuild:
    def test_build_returns_a_gtk_widget(self, tmp_path):
        widget = activity.build(types.SimpleNamespace())
        assert isinstance(widget, Gtk.Widget)

    def test_missing_log_file_is_an_empty_state_not_an_exception(self, tmp_path):
        tracker = tool_tracking.ToolTracker(log_dir=tmp_path / "nope")
        widget = activity.build(types.SimpleNamespace(tool_tracker=tracker))
        assert isinstance(widget, Gtk.Widget)
        assert _rows(widget) == []


class TestThePanelIsAWorkbench:
    """**The panel used to be a graveyard.**

    It read a log of everything the machine had done - 19,000 records on a
    real machine - and offered no way to do any of it again. Two things
    make it a workbench, and both are things the log already knew.
    """

    def test_the_row_says_who_started_the_call(self, tmp_path):
        """**The log has always known, and the panel never showed it.**

        A call a person made and a call an armed rule made are different
        events, and a panel that cannot tell them apart cannot answer
        "what did this machine do on its own?".
        """
        tracker = _tracker(tmp_path)
        tracker.record_call("set_volume", {"level": 40}, "set", 3.0,
                            origin=tool_tracking.ORIGIN_UNATTENDED)
        widget = activity.build(types.SimpleNamespace(tool_tracker=tracker))
        text = "\n".join(_labels(widget))
        assert "by rule" in text, (
            f"the panel does not say a rule made the call: {text!r}")
        assert "asked" in text, (
            f"the panel does not say a person made the call: {text!r}")

    def test_every_row_offers_to_run_the_call_again(self, tmp_path):
        widget = activity.build(
            types.SimpleNamespace(tool_tracker=_tracker(tmp_path)))
        rows = _rows(widget)
        assert rows, "the panel rendered no rows to put a button on"
        for row in rows:
            buttons = [b for b in _buttons(row) if b.get_label() == "Run again"]
            assert buttons, "a row offers no way to run the call again"

    def test_running_it_again_goes_through_the_real_dispatch(self, tmp_path,
                                                             monkeypatch):
        """**The same dispatch the chat uses** - so the consent gate, the
        sandbox and the verification all still apply.

        A private copy of the call would be a call with no consent and no
        verification, which is the opposite of what a workbench is for.
        """
        from shani_chronoa import tools

        seen = {}

        class _Outcome:
            text = "the answer"
            verdict = "verified"
            ran = True

        def _fake(name, arguments, by_reference=False, origin=tools.ORIGIN_USER):
            seen["name"] = name
            seen["arguments"] = arguments
            seen["origin"] = origin
            return _Outcome()

        monkeypatch.setattr(tools, "execute_tool_outcome", _fake)
        widget = activity.build(
            types.SimpleNamespace(tool_tracker=_tracker(tmp_path)))
        rows = _rows(widget)
        # **The row for the call being re-run**, by its title - the rows are
        # newest first, so the first one is not the one this test is about.
        row = next(r for r in rows
                   if str(r.get_title() or "").startswith("get_datetime"))
        button = next(b for b in _buttons(row) if b.get_label() == "Run again")
        button.emit("clicked")
        _pump(1500)
        assert seen.get("name"), (
            "pressing Run again did not run anything - the panel's button is "
            "a label with no action behind it")
        assert seen["arguments"] == {"tz": "utc"}, seen
        assert seen["origin"] == tools.ORIGIN_USER, (
            "a re-run is a person asking, so it must carry the person's "
            "origin and not the rule's")


class TestRecords:
    def test_three_records_render_three_rows(self, tmp_path):
        tracker = _tracker(tmp_path)
        widget = activity.build(types.SimpleNamespace(tool_tracker=tracker))
        assert len(_rows(widget)) == 3

    def test_rows_are_newest_first(self, tmp_path):
        tracker = _tracker(tmp_path)
        widget = activity.build(types.SimpleNamespace(tool_tracker=tracker))
        titles = [row.get_title() for row in _rows(widget)]
        # `record_call` stamps each call as it happens, so reverse insertion
        # order is newest first.
        assert [t.split(" - ")[0] for t in titles] == [
            "screenshot",
            "get_battery_status",
            "get_datetime",
        ]

    def test_filter_leaves_one_row(self, tmp_path):
        tracker = _tracker(tmp_path)
        widget = activity.build(types.SimpleNamespace(tool_tracker=tracker))
        _filter_by(widget, "get_battery_status")
        rows = _rows(widget)
        assert len(rows) == 1
        assert rows[0].get_title().startswith("get_battery_status")

    def test_filter_that_always_matches_would_break_the_narrowing(
        self, tmp_path, monkeypatch
    ):
        """The negative control for the assertion above.

        `_ActivityView._render` reads `_matches` as a module global at call
        time, so patching it here patches the name that is actually used -
        not a copy the widget holds. With the predicate forced to always-True
        the filtered count becomes 3, which is exactly why `== 1` above is a
        real assertion rather than a constant. Then restore and show the
        narrowing comes back.
        """
        tracker = _tracker(tmp_path)
        widget = activity.build(types.SimpleNamespace(tool_tracker=tracker))

        assert len(_rows(widget)) == 3, "fixture no longer renders three rows"
        _filter_by(widget, "get_battery_status")
        assert len(_rows(widget)) == 1, "control did not start from a narrowing filter"

        monkeypatch.setattr(activity, "_matches", lambda record, needle: True)
        _filter_by(widget, "get_battery_status")
        assert len(_rows(widget)) == 3, (
            "always-match filter still narrowed, so `== 1` proves nothing"
        )

        monkeypatch.undo()
        _filter_by(widget, "get_battery_status")
        assert len(_rows(widget)) == 1, "narrowing did not come back after restore"
