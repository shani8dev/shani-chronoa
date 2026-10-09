"""The real Chronoa app, spoken to: requests go in through a virtual microphone.

Same as `real_app.py`, except each request is a WAV played into the virtual
microphone (`mic_feed` -> `demo_mic`, set up by `record.py`) after the app's own
listen action, so Chronoa hears it through its normal path: capture, silence
auto-stop, whisper.cpp, then the turn. Replies are spoken to `demo_speaker`.

Original docstring follows.

The real Chronoa app, with a frame recorder attached in the same process.

Settings go into this run's own throwaway profile. The request is typed into
the app's chat entry and sent with its Send button (through GTK, not a
keyboard); everything after that - which skills, in what order, with what
arguments - is the cloud model's own choice through Chronoa's normal path.
"""
import sys, os, time, threading
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "usr", "lib", "shani-chronoa"))
import logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s.%(msecs)03d %(name)s: %(message)s', datefmt='%H:%M:%S')
import gi
gi.require_version("Gtk", "4.0"); gi.require_version("Graphene", "1.0")
from gi.repository import Gtk, GLib, Graphene
from shani_chronoa.config import ChronoaConfig

GO, DONE, FRAMES, TURNS_DIR = sys.argv[1:5]
os.makedirs(FRAMES, exist_ok=True)
c = ChronoaConfig()
for key, value in {
    "setup-complete": "true", "setup-mode": "cloud", "privacy-mode": "false",
    "cloud-fallback-enabled": "true", "web-sense-enabled": "true",
    "audio-input-device": "demo_mic", "audio-output-device": "demo_speaker",
    "whisper-model": "base", "end-of-speech-pause": "2.0",
}.items():
    c.set(key, value)
# The brain: the local model (record.py --brain local links the cached GGUF in)
# or the free cloud chain through Kilo.
if os.environ.get("CHRONOA_DEMO_BRAIN", "cloud") == "local":
    c.set("setup-mode", "local"); c.set("cloud-fallback-enabled", "false")
else:
    c.set("custom-llm-base-url", "https://api.kilo.ai/api/gateway")
    c.set("custom-llm-model", "kilo-auto/free")

from shani_chronoa.app.application import ChronoaApplication
app = ChronoaApplication()

# TIMING (diagnosis): how long each step between the mic press and recording takes.
import functools
def _timed(owner, name, label):
    fn = getattr(owner, name)
    @functools.wraps(fn)
    def wrapper(*a, **k):
        t = time.monotonic()
        try:
            return fn(*a, **k)
        finally:
            print(f"TIMING {label} {time.monotonic() - t:.3f}s", flush=True)
    setattr(owner, name, wrapper)
if os.environ.get("CHRONOA_DEMO_TIMING"):
    from shani_chronoa import cues as _cues
    _timed(_cues, "play", "cues.play")
    for _n in ("_begin_listening", "_silence_reply", "_start_listening"):
        _timed(type(app), _n, _n)
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
    if os.environ.get("CHRONOA_DEMO_NO_FRAMES"):
        return True
    if S["rec"]:
        n = S["n"]
        if app.window is not None:
            grab(app.window, os.path.join(FRAMES, f"main_{n:05d}.png"))
        bw = getattr(app, "_browser_window", None)
        if bw is not None:
            grab(bw, os.path.join(FRAMES, f"browser_{n:05d}.png"))
        S["n"] += 1
    return True

ANSWERS = sorted(os.path.join(TURNS_DIR, n) for n in os.listdir(TURNS_DIR) if n.startswith("answer"))
_asked = {"since": None}


def answer_question(state):
    """Answer a question Chronoa asked aloud, by voice, as the person would.

    When the model asks (ask_user) during a spoken turn, Chronoa says the question
    and listens for the reply. A harness that never answers leaves the question
    pending and the next request lands on it, which is what tangled earlier
    runs. The next line of voice-answers.txt is spoken once Chronoa has been
    listening for a second with a question pending.
    """
    import subprocess
    from shani_chronoa.gui.widgets import AssistantState
    # The window holds `_pending_question` for as long as Chronoa waits for the
    # answer; `ask_bridge.pending_count()` drops to 0 the moment the question is
    # handed to the window, so it never saw a question to answer.
    waiting = app.window is not None and getattr(app.window, "_pending_question", None) is not None
    if not ANSWERS or not waiting or state != AssistantState.LISTENING:
        _asked["since"] = None
        return
    _asked["since"] = _asked["since"] or time.monotonic()
    if time.monotonic() - _asked["since"] > 1.0:
        wav = ANSWERS.pop(0)
        print(f"ANSWER {os.path.basename(wav)} {time.strftime('%H:%M:%S')}", flush=True)
        subprocess.run(["pw-play", "--target", "mic_feed", wav], check=False)
        _asked["since"] = None


