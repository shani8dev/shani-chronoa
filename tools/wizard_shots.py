"""Screenshot every setup-wizard page, headless. Driven by tools/wizard_shots.sh.

Env: OUTDIR (where PNGs go), SIZE (WxH), PAGES (comma ids; default all).
Reuses the same ChronoaApplication so the pages are the real ones.
"""
import os, sys
repo = sys.argv[1] if len(sys.argv) > 1 else os.getcwd()
sys.path.insert(0, os.path.join(repo, "usr", "lib", "shani-chronoa"))
from gi.repository import GLib, Graphene, Gtk  # noqa: E402
from shani_chronoa.app import application as appmod  # noqa: E402
from shani_chronoa import setup_wizard  # noqa: E402

OUT = os.environ.get("OUTDIR", "/tmp/clpt/wiz")
os.makedirs(OUT, exist_ok=True)
W, H = (int(v) for v in os.environ.get("SIZE", "460x640").lower().split("x"))
PAGES = [p for p in os.environ.get("PAGES", "").split(",") if p]


def _shoot(win, name):
    win.set_default_size(W, H)
    width, height = win.get_width() or W, win.get_height() or H
    snap = Gtk.Snapshot()
    Gtk.WidgetPaintable.new(win).snapshot(snap, width, height)
    node = snap.to_node()
    if node is None:
        print("NO NODE", name, flush=True); return
    tex = win.get_renderer().render_texture(node, Graphene.Rect().init(0, 0, width, height))
    path = os.path.join(OUT, f"{name}.png")
    tex.save_to_png(path)
    print("SHOT", name, path, width, height, flush=True)


def _run(app):
    win = setup_wizard.build_window(app, config=app.config, on_finished=lambda: None)
    win.set_application(app)
    win.set_default_size(W, H)
    win.present()
    pages = PAGES or list(setup_wizard.page_registry.page_ids("setup")) if hasattr(setup_wizard, "page_registry") else PAGES
    if not pages:
        pages = [p[1] for p in setup_wizard._SETUP_PAGES]
    idx = {"i": 0}

    def step():
        if idx["i"] >= len(pages):
            app.quit(); return False
        pid = pages[idx["i"]]; idx["i"] += 1
        try:
            win.show_page(pid)
        except Exception as e:
            print("SHOW-FAIL", pid, e, flush=True)
        GLib.timeout_add(900, lambda: (_shoot(win, pid), step(), False)[2])
        return False
    GLib.timeout_add(1200, step)


_init = appmod.ChronoaApplication.__init__
def _patched(self, *a, **k):
    _init(self, *a, **k)
    self.connect("activate", lambda app: GLib.timeout_add(500, lambda: (_run(app), False)[1]))
appmod.ChronoaApplication.__init__ = _patched
sys.argv = [sys.argv[0]]
appmod.main()
