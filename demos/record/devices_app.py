"""Connect a device and the watch dashboard, against the stand-in BlueZ.

Scripted and reproducible: `tools/fake_bluez.py` serves BlueZ's own D-Bus API
on a private bus and calls back into the window's real pairing agent, so the
code comparison, the pairing and the GATT connect are the real window code
paths. No real adapter, phone or watch is touched. The watch dashboard is fed
a sample week (`render_ui.py watch` uses the same shape).
"""
import os
import sys
import threading
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "..", "usr", "lib", "shani-chronoa"))
sys.path.insert(0, os.path.join(HERE, "..", "..", "tools"))
import gi  # noqa: E402

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
gi.require_version("Graphene", "1.0")
from gi.repository import Adw, Gio, GLib, Graphene, Gtk  # noqa: E402

GO, DONE, FRAMES = sys.argv[1:4]
os.makedirs(FRAMES, exist_ok=True)

import fake_bluez  # noqa: E402

bus = Gio.TestDBus.new(Gio.TestDBusFlags.NONE)
bus.up()
fake = fake_bluez.FakeBluez(bus.get_bus_address()).start()

from shani_chronoa.gui.connect_device import ConnectDeviceWindow  # noqa: E402

app = Adw.Application(application_id="org.shani.chronoa.devicesdemo",
                      flags=Gio.ApplicationFlags.NON_UNIQUE)
S = {"rec": False, "n": 0, "win": None}


def grab(win, name):
    w, h = win.get_width(), win.get_height()
    if not (w and h):
        return
    snap = Gtk.Snapshot()
    Gtk.WidgetPaintable.new(win).snapshot(snap, w, h)
    node = snap.to_node()
    if node is not None:
        win.get_native().get_renderer().render_texture(
            node, Graphene.Rect().init(0, 0, w, h)).save_to_png(name)


def capture():
    if S["rec"] and S["win"] is not None and S["win"].get_mapped():
        grab(S["win"], os.path.join(FRAMES, f"main_{S['n']:05d}.png"))
        S["n"] += 1
    return True


def find(widget, kind, label):
    stack = [widget]
    while stack:
        w = stack.pop()
        if isinstance(w, kind):
            for getter in ("get_label", "get_title"):
                fn = getattr(w, getter, None)
                if fn is not None and fn() == label:
                    return w
        child = w.get_first_child()
        while child is not None:
            stack.append(child)
            child = child.get_next_sibling()
    return None


def on_main(fn):
    """Run `fn` on the GTK loop and wait for it."""
    done = threading.Event()
    box = {}

    def run():
        box["v"] = fn()
        done.set()
        return False
    GLib.idle_add(run)
    done.wait(10)
    return box.get("v")


def wait_for(cond, seconds=10):
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        if on_main(cond):
            return True
        time.sleep(0.2)
    return False


def page(win):
    return win.view.get_visible_page()


def press(win, kind, label, inside=None):
    def go():
        root = page(win)
        target = find(inside(root) if inside else root, kind, label)
        if target is None:
            print(f"DEMO missing {label!r}", flush=True)
            return False
        target.emit("activated" if isinstance(target, Adw.ActionRow) else "clicked")
        print(f"DEMO pressed {label!r}", flush=True)
        return True
    return on_main(go)


def row_button(win, row_title, label):
    return press(win, Gtk.Button, label,
                 inside=lambda root: find(root, Adw.ActionRow, row_title) or root)


def sample_summary():
    from datetime import datetime, timedelta
    import math
    today = datetime.now().replace(second=0, microsecond=0)
    night = today.replace(hour=23, minute=10) - timedelta(days=1)
    sleep, at = [], night
    for stage, minutes in (("light", 35), ("deep", 70), ("light", 80), ("awake", 10),
                           ("light", 60), ("deep", 45), ("light", 95), ("awake", 5)):
        sleep.append({"start": at, "end": at + timedelta(minutes=minutes),
                      "stage": stage, "minutes": minutes})
        at += timedelta(minutes=minutes)
    return {
        "battery": 64, "step_goal": 10000,
        "steps_today": {"steps": 6842, "distance_m": 4920, "calories": 231},
        "steps_yesterday": {"steps": 11204, "distance_m": 8100, "calories": 402},
        "steps_2_days_ago": {"steps": 8310, "distance_m": 5990, "calories": 296},
        "sleep": sleep,
        "sleep_summary": {"asleep_minutes": 385, "deep_minutes": 115, "light_minutes": 270,
                          "awake_minutes": 15, "from": sleep[0]["start"], "to": sleep[-1]["start"]},
        "stress_today": [(today.replace(hour=h // 2, minute=30 * (h % 2)),
                          int(42 + 18 * math.sin(h / 3) + (h * 7) % 11))
                         for h in range(0, max(4, 2 * today.hour + today.minute // 30))],
        "training": [{"type": "walk", "start": today.replace(hour=7, minute=5),
                      "active_seconds": 1860, "steps": 3420, "calories": 118, "avg_hr": 104}],
        "device": {"manufacturer": "MOYOUNG-V2", "software": "MOY-7OT4-2.0.1"},
    }


def driver():
    while not os.path.exists(GO):
        time.sleep(0.2)
    S["rec"] = True
    win = S["win"]
    time.sleep(2.5)
    press(win, Adw.ActionRow, "Bluetooth")
    wait_for(lambda: find(page(win), Adw.ActionRow, "Pixel 9") is not None)
    time.sleep(3)
    row_button(win, "Pixel 9", "Pair")
    wait_for(lambda: page(win).get_tag() == "bt-code")
    time.sleep(3.5)
    press(win, Gtk.Button, "Yes, pair")
    wait_for(lambda: page(win).get_tag() == "done")
    time.sleep(3.5)
    press(win, Gtk.Button, "Connect another")
    time.sleep(1.5)
    press(win, Adw.ActionRow, "Bluetooth")
    wait_for(lambda: find(page(win), Adw.ActionRow, "Band 8") is not None)
    time.sleep(2)
    row_button(win, "Band 8", "Connect")
    wait_for(lambda: page(win).get_tag() == "done")
    time.sleep(3.5)

    def to_watch():
        from shani_chronoa.gui.surfaces.watch_dashboard import WatchDashboard
        dash_win = Adw.ApplicationWindow(application=app, title="Band 8",
                                         default_width=900, default_height=760)
        dash = WatchDashboard("C1:00:00:00:00:05", "Band 8")
        dash.show(sample_summary())
        dash.tiles["heart_rate"][0].set_text("72")
        for side in ("top", "bottom", "start", "end"):
            getattr(dash, f"set_margin_{side}")(18)
        toolbar = Adw.ToolbarView(content=Gtk.ScrolledWindow(child=Adw.Clamp(maximum_size=900,
                                                                               child=dash)))
        toolbar.add_top_bar(Adw.HeaderBar())
        dash_win.set_content(toolbar)
        dash_win.present()
        S["win"] = dash_win
        win.close()
        return True
    on_main(to_watch)
    time.sleep(6)
    S["rec"] = False
    print(f"frames: {S['n']}", flush=True)
    fake.stop()
    open(DONE, "w").close()


def activate(_app):
    win = ConnectDeviceWindow(app, bt_address=bus.get_bus_address())
    win.set_default_size(560, 720)
    win.present()
    S["win"] = win
    GLib.timeout_add(166, capture)
    threading.Thread(target=driver, daemon=True).start()


app.connect("activate", activate)
try:
    sys.exit(app.run([sys.argv[0]]))
finally:
    bus.down()
