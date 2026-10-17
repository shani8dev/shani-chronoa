"""The real Chronoa app, with a frame recorder attached in the same process.

Settings go into this run's own throwaway profile. The request is typed into
the app's chat entry and sent with its Send button (through GTK, not a
keyboard); everything after that - which skills, in what order, with what
arguments - is the cloud model's own choice through Chronoa's normal path.
"""
import sys, os, subprocess, time, threading
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "usr", "lib", "shani-chronoa"))
import gi
gi.require_version("Gtk", "4.0"); gi.require_version("Graphene", "1.0")
from gi.repository import Gtk, GLib, Graphene
from shani_chronoa.config import ChronoaConfig

GO, DONE, FRAMES, PROMPT_FILE = sys.argv[1:5]
os.makedirs(FRAMES, exist_ok=True)
c = ChronoaConfig()
LOCAL = os.environ.get("CHRONOA_DEMO_BRAIN") == "local"
if LOCAL:
    # **The private demo.** The model on this machine, privacy mode on, no
    # cloud fallback and no cloud URL configured at all - so the egress chart
    # shown at the end can only say "everything stayed on this computer".
    settings = {"setup-complete": "true", "setup-mode": "local", "privacy-mode": "true",
                "cloud-fallback-enabled": "false"}
else:
    settings = {"setup-complete": "true", "setup-mode": "cloud", "privacy-mode": "false",
                "cloud-fallback-enabled": "true", "web-sense-enabled": "true",
                "custom-llm-base-url": "https://api.kilo.ai/api/gateway",
                "custom-llm-model": "kilo-auto/free"}
# Extra switches a scenario needs (e.g. file-edit-enabled=true), "k=v,k=v".
for pair in filter(None, os.environ.get("CHRONOA_DEMO_SETTINGS", "").split(",")):
    k, _, v = pair.partition("=")
    settings[k.strip()] = v.strip()
for key, value in settings.items():
    c.set(key, value)

#: The coding demo: a tiny project with one bug and a failing test, seeded in
#: this private session's home, and the app put in Build mode on it.
PROJECT = os.environ.get("CHRONOA_DEMO_PROJECT", "")
if PROJECT:
    project = os.path.expanduser(PROJECT)
    os.makedirs(project, exist_ok=True)
    with open(os.path.join(project, "pricing.py"), "w") as fh:
        fh.write('''"""Prices for the shop."""


def total(prices, discount=0.0):
    """Sum of prices, after a percentage discount (10 means 10%)."""
    subtotal = sum(prices)
    return round(subtotal - subtotal * discount, 2)
''')
    with open(os.path.join(project, "test_pricing.py"), "w") as fh:
        fh.write('''from pricing import total


def test_no_discount():
    assert total([10, 5]) == 15


def test_ten_percent_off():
    assert total([10, 10], 10) == 18
''')
    # Its own pytest root, as a real project has: without one pytest walks up
    # looking for a config and the sandbox (rightly) refuses to let it read
    # folders outside the project.
    with open(os.path.join(project, "pytest.ini"), "w") as fh:
        fh.write("[pytest]\n")
    settings_extra = {"project-dir": project}
    for key, value in settings_extra.items():
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

#: Filmed from outside (record.py --screen): the window does not grab itself,
#: a question goes to the notification, and the finale opens the top-bar menu.
SCREEN = os.environ.get("CHRONOA_DEMO_SCREEN") == "1"


def capture():
    if S["rec"] and not SCREEN:
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
        win.set_input_text(text[:i + 1])  # the composer is a TextView now
        GLib.timeout_add(28, type_and_send, text, i + 1)
    else:
        GLib.timeout_add(600, lambda: (win._send_button.emit("clicked"), False)[1])
    return False

def driver():
    while not os.path.exists(GO):
        time.sleep(0.2)
    S["rec"] = True
    if PROJECT:
        def build_mode():
            app._set_project(os.path.expanduser(PROJECT))
            rules = app.lookup_action("answer-rules")
            if rules is not None:
                rules.change_state(GLib.Variant("s", "build"))
            return False
        GLib.idle_add(build_mode)
    time.sleep(2)
    # Several requests, one per line (CHRONOA_DEMO_PROMPTS), each sent when
    # the previous turn is over - an everyday-use run in one session.
    many = os.environ.get("CHRONOA_DEMO_PROMPTS", "")
    prompts = [l.strip() for l in open(many).read().splitlines() if l.strip()] if many \
        else [open(PROMPT_FILE).read().strip()]
    for n, prompt in enumerate(prompts):
        print(f"PROMPT {n + 1}/{len(prompts)} {prompt}", flush=True)
        GLib.idle_add(type_and_send, prompt)
        if SCREEN and os.environ.get("CHRONOA_DEMO_KEEP_WINDOW") != "1":
            # Out of the way, so the question arrives as a notification.
            time.sleep(4)
            GLib.idle_add(lambda: (app.window.minimize(), False)[1])
        wait_turn()
    _finale_and_stop()