def wait_for_idle(app, limit=420):
    """Until the window has left IDLE and come back to it for 6s - the app's own state."""
    from shani_chronoa.gui.widgets import AssistantState
    busy_seen, idle_since, start, last = False, None, time.monotonic(), None
    while time.monotonic() - start < limit:
        state = getattr(app.window, "_state", None) if app.window else None
        if state != last:
            print(f"STATE {state}", flush=True); last = state
        answer_question(state)
        if state is not None and state not in (AssistantState.IDLE, AssistantState.ERROR):
            busy_seen, idle_since = True, None
        elif busy_seen or state == AssistantState.ERROR:
            # ERROR ends a turn as surely as IDLE (e.g. every free provider
            # exhausted); waiting for IDLE there sat out the 7-minute cap.
            idle_since = idle_since or time.monotonic()
            if time.monotonic() - idle_since > 6:
                return
        time.sleep(0.5)


def driver():
    import subprocess
    if os.environ.get("CHRONOA_DEMO_TIMING"):
        from shani_chronoa.audio import AudioRecorder, AudioPlayer
        _timed(AudioRecorder, "start_auto_stop", "recorder.start_auto_stop")
        _timed(AudioPlayer, "stop", "player.stop")
    while not os.path.exists(GO):
        time.sleep(0.2)
    S["rec"] = True
    time.sleep(2)
    turns = sorted(p for p in os.listdir(TURNS_DIR) if p.startswith("turn") and p.endswith(".wav"))
    for name in turns:
        wav = os.path.join(TURNS_DIR, name)
        print(f"TURN {name} {time.time():.3f} {time.strftime('%H:%M:%S')}", flush=True)
        # The app's own listen action - the one the mic button and the tray use.
        # High priority: at the default idle priority the frame recorder's 6 fps
        # timeout starved this press for ~3 s (measured), so the request was
        # spoken before Chronoa began recording - the harness's fault, not the app's.
        GLib.idle_add(lambda: (app.activate_action("toggle-listening", None), False)[1],
                      priority=GLib.PRIORITY_HIGH)
        time.sleep(float(os.environ.get("CHRONOA_DEMO_PRESS_TO_SPEAK", "1.2")))
        print(f"PLAY {name} {time.strftime('%H:%M:%S')}", flush=True)
        subprocess.run(["pw-play", "--target", "mic_feed", wav], check=False)
        print(f"PLAYED {name} {time.strftime('%H:%M:%S')}", flush=True)
        wait_for_idle(app)
        time.sleep(1.5)
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

def watchdog():
    """WATCHDOG (diagnosis, CHRONOA_DEMO_WATCHDOG=1): print the main thread's stack
    whenever the GTK main loop stops answering for more than half a second."""
    import sys as _sys, traceback
    main_id = threading.main_thread().ident
    while True:
        answered = threading.Event()
        GLib.idle_add(lambda: (answered.set(), False)[1], priority=GLib.PRIORITY_HIGH)
        if not answered.wait(0.5):
            frame = _sys._current_frames().get(main_id)
            stack = "".join(traceback.format_stack(frame)[-12:]) if frame else "?"
            print(f"WATCHDOG main loop stalled at {time.strftime('%H:%M:%S')}:\n{stack}", flush=True)
            answered.wait(30)
        time.sleep(0.1)

if os.environ.get("CHRONOA_DEMO_WATCHDOG"):
    threading.Thread(target=watchdog, daemon=True).start()
GLib.timeout_add(500, answer_questions)
GLib.timeout_add(166, capture)
threading.Thread(target=driver, daemon=True).start()
sys.exit(app.run([sys.argv[0]]))
