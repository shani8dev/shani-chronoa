"""Drive the real Chronoa app through a scripted run, headless, and shoot it.

The in-process half of `tools/drive_app.sh`: it runs the real
`ChronoaApplication` on the headless GNOME the wrapper set up, then plays a
small JSON *script* of steps against the live main window - the same methods and
actions the UI itself calls, so what it exercises is the shipped behaviour and
not a reimplementation.

**Why a tool and not a scratch script.** Setting up a fresh cloud user, a clean
config, a task, a model switch and a screenshot was retyped into throwaway files
every time a run was needed (`AGENTS.md`'s "Rendering the UI" section is a list
of those). This is that work owned: one harness, parameterised, that any later
run reuses - the same reason `tools/live_window.sh` exists, one interaction
deeper.

The script is JSON in `$DRIVE_SCRIPT` (a path) or inline `$DRIVE_SCRIPT_JSON`, a
list of steps run in order on the GTK loop:

    {"wait": 1500}                     pause (ms) for an async answer to land
    {"ask": "what is 2+2?"}            submit a turn through the composer
    {"cloud_model": ["kilo", "id"]}    switch the answering cloud model
    {"thinking": "high"}               set the reasoning effort (stateful action)
    {"reply_style": "brief"}           set the reply style
    {"action": ["name", "s", "val"]}   activate any app action (type + value)
    {"state": ["name", "s", "val"]}    change a stateful action's state
    {"shot": "label"}                  screenshot to $DRIVE_OUT/label.png
    {"audit_composer": true}           print the composer wiring report (see below)

The audit walks every button/menu-button under the composer and reports, per
control, whether it carries a `GtkActionable` action name or a clicked/activated
handler - i.e. whether pressing it does anything. Inert controls are the point.
"""
import json
import os
import sys

repo = os.environ.get("REPO") or os.getcwd()
sys.path.insert(0, os.path.join(repo, "usr", "lib", "shani-chronoa"))

from gi.repository import GLib, Gio, Graphene, Gtk  # noqa: E402
from shani_chronoa.app import application as appmod  # noqa: E402

OUT = os.environ.get("DRIVE_OUT", "/tmp/drive")
SIZE = os.environ.get("DRIVE_SIZE", "480x760")
W, H = (int(v) for v in SIZE.lower().split("x"))
os.makedirs(OUT, exist_ok=True)


def _script():
    path = os.environ.get("DRIVE_SCRIPT", "")
    if path and os.path.exists(path):
        return json.loads(open(path).read())
    return json.loads(os.environ.get("DRIVE_SCRIPT_JSON", "[]"))


def _shoot(win, label):
    w, h = win.get_width() or W, win.get_height() or H
    snap = Gtk.Snapshot()
    Gtk.WidgetPaintable.new(win).snapshot(snap, w, h)
    node = snap.to_node()
    if node is None:
        print("NO NODE", label, flush=True)
        return
    tex = win.get_renderer().render_texture(node, Graphene.Rect().init(0, 0, w, h))
    tex.save_to_png(os.path.join(OUT, label + ".png"))
    print("SHOT", label, w, h, flush=True)


def _variant(sig, val):
    return GLib.Variant(sig, tuple(val) if sig.startswith("(") else val)


def _walk(widget, depth=0):
    yield widget, depth
    child = widget.get_first_child() if hasattr(widget, "get_first_child") else None
    while child is not None:
        yield from _walk(child, depth + 1)
        child = child.get_next_sibling()


