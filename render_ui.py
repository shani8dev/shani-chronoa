#!/usr/bin/env python3
"""Render one of Chronoa's own windows to a PNG, offscreen.

Run as a script, not imported into a test, and that is deliberate. Every version
of this that lived inside a pytest process returned an empty PNG while the same
code worked here - the failure is a GTK main-loop ordering problem that only
disappears when the script owns the process. So the test runs this and measures
what comes out, rather than reimplementing the sequence and losing the battle
again.

Requirements, all four of which fail by producing an empty PNG rather than an
error. See `AGENTS.md` for the full account:

- `Adw.init()` at module scope, before the `Gtk.Application` is constructed.
  Adw widgets built before it render nothing.
- `present()` in the activate handler, and the snapshot in a later turn of the
  loop. A tree built inside `activate` is unallocated, and calling `present()`
  again from inside the capture re-maps the window.
- `save_to_png(path)`, not `save_to_png_bytes()`, on a Cairo-renderer texture.
- A lowercased application id; a D-Bus name may not contain `Adwaita`.
- **This window cannot be scrolled offscreen.** Measured on libadwaita 1.5 with
  no window manager: the preferences page is ~9300px tall, the window is clamped
  to the size asked for, `Gtk.ScrolledWindow` then reports an adjustment range
  equal to its own height, and `set_value()` on it silently succeeds and moves
  nothing. Asking for a 9450px window instead is not a way round it - the capture
  comes back with the glyphs horizontally squeezed, i.e. scaled rather than
  rendered. Photographing the panel widget on its own is not a way round it
  either: a detached snapshot leaves the page background unpainted (measured: page
  alpha 0, group description alpha 140) and does not inherit the window's
  color-scheme, so the header comes out invisible and the rows come out light.
  What does work is the window's own search - typing "Tool activity" hides every
  other group, the page becomes short, and the panel is in frame in a fully
  painted window. That is a real user action rather than a trick, and it is what
  `tool-activity` does.

Usage: render_ui.py <settings|main|tool-activity|empty> <out.png> <width> <height> [theme]
"""

import json
import os
import sys

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
gi.require_version("Gsk", "4.0")
from gi.repository import Adw, GLib, Gdk, Graphene, Gsk, Gtk  # noqa: E402

# Before the application exists, not inside `activate`.
Adw.init()

WHICH, OUT = sys.argv[1], sys.argv[2]
WIDTH, HEIGHT = int(sys.argv[3]), int(sys.argv[4])
THEME = sys.argv[5] if len(sys.argv) > 5 else ""

sys.path.insert(
    0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "usr", "lib", "shani-chronoa")
)

from shani_chronoa.config import ChronoaConfig  # noqa: E402


def _walk(node, out):
    out.append(node)
    child = node.get_first_child()
    while child is not None:
        _walk(child, out)
        child = child.get_next_sibling()
    return out


def _seed_tool_activity():
    """One call per verdict, plus one the log holds and the ring cannot know about.

    The point of the render is the four renderings side by side, so the fixture
    has to contain all four - a panel photographed with only `verified` rows
    says nothing about the three states that are easy to get wrong.
    """
    from shani_chronoa import tool_tracking, tools
    tools._TRACKER.record_call("set_brightness", {"level": 40}, "Set to 40%", 12.0,
                               verdict="verified", evidence="the backlight reads 40%")
    tools._TRACKER.record_call("delete_file", {"path": "/tmp/opencode/junk.txt"},
                               "All done successfully.", 8.0, verdict="failed",
                               evidence="the path is still there and is not in the trash")
    tools._TRACKER.record_call("get_datetime", {}, "2026-10-01T16:40:00", 3.0,
                               verdict="unverified")
    tools._TRACKER.record_call("notify", {"summary": "lunch is ready"}, "", 1.0)
    tools._TRACKER.record_call("move_pointer", {"x": 10, "y": 20}, "moved", 4.0,
                               origin="unattended", verdict="failed",
                               evidence="the pointer did not move")
    with tool_tracking.LOG_FILE.open("a") as fh:
        fh.write(json.dumps({
            "timestamp": "2026-09-30T08:00:00+00:00",
            "tool_name": "get_battery_status", "args": {}, "result": "82%",
            "duration_ms": 210.5, "origin": "user", "verdict": "unverified",
            "evidence": "",
        }) + "\n")


