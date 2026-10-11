"""Screen share: the sixth dead module, now reachable by a person.

`skills/screen_to_phone.py` shipped a complete capability - ScreenCast portal
round trip, VP8 pipeline, one-viewer server - and reached **no GUI at all**. The
only way to use it was a model deciding to call it, which for a thing a person
*does* (start sharing, stop sharing, see why it cannot) is the wrong shape.

Two properties these tests pin, both of which are the difference between a panel
and a shortcut:

- **Every control goes through `tools.execute_tool`.** The consent gate
  (`screen-share-enabled`), the sandbox and the audit origin are the chat path's,
  so a panel calling the skill's helpers directly would share a screen outside
  the switches a person set.
- **The probe is slow, and the UI is honest about it.** `action=status` runs the
  launcher's `--check`, up to 90 seconds, so it runs on a worker and reports on
  an idle. A button that does nothing for ninety seconds while the window is
  frozen is the failure `quick_ask.py` documents for `asyncio.run` in a handler.
"""

from __future__ import annotations

import gi  # noqa: E402

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
import os  # noqa: E402
import sys  # noqa: E402

import pytest  # noqa: E402
from gi.repository import Adw, GLib, Gtk  # noqa: E402

Adw.init()

sys.path.insert(0, os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "usr", "lib", "shani-chronoa"))

from shani_chronoa.gui.surfaces import screen_share as surface_mod  # noqa: E402


def _texts(widget, out=None):
    if out is None:
        out = []
    if isinstance(widget, Gtk.Label):
        out.append(widget.get_label())
    child = widget.get_first_child()
    while child is not None:
        _texts(child, out)
        child = child.get_next_sibling()
    return out


def _buttons(widget, out=None):
    if out is None:
        out = []
    if isinstance(widget, Gtk.Button):
        out.append(widget)
    child = widget.get_first_child()
    while child is not None:
        _buttons(child, out)
        child = child.get_next_sibling()
    return out


class _App:
    window = None


def _build():
    page = surface_mod.build(_App())
    return page, getattr(page, "surface", None)


def _pump():
    context = GLib.MainContext.default()
    for _ in range(200):
        if not context.pending():
            break
        context.iteration(False)


@pytest.fixture
def dispatched(monkeypatch):
    """Record what the panel sends through the real dispatch path.

    `execute_tool_outcome` is stubbed so the test does not need a portal, a
    launcher or a phone - but it is stubbed **at the seam the panel calls**, so a
    panel that stopped calling it would send nothing and fail here.
    """
    calls = []

    class _Result:
        def __init__(self, text):
            self.text = text

    def fake(name, arguments, origin=None):
        calls.append((name, arguments, origin))
        return _Result(f"answer for {arguments.get('action')}")

    from shani_chronoa import tools
    monkeypatch.setattr(tools, "execute_tool_outcome", fake)
    return calls


# --- it is really reachable now ---------------------------------------------


def test_the_skill_is_a_tool_the_panel_can_drive():
    from shani_chronoa import capabilities, tools

    names = [t.get("function", {}).get("name") for t in tools.TOOLS]
    assert "screen_to_phone" in names, "the skill is not registered at all"
    assert capabilities.GATED.get("screen_to_phone") == "screen-share-enabled"


def test_it_is_registered_with_a_unique_icon():
    from shani_chronoa.gui.surfaces import SURFACE_IDS, all_surfaces

    assert "screen_share" in SURFACE_IDS
    icons = [icon for _t, icon, _b in all_surfaces().values()]
    assert icons.count(surface_mod.ICON) == 1, (
        f"{surface_mod.ICON} is shared by "
        f"{icons.count(surface_mod.ICON)} rows - a duplicate glyph")


def test_the_icon_exists_in_adwaita():
    """Drawn blank on GNOME otherwise; the shape `test_sidebar_icons` records."""
    import pathlib
    theme = pathlib.Path("/usr/share/icons/Adwaita")
    if theme.is_dir():
        assert any(theme.rglob(f"{surface_mod.ICON}.svg")) or \
            any(theme.rglob(f"{surface_mod.ICON}.png")), surface_mod.ICON


# --- the controls go through the real dispatch path -------------------------