def _audit_composer(win):
    """Report every pressable control under the composer and whether it is wired."""
    composer = (getattr(win, "_composer", None) or getattr(win, "_input_bar", None)
                or getattr(win, "_composer_box", None))
    if composer is None:
        # Fall back to the subtree around the input entry.
        entry = getattr(win, "_input_entry", None)
        composer = entry.get_ancestor(Gtk.Box) if entry is not None else win
    print("AUDIT composer", type(composer).__name__, flush=True)
    for widget, _d in _walk(composer):
        if not isinstance(widget, (Gtk.Button, Gtk.MenuButton, Gtk.ToggleButton)):
            continue
        name = type(widget).__name__
        label = widget.get_tooltip_text() or ""
        try:
            lbl = widget.get_child()
            if isinstance(lbl, Gtk.Label):
                label = label or lbl.get_text()
        except Exception:  # noqa: BLE001
            pass
        wired = []
        action = widget.get_action_name() if hasattr(widget, "get_action_name") else None
        if action:
            wired.append("action=" + action)
        if isinstance(widget, Gtk.MenuButton):
            if widget.get_menu_model() is not None:
                wired.append("menu")
            if widget.get_popover() is not None:
                wired.append("popover")
        # A clicked/activated/toggled handler is wired even without a GAction.
        from gi.repository import GObject
        for sig in ("clicked", "toggled", "activate"):
            sid = GObject.signal_lookup(sig, type(widget))
            if not sid:
                continue
            found = GObject.signal_handler_find(
                widget, GObject.SignalMatchType.ID, sid, 0, None, None, None)
            if found and found != 0:
                wired.append(sig)
                break
        verdict = "WIRED" if wired else "UNWIRED"
        print(f"  {verdict} {name} [{label[:34]}] {' '.join(wired)}", flush=True)


def _run(win, app):
    steps = _script()
    idx = {"i": 0}

    def act(name, sig, val):
        try:
            app.activate_action(name, _variant(sig, val) if sig else None)
        except Exception as exc:  # noqa: BLE001
            print("ACTION ERR", name, exc, flush=True)

    def state(name, sig, val):
        try:
            app.change_action_state(name, _variant(sig, val))
        except Exception as exc:  # noqa: BLE001
            print("STATE ERR", name, exc, flush=True)

    def step():
        if idx["i"] >= len(steps):
            print("DONE", flush=True)
            app.quit()
            return False
        s = steps[idx["i"]]
        idx["i"] += 1
        delay = 300
        if "wait" in s:
            delay = int(s["wait"])
        elif "ask" in s:
            print("ASK", s["ask"][:40], flush=True)
            win.submit_text(s["ask"])
        elif "cloud_model" in s:
            act("answer-cloud-model", "(ss)", s["cloud_model"])
            print("CLOUD_MODEL", s["cloud_model"], flush=True)
        elif "thinking" in s:
            state("thinking", "s", s["thinking"])
            print("THINKING", s["thinking"], "-> config", app.config.thinking, flush=True)
        elif "reply_style" in s:
            state("reply-style", "s", s["reply_style"])
        elif "project" in s:
            path = os.path.expandvars(s["project"])
            os.makedirs(path, exist_ok=True)
            if hasattr(app, "_set_project"):
                app._set_project(path)
            state("answer-rules", "s", "build")
            print("PROJECT", path, flush=True)
        elif "action" in s:
            act(s["action"][0], s["action"][1] if len(s["action"]) > 1 else "", s["action"][2:] and s["action"][2])
        elif "state" in s:
            state(s["state"][0], s["state"][1], s["state"][2])
        elif "shot" in s:
            _shoot(win, s["shot"])
        elif "audit_composer" in s:
            _audit_composer(win)
        GLib.timeout_add(delay, step)
        return False

    GLib.timeout_add(1500, step)


def _on_window(app, win):
    if not hasattr(win, "submit_text"):
        return
    win.connect("realize", lambda w: w.set_default_size(W, H))
    win.connect("map", lambda w: GLib.timeout_add(1200, lambda: (_run(w, app), False)[1]))


_init = appmod.ChronoaApplication.__init__


def _patched(self, *a, **k):
    _init(self, *a, **k)
    self.connect("window-added", _on_window)


appmod.ChronoaApplication.__init__ = _patched
sys.argv = [sys.argv[0]]
appmod.main()
