"""The real Chronoa app, with a frame recorder attached in the same process.

Settings go into this run's own throwaway profile. The request is typed into
the app's chat entry and sent with its Send button (through GTK, not a
keyboard); everything after that - which skills, in what order, with what
arguments - is the cloud model's own choice through Chronoa's normal path.
"""
import sys, os, time, threading
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "usr", "lib", "shani-chronoa"))
import gi
gi.require_version("Gtk", "4.0"); gi.require_version("Graphene", "1.0")
from gi.repository import Gtk, GLib, Graphene
from shani_chronoa.config import ChronoaConfig

GO, DONE, FRAMES, PROMPT_FILE = sys.argv[1:5]
os.makedirs(FRAMES, exist_ok=True)
c = ChronoaConfig()
for key, value in {
    "setup-complete": "true", "setup-mode": "cloud", "privacy-mode": "false",
    "cloud-fallback-enabled": "true", "web-sense-enabled": "true",
    "custom-llm-base-url": "https://api.kilo.ai/api/gateway", "custom-llm-model": "kilo-auto/free",
}.items():
    c.set(key, value)

from shani_chronoa.app.application import ChronoaApplication
app = ChronoaApplication()
S = {"rec": False, "n": 0, "done_after": None}

def grab(win, name):
    w, h = win.get_width(), win.get_height()
    if not (w and h):
        S["skipped"] = S.get("skipped", 0) + 1
        return
    snap = Gtk.Snapshot(); Gtk.WidgetPaintable.new(win).snapshot(snap, w, h)
    node = snap.to_node()
    if node is not None:
        win.get_native().get_renderer().render_texture(node, Graphene.Rect().init(0, 0, w, h)).save_to_png(name)

def capture():
    if S["rec"]:
        n = S["n"]
        if app.window is not None:
            grab(app.window, os.path.join(FRAMES, f"main_{n:05d}.png"))
        bw = getattr(app, "_browser_window", None)
        if bw is not None:
            grab(bw, os.path.join(FRAMES, f"browser_{n:05d}.png"))
        S["n"] += 1
    return True

def type_and_send(text, i=0):
    win = app.window
    if i < len(text):
        win._input_entry.set_text(text[:i + 1]); win._input_entry.set_position(-1)
        GLib.timeout_add(28, type_and_send, text, i + 1)
    else:
        GLib.timeout_add(600, lambda: (win._send_button.emit("clicked"), False)[1])
    return False

def driver():
    while not os.path.exists(GO):
        time.sleep(0.2)
    S["rec"] = True
    time.sleep(2)
    prompt = open(PROMPT_FILE).read().strip()
    GLib.idle_add(type_and_send, prompt)
    # Done when the window has left IDLE for the turn and come back to it for
    # 8s straight - the app's own state, not a guess from a quiet log.
    from shani_chronoa.gui.widgets import AssistantState
    busy_seen, idle_since, start, last = False, None, time.monotonic(), None
    while time.monotonic() - start < 540:
        state = getattr(app.window, "_state", None) if app.window else None
        if state != last:
            print(f"STATE {state}", flush=True); last = state
        if state is not None and state != AssistantState.IDLE:
            busy_seen, idle_since = True, None
        elif busy_seen:
            idle_since = idle_since or time.monotonic()
            if time.monotonic() - idle_since > 8:
                break
        time.sleep(0.5)
    time.sleep(2); S["rec"] = False
    print(f"frames: {S['n']}, skipped grabs: {S.get('skipped', 0)}", flush=True)
    open(DONE, "w").close()

SEEN = {}

def answer_questions():
    """Stand in for the person: press "Allow" once a question has been on screen 3s."""
    for win in Gtk.Window.list_toplevels():
        if not win.get_mapped():
            continue
        stack = [win]
        while stack:
            w = stack.pop()
            if isinstance(w, Gtk.Button) and w.get_label() in ("Allow", "Let it continue") and w.get_mapped():
                first = SEEN.setdefault(id(w), time.monotonic())
                if time.monotonic() - first > 3.0:
                    print(f"DEMO pressed {w.get_label()!r} (standing in for the person)", flush=True)
                    SEEN.pop(id(w)); w.emit("clicked")
            c = w.get_first_child()
            while c is not None:
                stack.append(c); c = c.get_next_sibling()
    return True

GLib.timeout_add(500, answer_questions)
GLib.timeout_add(166, capture)
threading.Thread(target=driver, daemon=True).start()
sys.exit(app.run([sys.argv[0]]))
