"""The right rail: it shows what it says, and nothing it cannot back.

Every section is asserted against a store written through the *real* writer -
`timer.set_timer`, `undo_last_change.record_preimage`, a `todos.json` written
the way the skill writes it - because a rail fed by hand-built JSON proves the
renderer and not the wiring, and the wiring is where this can be wrong.
"""

from __future__ import annotations

import json
import sys
import time
from pathlib import Path

import pytest

_REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_REPO / "usr" / "lib" / "shani-chronoa"))


def _labels(widget) -> "list[str]":
    out: "list[str]" = []
    if hasattr(widget, "get_label") and widget.get_label():
        out.append(widget.get_label())
    child = widget.get_first_child()
    while child is not None:
        out.extend(_labels(child))
        child = child.get_next_sibling()
    return out


def _buttons(widget) -> "list[Gtk.Button]":  # noqa: F821 - gi import is in the test body
    out = []
    if isinstance(widget, Gtk.Button):  # noqa: F821
        out.append(widget)
    child = widget.get_first_child()
    while child is not None:
        out.extend(_buttons(child))
        child = child.get_next_sibling()
    return out


def _rail(tmp_path, monkeypatch, **kwargs):
    import gi
    gi.require_version("Gtk", "4.0")
    global Gtk
    from gi.repository import Gtk as _Gtk
    Gtk = _Gtk
    from shani_chronoa.gui import rail as rail_module
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state"))
    widget = rail_module.NowRail(**kwargs)
    return widget


class TestTheRailShowsRealState:
    def test_every_section_is_present_and_says_something_when_empty(self, tmp_path,
                                                                   monkeypatch):
        widget = _rail(tmp_path, monkeypatch, get_state=lambda: "Ready",
                       get_tool=lambda: "")
        text = "\n".join(_labels(widget))
        for heading in ("Now", "Timers", "Changed", "Tasks", "Posture"):
            assert heading in text, f"the rail has no {heading} section: {text!r}"
        assert "Ready" in text
        # Empty states are sentences, not blanks: a section with nothing must
        # still answer, or the rail looks broken rather than quiet.
        assert "No timers pending" in text

    def test_a_real_timer_is_shown_with_the_time_left(self, tmp_path, monkeypatch):
        monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state"))
        from shani_chronoa.skills import timer
        monkeypatch.setattr(timer, "_schedule", lambda *a, **k: (True, ""))
        monkeypatch.setattr(timer, "_systemd_available", lambda: True)
        assert "kettle" in timer.set_timer(95, "kettle")
        widget = _rail(tmp_path, monkeypatch)
        text = "\n".join(_labels(widget))
        assert "kettle" in text
        assert "1m 3" in text or "1m 35" in text, text

    def test_a_real_edit_is_listed_as_changed_and_opens_the_diff_panel(
            self, tmp_path, monkeypatch):
        monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state"))
        target = tmp_path / "notes.md"
        target.write_text("alpha\n", encoding="utf-8")
        from shani_chronoa.skills import undo_last_change
        undo_last_change.record_preimage(target, b"alpha\n")
        target.write_text("alpha\nbeta\n", encoding="utf-8")

        opened: "list[str]" = []
        widget = _rail(tmp_path, monkeypatch, on_open=opened.append)
        widget.refresh()
        text = "\n".join(_labels(widget))
        assert "notes.md" in text
        assert "line(s) added" in text

        clicked = [b for b in _buttons(widget) if "diff" in (b.get_tooltip_text() or "")]
        assert clicked, "the changed row cannot be opened, so the rail only reports"
        clicked[0].emit("clicked")
        assert opened == ["diff"]

    def test_tasks_appear_only_when_the_consent_key_is_on(self, tmp_path,
                                                         monkeypatch):
        store = tmp_path / "state" / "shani-chronoa" / "todos.json"
        store.parent.mkdir(parents=True, exist_ok=True)
        store.write_text(json.dumps([
            {"id": 1, "content": "renew the domain", "status": "in_progress"},
            {"id": 2, "content": "water the plants", "status": "pending"},
            {"id": 3, "content": "already done", "status": "completed"},
        ]), encoding="utf-8")
        from shani_chronoa import config as config_mod

        real = config_mod.ChronoaConfig.get_bool
        monkeypatch.setattr(config_mod.ChronoaConfig, "get_bool",
                            lambda self, key, default=False:
                            True if key == "todo-list-enabled" else real(self, key, default))
        widget = _rail(tmp_path, monkeypatch)
        text = "\n".join(_labels(widget))
        assert "renew the domain" in text and "water the plants" in text
        assert "already done" not in text, "a completed task is not outstanding"

        # ...and with the key off the store is not read into the window at all.
        monkeypatch.setattr(config_mod.ChronoaConfig, "get_bool",
                            lambda self, key, default=False:
                            False if key == "todo-list-enabled" else real(self, key, default))
        widget = _rail(tmp_path, monkeypatch)
        text = "\n".join(_labels(widget))
        assert "renew the domain" not in text
        assert "off" in text

    def test_a_broken_store_degrades_to_a_sentence_not_a_crash(self, tmp_path,
                                                               monkeypatch):
        store = tmp_path / "state" / "shani-chronoa" / "todos.json"
        store.parent.mkdir(parents=True, exist_ok=True)
        store.write_text("{not json", encoding="utf-8")
        from shani_chronoa import config as config_mod
        real = config_mod.ChronoaConfig.get_bool
        monkeypatch.setattr(config_mod.ChronoaConfig, "get_bool",
                            lambda self, key, default=False:
                            True if key == "todo-list-enabled" else real(self, key, default))
        widget = _rail(tmp_path, monkeypatch)      # must not raise
        assert "could not be read" in "\n".join(_labels(widget)).lower()