def wait_turn():
    """Until the window has left IDLE for the turn and come back to it for
    8s straight - the app's own state, not a guess from a quiet log."""
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


def _raise_chronoa() -> None:
    """Bring Chronoa's window back through the window extension.

    `window.present()` from a script is not a user action, so GNOME's focus-
    stealing guard showed a "'Shani Chronoa' is ready" notice instead of the
    window (measured in the desktop demo). Focus through the extension is the
    same act as clicking it.
    """
    import json
    def gd(*a):
        return subprocess.run(["gdbus", "call", "--session", "--dest", "org.shani.Chronoa.Windows",
                               "--object-path", "/org/shani/Chronoa/Windows", "--method", *a],
                              capture_output=True, text=True, timeout=20).stdout
    try:
        raw = gd("org.shani.Chronoa.Windows.List").strip()
        windows = json.loads(raw[raw.index("'") + 1:raw.rindex("'")].encode().decode("unicode_escape"))
        mine = next(w for w in windows if w.get("title") == "Shani Chronoa")
        gd("org.shani.Chronoa.Windows.Focus", str(mine["id"]))
    except Exception as exc:  # noqa: BLE001 - a demo nicety
        print(f"DEMO could not raise the window: {exc}", flush=True)


def _open_indicator(open_it: bool) -> None:
    subprocess.run(["gdbus", "call", "--session", "--dest", "org.shani.Demo.Capture",
                    "--object-path", "/org/shani/Demo/Capture", "--method",
                    "org.shani.Demo.Capture.OpenIndicator", "true" if open_it else "false"],
                   capture_output=True, timeout=20)


def _finale_and_stop():
    if SCREEN:
        # The window back, then Chronoa's own menu in the top bar.
        _raise_chronoa()
        time.sleep(3)
        print("FINALE indicator", flush=True)
        _open_indicator(True)
        time.sleep(5)
        _open_indicator(False)
    # The finale: what happened, shown rather than told. "privacy" opens the
    # Privacy panel (where the data went); "revert" presses the turn's own
    # "Revert N files" button; "diff" opens the Diff panel.
    for step in filter(None, os.environ.get("CHRONOA_DEMO_FINALE", "").split(",")):
        step = step.strip()
        print(f"FINALE {step}", flush=True)
        if step == "revert":
            GLib.idle_add(press_label_prefix, "Revert")
        else:
            GLib.idle_add(lambda s=step: (app.window._show_surface(s), False)[1])
        time.sleep(6)
    time.sleep(2); S["rec"] = False
    print(f"frames: {S['n']}, skipped grabs: {S.get('skipped', 0)}", flush=True)
    open(DONE, "w").close()

def press_label_prefix(prefix):
    """Press the first mapped button whose label starts with `prefix`."""
    for win in Gtk.Window.list_toplevels():
        stack = [win]
        while stack:
            w = stack.pop()
            if isinstance(w, Gtk.Button) and (w.get_label() or "").startswith(prefix) and w.get_mapped():
                print(f"DEMO pressed {w.get_label()!r}", flush=True)
                w.emit("clicked")
                return False
            ch = w.get_first_child()
            while ch is not None:
                stack.append(ch); ch = ch.get_next_sibling()
    print(f"DEMO no button starting {prefix!r}", flush=True)
    return False


SEEN = {}

def answer_questions():
    """Stand in for the person: press "Allow" once a question has been on screen 3s.

    Filmed from outside, the question is answered the way the notification's
    button answers it - `app.answer-question` - after 5s on screen.
    """
    if SCREEN:
        win = app.window
        if win is not None and win.has_pending_question():
            first = SEEN.setdefault("q", time.monotonic())
            options = list(getattr(win, "_pending_options", []) or [])
            if time.monotonic() - first > 5.0 and options:
                print(f"DEMO answered {options[0]!r} from the notification", flush=True)
                SEEN.pop("q")
                app.activate_action("answer-question", GLib.Variant("s", options[0]))
                threading.Timer(1.5, _raise_chronoa).start()
        return True
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
