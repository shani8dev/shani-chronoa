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

Usage: render_ui.py <settings|main> <out.png> <width> <height> [theme]
"""

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


def on_activate(app):
    if THEME:
        Gtk.Settings.get_default().set_property("gtk-theme-name", THEME)
    if WHICH == "settings":
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
    GLib.timeout_add(1500, lambda: _render(window, WIDTH, HEIGHT, OUT))
    # The help window is modal, so app.quit() alone leaves it up and run() hangs.
    GLib.timeout_add(2500 if WHICH == "help" else 2000,
                     lambda: (app.quit(), False)[1])
    return None


app = App()
app.connect("activate", on_activate)
GLib.timeout_add(60000, lambda: (app.quit(), False)[1])
app.run([])