class TestTheRailIsWiredIntoTheWindow:
    def test_the_window_carries_a_rail_a_toggle_and_a_key(self):
        """The window has to *have* the rail: built, visible, and reachable
        from the keyboard as well as the header button."""
        import gi
        gi.require_version("Gtk", "4.0")
        gi.require_version("Adw", "1")
        from gi.repository import Adw
        from shani_chronoa.config import ChronoaConfig
        from shani_chronoa.gui.window import ChronoaWindow

        app = Adw.Application(application_id="dev.shani.ChronoaRailTest")
        window = ChronoaWindow(app, ChronoaConfig())
        try:
            assert window._rail is not None
            assert window._rail.get_visible() is True
            assert window._rail_toggle is not None
            assert window._rail_breakpoint is not None
            window.toggle_rail()
            assert window._rail.get_visible() is False
            window.toggle_rail()
            assert window._rail.get_visible() is True
        finally:
            if getattr(window, "_rail_ticket", None):
                from gi.repository import GLib
                GLib.source_remove(window._rail_ticket)

    def test_the_state_word_the_rail_shows_is_the_one_on_screen(self):
        """One source: the rail reads the state label rather than tracking
        state separately, so the two cannot disagree."""
        import gi
        gi.require_version("Gtk", "4.0")
        gi.require_version("Adw", "1")
        from gi.repository import Adw
        from shani_chronoa.config import ChronoaConfig
        from shani_chronoa.gui.window import ChronoaWindow
        from shani_chronoa.gui.widgets import AssistantState

        app = Adw.Application(application_id="dev.shani.ChronoaRailStateTest")
        window = ChronoaWindow(app, ChronoaConfig())
        try:
            window.set_state(AssistantState.THINKING)
            assert window._rail_state() == window._state_label.get_label()
            # The label carries the state's own ellipsis ("Thinking…"), so the
            # assertion is on the word rather than on an exact spelling the
            # window is free to choose.
            assert window._rail_state().startswith("Thinking")
        finally:
            if getattr(window, "_rail_ticket", None):
                from gi.repository import GLib
                GLib.source_remove(window._rail_ticket)


