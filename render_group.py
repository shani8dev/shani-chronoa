#!/usr/bin/env python3
"""Render one sense group of the settings window to a PNG, using the window's
own search filter to isolate it.

Same four requirements as render_ui.py, for the same reasons (see that file's
docstring and AGENTS.md): Adw.init() at module scope before the application,
present() in the activate handler with the snapshot a turn later, save_to_png
not save_to_png_bytes, and a lowercased application id.

Usage: render_group.py <needle> <out.png> <width> <height>
"""
import os
import sys

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
gi.require_version("Gsk", "4.0")
from gi.repository import Adw, GLib, Gdk, Graphene, Gsk, Gtk  # noqa: E402

Adw.init()

NEEDLE, OUT = sys.argv[1], sys.argv[2]
WIDTH, HEIGHT = int(sys.argv[3]), int(sys.argv[4])

sys.path.insert(
    0, os.path.join(
        os.path.dirname(os.path.abspath(__file__)),
        "usr", "lib", "shani-chronoa",
    )
)

from shani_chronoa.config import ChronoaConfig  # noqa: E402


class App(Gtk.Application):
    def __init__(self):
        super().__init__(application_id="test.chronoa.rendergroup")
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
    texture = renderer.render_texture(
        node, Graphene.Rect().init(0, 0, width, height))
    if texture is None:
        print("NO TEXTURE", flush=True)
        return False
    texture.save_to_png(path)
    print("WROTE", path, flush=True)
    return False


def on_activate(app):
    from shani_chronoa.settings_window import SettingsWindow
    window = SettingsWindow(app)
    window.set_default_size(WIDTH, HEIGHT)
    window.present()

    def filter_then_render():
        # The window's own filter, driven the way a person drives it.
        window._search.insert_text(NEEDLE, 0)

        def capture():
            groups = []

            def walk(node):
                child = node.get_first_child()
                while child:
                    walk(child)
                    groups.append(child)
                    child = child.get_next_sibling()

            walk(window.get_child())
            for g in groups:
                if isinstance(g, Adw.PreferencesGroup) and g.get_visible():
                    print("VISIBLE GROUP:", g.get_title(), flush=True)
            _render(window, WIDTH, HEIGHT, OUT)
            app.quit()
            return False

        GLib.timeout_add(600, capture)
        return False

    GLib.timeout_add(1200, filter_then_render)
    GLib.timeout_add(30000, lambda: (app.quit(), False)[1])
    return None


app = App()
app.connect("activate", on_activate)
app.run([])
