#!/usr/bin/env python3
"""Render ONE Chronoa surface page to a PNG, offscreen. One surface per
process: Gsk.CairoRenderer asserts on dispose if a second one is torn down in
the same process, and render_ui.py's own lesson is that this has to be a script
that owns the process, not something imported into a test.

Usage: _render_one.py <surface-name|chat> <out.png> [width] [height] [theme]
"""
import os
import sys

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
gi.require_version("Gsk", "4.0")
from gi.repository import Adw, GLib, Graphene, Gsk, Gtk  # noqa: E402

Adw.init()

WHICH = sys.argv[1]
OUT = sys.argv[2]
WIDTH = int(sys.argv[3]) if len(sys.argv) > 3 else 1100
HEIGHT = int(sys.argv[4]) if len(sys.argv) > 4 else 820
THEME = sys.argv[5] if len(sys.argv) > 5 else ""

sys.path.insert(
    0, "/home/shrinivaskumbhar/Documents/shani/shani-chronoa/usr/lib/shani-chronoa")

from shani_chronoa.config import ChronoaConfig  # noqa: E402


class App(Gtk.Application):
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
    print("WROTE", path, flush=True)
    texture = None
    renderer = None
    return False


def on_activate(app):
    if THEME:
        Gtk.Settings.get_default().set_property("gtk-theme-name", THEME)

    from shani_chronoa.gui import ChronoaWindow
    window = ChronoaWindow(app)
    window.set_default_size(WIDTH, HEIGHT)
    window.present()

    if WHICH != "chat":
        def go():
            try:
                window._show_surface(WHICH)
                print("SURFACE", WHICH, flush=True)
            except Exception as exc:
                import traceback
                traceback.print_exc()
                print("RAISED", repr(exc), flush=True)
                app.quit()
                return False
            return False
        GLib.timeout_add(900, go)
    else:
        GLib.timeout_add(500, lambda: (
            window.add_user_turn("how much disk space is left?"),
            window.set_response(
                "Root has **38G** free of 120G.\n"
                "- `/` is 32% full\n"
                "- `/data` holds your documents"),
            False)[2])

    GLib.timeout_add(2200, lambda: _render(window, WIDTH, HEIGHT, OUT))
    GLib.timeout_add(3000, lambda: (app.quit(), False)[1])
    return None


app = App()
app.connect("activate", on_activate)
GLib.timeout_add(90000, lambda: (app.quit(), False)[1])
app.run([])