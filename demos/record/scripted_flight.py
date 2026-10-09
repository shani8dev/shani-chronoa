"""Flight search and booking on blazedemo.com, driven through Chronoa's browse skill.

The window records itself: it is rendered to a PNG several times a second and
the frames are joined by ffmpeg afterwards, so the video is exactly this window.
"""
import sys, threading, json, time, os, re
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "usr", "lib", "shani-chronoa"))
import gi
gi.require_version("Gtk", "4.0"); gi.require_version("Adw", "1"); gi.require_version("Graphene", "1.0")
from gi.repository import Gtk, GLib, Gio, Adw, Graphene
from shani_chronoa.config import ChronoaConfig
ChronoaConfig().set("web-sense-enabled", "true"); ChronoaConfig().set("privacy-mode", "false")
from shani_chronoa import browser_bridge, tools
from shani_chronoa.gui.browser import BrowserWindow, is_available

GO, DONE, FRAMES = sys.argv[1], sys.argv[2], sys.argv[3]
FPS = 6
os.makedirs(FRAMES, exist_ok=True)
app = Adw.Application(application_id="dev.shani.ChronoaBrowseDemo", flags=Gio.ApplicationFlags.NON_UNIQUE)
state = {"recording": False, "n": 0, "slow": 0}

def provider():
    if "w" not in state:
        state["w"] = BrowserWindow(application=app, home_url="about:blank")
        state["w"].set_default_size(1240, 740); state["w"].present()
    return state["w"]
browser_bridge.set_provider(provider, available=is_available)

def capture():
    if not state["recording"]:
        return True
    w = state["w"]; width, height = w.get_width(), w.get_height()
    if width and height:
        t = time.monotonic()
        snap = Gtk.Snapshot()
        Gtk.WidgetPaintable.new(w).snapshot(snap, width, height)
        node = snap.to_node()
        if node is not None:
            tex = w.get_native().get_renderer().render_texture(
                node, Graphene.Rect().init(0, 0, width, height))
            tex.save_to_png(os.path.join(FRAMES, f"main_{state['n']:05d}.png")); state["n"] += 1
        if time.monotonic() - t > 1.0 / FPS:
            state["slow"] += 1
    return True

def browse(action, **args):
    out = tools.execute_tool("browse", dict(args, action=action, timeout=30))
    print(f"{action} {json.dumps(args)}\n    -> {out[:500]!r}", flush=True)
    time.sleep(1.6)
    return out

def run_steps():
    browse("navigate", url="https://blazedemo.com/")
    browse("select", selector="select[name=fromPort]", label="Boston")
    browse("select", selector="select[name=toPort]", label="London")
    browse("click", selector="input[type=submit][value='Find Flights']", settle=2.0)
    listing = browse("get_text")
    prices = [float(p) for p in re.findall(r"\$(\d+\.\d{2})", listing)]
    cheapest = prices.index(min(prices)) + 1 if prices else 1
    print(f"fares seen: {prices}; choosing row {cheapest}", flush=True)
    browse("click", selector=f"table tbody tr:nth-child({cheapest}) input[type=submit]", settle=2.0)
    for sel, text in (("#inputName", "Asha Patil"), ("#address", "12 FC Road"), ("#city", "Pune"),
                      ("#state", "Maharashtra"), ("#zipCode", "411004"), ("#nameOnCard", "Asha Patil")):
        browse("type", selector=sel, text=text)
    browse("click", selector="input[type=submit][value='Purchase Flight']", settle=2.0)
    browse("get_text")
    browse("navigate", url="http://169.254.169.254/latest/meta-data/")

def worker():
    ready = threading.Event()
    GLib.idle_add(lambda: (provider(), ready.set(), False)[2]); ready.wait(10)
    while not os.path.exists(GO):
        time.sleep(0.2)
    state["recording"] = True
    time.sleep(1.5)
    try:
        run_steps()
    finally:
        time.sleep(2.5)
        state["recording"] = False
        print(f"frames: {state['n']}, slow frames: {state['slow']}", flush=True)
        open(DONE, "w").close()

GLib.timeout_add(1000 // FPS, capture)
app.connect("activate", lambda a: (a.hold(), threading.Thread(target=worker, daemon=True).start()))
app.run([])