class App(Gtk.Application):
    """A real Gtk.Application: `application=` is a typed property, so a duck
    type is rejected outright."""

    def __init__(self):
        super().__init__(application_id="test.chronoa.render")
        self.config = ChronoaConfig()
        self.window = None
        self._wake_word_active = False

    def activate_action(self, name, arg):
        pass


def _render(widget, width, height, path):
    paintable = Gtk.WidgetPaintable.new(widget)
    snapshot = Gtk.Snapshot()
    paintable.snapshot(snapshot, width, height)
    node = snapshot.to_node()
    if node is None:
        print("NO NODE", flush=True)
        return False
    renderer = Gsk.CairoRenderer.new()
    renderer.realize(None)
    texture = renderer.render_texture(node, Graphene.Rect().init(0, 0, width, height))
    if texture is None:
        print("NO TEXTURE", flush=True)
        return False
    texture.save_to_png(path)
    print("WROTE", flush=True)
    return False


def _frame_panel(window, css_class, needle):
    """Hide every group but the one named `css_class`, by using the real search.

    The alternative - scrolling the window, or rendering the panel detached - is
    documented in this module's docstring and both produce a picture that is not
    what a user sees. This is the filter the window ships, driven with a typed
    needle, so every remaining pixel is the real window rendering itself.
    """
    group = next((n for n in _walk(window.get_child(), [])
                  if css_class in n.get_css_classes()), None)
    if group is None:
        print(f"NO PANEL {css_class}", flush=True)
        return False
    window._search.insert_text(needle, 0)
    print(f"FRAMED {css_class} with needle {needle!r}", flush=True)
    return False


def _report_framing(window):
    """How many groups the needle actually hid - a turn later.

    `set_visible()` does not take effect within the turn that asks for it, so
    reading it in the same turn reported "0 of 23 hidden" over a panel that was
    plainly the only thing on screen. A diagnostic that cannot be right is worse
    than none, which is this repo's own rule about display probes.
    """
    hidden = sum(1 for g, _t in window._searchable if not g.get_visible())
    print(f"HIDDEN {hidden} of {len(window._searchable)} groups", flush=True)
    return False


def on_activate(app):
    if THEME:
        Gtk.Settings.get_default().set_property("gtk-theme-name", THEME)
    if WHICH == "tool-activity":
        from shani_chronoa.settings_window import SettingsWindow
        _seed_tool_activity()
        window = SettingsWindow(app)
    elif WHICH == "settings":
        from shani_chronoa.settings_window import SettingsWindow
        window = SettingsWindow(app)
    elif WHICH == "help":
        from shani_chronoa.gui import CajitaWindow
        parent = CajitaWindow(app)
        window = parent.open_help()
    else:
        from shani_chronoa.gui import CajitaWindow
        window = CajitaWindow(app)
        if WHICH == "empty":
            pass
        else:
            window.add_user_turn("how much disk space is left?")
            window.set_response("Root has **38G** free of 120G.\n- `/` is 32% full")
    window.set_default_size(WIDTH, HEIGHT)
    # Presented here; the map needs a turn of the loop before the capture.
    window.present()
    if WHICH == "tool-activity":
        # After present(): a needle typed before the map lands in a tree that is
        # not laid out yet.
        GLib.timeout_add(900, lambda: _frame_panel(
            window, "tool-activity-group", "Tool activity"))
        GLib.timeout_add(1500, lambda: _report_framing(window))
        GLib.timeout_add(1800, lambda: _render(window, WIDTH, HEIGHT, OUT))
    else:
        GLib.timeout_add(1500, lambda: _render(window, WIDTH, HEIGHT, OUT))
    # The help window is modal, so app.quit() alone leaves it up and run() hangs.
    GLib.timeout_add(2500 if WHICH == "help" else 3200,
                     lambda: (app.quit(), False)[1])
    return None


app = App()
app.connect("activate", on_activate)
GLib.timeout_add(60000, lambda: (app.quit(), False)[1])
app.run([])