def test_check_start_and_stop_all_go_through_execute_tool(dispatched):
    """**Pressed, not called.** `surface._run(...)` is what a first version did.

    It passed with `surface._run` renamed, which is not a test of a button - and
    this repo has recorded the dead-control defect five times. Emitting
    `clicked` measures the wiring rather than the behaviour behind it.
    """
    page, surface = _build()
    mine = [b for b in _buttons(page) if b.get_label() in ("Check", "Start", "Stop")]
    assert len(mine) == 3, [b.get_label() for b in _buttons(page)]
    for action_button in mine:
        action_button.emit("clicked")
        _pump()
    assert [call[1].get("action") for call in dispatched] == ["status", "start", "stop"], dispatched
    for name, _arguments, origin in dispatched:
        assert name == "screen_to_phone", name
        assert origin == "user", (
            f"an action with no user origin is indistinguishable from a "
            f"trigger's, got {origin!r}")


def test_the_answer_is_shown_in_full_not_truncated(dispatched):
    page, surface = _build()
    surface._run("status")
    _pump()
    shown = " ".join(t for t in _texts(page) if t)
    assert "answer for status" in shown, shown


def test_a_refusal_from_the_gate_is_shown_not_swallowed(monkeypatch):
    """Consent off: the skill's own refusal reaches the person.

    `execute_tool_outcome` is the gate, so the panel must show what it returns
    rather than treat an empty or refused answer as success.
    """
    from shani_chronoa import tools

    class _Result:
        text = ("Refusing: sharing this screen is turned off "
                "(enable 'screen-share-enabled' in Settings).")

    monkeypatch.setattr(tools, "execute_tool_outcome",
                        lambda *a, **k: _Result())
    page, surface = _build()
    surface._run("start")
    _pump()
    assert "screen-share-enabled" in " ".join(t for t in _texts(page) if t)


def test_a_broken_tool_is_a_sentence_not_a_freeze(monkeypatch):
    from shani_chronoa import tools

    def boom(*_a, **_k):
        raise RuntimeError("the launcher would not start")
    monkeypatch.setattr(tools, "execute_tool_outcome", boom)
    page, surface = _build()
    surface._run("start")
    _pump()
    shown = " ".join(t for t in _texts(page) if t)
    assert "failed" in shown and "launcher" in shown, shown


# --- slow, and the window stays alive ---------------------------------------


def test_the_buttons_come_back_after_a_slow_action(dispatched):
    """A 90-second probe must leave the panel usable."""
    import time

    def slow(*_a, **_k):
        time.sleep(0.2)
        class _R:
            text = "The screen can be shared: over Bluetooth."
        return _R()

    from shani_chronoa import tools
    from shani_chronoa.gui.surfaces import screen_share as mod
    # drive the worker the way the panel does, then let the idle fire
    page, surface = _build()
    monkey = None
    original = mod.threading.Thread
    mod.threading.Thread = lambda **kw: original(
        target=kw["target"], daemon=kw.get("daemon", True), name=kw.get("name"))
    try:
        surface._run("status")
        for _ in range(50):
            _pump()
            if surface._busy is False:
                break
    finally:
        mod.threading.Thread = original
    mine = [b for b in _buttons(page) if b.get_label() in ("Check", "Start", "Stop")]
    assert len(mine) == 3, [b.get_label() for b in _buttons(page)]
    for action_button in mine:
        assert action_button.get_sensitive(), (
            f"{action_button.get_label()} stayed disabled after the action")


def test_the_panel_does_not_freeze_the_loop_during_a_probe(monkeypatch):
    """The probe runs on a worker - the window must still turn while it runs.

    `quick_ask.py` records that `asyncio.run()` in a GTK handler blocks it, and
    the same failure here would freeze the window for up to 90 seconds.
    """
    page, surface = _build()
    called = []
    monkeypatch.setattr(surface_mod, "_pump_noop", lambda: None, raising=False)
    # A worker is what makes this true; assert one is started rather than that
    # the work happened inline.
    started = []
    real_thread = surface_mod.threading.Thread

    class _Recorder:
        def __init__(self, **kwargs):
            started.append(kwargs.get("name"))

        def start(self):
            pass

    surface_mod.threading.Thread = _Recorder
    try:
        surface._run("status")
    finally:
        surface_mod.threading.Thread = real_thread
    assert started == ["chronoa-screen-share"], started