class TestTheWindowIsUsableWhenItIsNarrow:
    """The narrow layout, measured rather than assumed.

    Three separate things had to be true for a 640px window to work, and each
    was found by rendering it: the organ strip must be able to wrap, the sidebar
    must collapse into a drawer *with the conversation on top of it*, and the
    rail must hide. Any one of them alone leaves a clipped window.
    """

    def _window(self, app_id, width):
        import gi
        gi.require_version("Gtk", "4.0")
        gi.require_version("Adw", "1")
        from gi.repository import Adw, GLib
        from shani_chronoa.config import ChronoaConfig
        from shani_chronoa.gui.window import ChronoaWindow
        app = Adw.Application(application_id=app_id)
        window = ChronoaWindow(app, ChronoaConfig())
        window.set_default_size(width, 780)
        window.present()

        def finish():
            window._apply_narrow_layout()
            GLib.source_remove(window._rail_ticket)
            app.quit()
            return False

        GLib.timeout_add_seconds(2, finish)
        app.run([])
        return window

    def test_at_640_the_sidebar_is_a_drawer_and_the_rail_is_gone(self):
        window = self._window("dev.shani.ChronoaNarrow", 640)
        try:
            assert window._split.get_collapsed() is True, (
                "the panels stayed a column beside a conversation that cannot "
                "fit next to them")
            # Collapsed *and* the content on top: collapsed alone leaves the
            # panel list covering the whole window.
            assert window._split.get_show_content() is True, (
                "the sidebar collapsed but is drawn over the conversation")
            assert window._rail.get_visible() is False
        finally:
            pass

    def test_at_1280_both_columns_are_back(self):
        window = self._window("dev.shani.ChronoaWide", 1280)
        assert window._split.get_collapsed() is False
        assert window._rail.get_visible() is True

    def test_the_organ_strip_can_wrap_instead_of_demanding_a_row(self):
        """It asked for 470px on its own, which is what made the split view
        need 762px and the narrow layout impossible."""
        import gi
        gi.require_version("Gtk", "4.0")
        gi.require_version("Adw", "1")
        from gi.repository import Gtk
        from shani_chronoa.gui.organs import OrganStrip
        strip = OrganStrip()
        width = strip.measure(Gtk.Orientation.HORIZONTAL, -1)[0]
        assert width < 200, (
            f"the organ strip asks for {width}px of a single row, so it is "
            f"what stops the conversation from fitting a narrow window")


def test_the_organ_lights_are_separated_by_a_gap():
    """The companion to the width test, and it exists because that one passed
    while the strip was unreadable.

    Zeroing the `flowboxchild` padding recovered the width the previous test
    measures - and left nothing between the cells, so the eight labels rendered
    as "listening looking speaking network sensing remembering acting thinking":
    one run-on string, not eight named lights. `set_column_spacing` is the
    property that says so, and this asserts the number the render depends on
    rather than the CSS that produced it - a stylesheet assertion would pass
    against a rule the parser had dropped, which is the dead-declaration class
    this repo keeps meeting.
    """
    from shani_chronoa.gui.organs import OrganStrip

    strip = OrganStrip()
    assert strip.get_column_spacing() >= 4, (
        f"column spacing is {strip.get_column_spacing()}px, so the labels "
        f"touch - the strip's width test passes on a row nobody can read")
    assert strip.get_column_spacing() < 24, (
        f"column spacing is {strip.get_column_spacing()}px, which is spacing "
        f"for its own sake rather than a gap")


def test_human_seconds_reads_as_a_countdown():
    from shani_chronoa.gui.rail import _human_seconds
    assert _human_seconds(45) == "45s"
    assert _human_seconds(192) == "3m 12s"
    assert _human_seconds(3600 + 240) == "1h 04m"
    assert _human_seconds(-5) == "0s"
