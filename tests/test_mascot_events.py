"""The mascot's event bus: the slot, the routes, and the wires that feed them.

`mascot_events` is a module-level slot plus named producers. The slot itself is
tested **live** - install, emit, uninstall, with a recording listener - because
those are behaviours, and source text cannot prove a bus delivers.

The producers are tested **at their call sites** through the source text,
because a producer's whole job is to be called from a module that otherwise has
no relationship to the mascot; the only observable fact is that the call site
exists, says what happened, and sits on the right side of the sequencing rule
(a face is emitted before `_turn_finished` starts anything queued - the same
reason `tests/test_a_failed_turn_is_not_an_answer.py` exists).

Three of these tests are controls for how each other one could lie:

- `test_emit_without_a_listener_is_a_no_op` is the control for every producer
  test: a bus that raised on an empty slot would make a producer test green by
  crashing around it. The companion is off by default; the slot is empty more
  often than not by design.
- the uninstall tests pin the `listener is _listener` guard from both sides;
  a late `stop()` must not unlisten its replacement.
- `test_the_step_is_a_bubble_not_a_face` routes on a bare `Mascot` instance,
  pinned against `notice()` at all - the mapping `BY_NAME -> _EVENT_FACE`
  would route "step" nowhere at all if both tables agreed to include it.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "usr/lib/shani-chronoa"))

from shani_chronoa import mascot, mascot_events  # noqa: E402
from tests._source import package_source  # noqa: E402


class _Recorder:
    def __init__(self):
        self.seen = []

    def __call__(self, kind, text=""):
        self.seen.append((kind, text))


# --- the slot itself, live ---------------------------------------------------

def test_emit_without_a_listener_is_a_no_op():
    """**The control for the whole file.**

    The companion is off by default, and on Mutter it never opens at all - so
    the slot is empty most of the time and *every* producer fires into it. If
    `emit` raised here, or returned something callers had to handle, each
    producer above would be a turn-killer waiting for the person to notice.
    """
    mascot_events.uninstall()
    assert mascot_events.installed() is False, "a listener is installed globally"
    res = mascot_events.emit("build-ok", "hello")
    assert res is None, f"emit answered with {res!r} - a no-op must not invent a reply"


def test_emit_reaches_the_installed_listener():
    rec = _Recorder()
    mascot_events.install(rec)
    try:
        mascot_events.emit("build-ok", "done")
        assert rec.seen == [("build-ok", "done")], (
            f"installed listener saw {rec.seen!r}")
    finally:
        mascot_events.uninstall(rec)


def test_emit_never_raises_even_and_especially_when_the_listener_raises():
    """A broken listener is logged and dropped; the producer's call proceeds."""
    def broken(kind, text=""):
        raise RuntimeError("a face is never worth a failed turn")

    mascot_events.install(broken)
    try:
        mascot_events.emit("greet", "hi")  # must not raise
    finally:
        mascot_events.uninstall(broken)


def test_uninstall_clears_only_when_it_is_the_same_listener():
    """The `listener is _listener` guard, from the side that matters: a window
    destroyed late must not unlisten the mascot that replaced it."""
    old, newer = _Recorder(), _Recorder()
    mascot_events.install(old)
    try:
        mascot_events.uninstall(newer)
        assert mascot_events.installed() and old is not None, (
            "uninstall(other) cleared the slot - a late stop() would have "
            "dropped the replacement mascot's own events")
        mascot_events.uninstall(old)
        assert mascot_events.installed() is False, (
            "uninstall(self) left a listener installed")
    finally:
        mascot_events.uninstall()


# --- the table: kinds the bus names must be faces the mascot can draw ----------

def test_every_bus_kind_except_step_is_a_face_mascot_can_draw():
    """`BY_NAME` and `mascot._EVENT_FACE` must not drift apart in silence."""
    for name, kind in mascot_events.BY_NAME.items():
        if kind == "step":
            continue
        assert kind in mascot._EVENT_FACE, (
            f"event {name!r} maps to face kind {kind!r}, which "
            f"`mascot._EVENT_FACE` does not know - the bus would silently show "
            f"nothing (notice logs a miss and leaves the picture alone)")


def test_the_step_is_a_bubble_not_a_face():
    """"step" routes to `say()` - a tool running is what's happening, not a
    reaction - and everything else routes to `notice()`. Pinned on a bare
    `Mascot`, so this can fail independently of any window."""
    m = object.__new__(mascot.Mascot)
    m._asleep = False
    m._last_activity = 0.0
    said, noticed = [], []
    m.say = lambda text="": said.append(text)
    m.notice = lambda kind, text="": noticed.append((kind, text))

    m._on_event_here("step", "opening the file")
    m._on_event_here("build-ok", "done")
    assert said == ["opening the file"], f"step routed as {said!r}"
    assert noticed == [("build-ok", "done")], f"notice routed as {noticed!r}"


def test_a_producer_off_the_gtk_thread_is_hopped_onto_it():
    """`_on_event` compares threads and hops via `GLib.idle_add`. Recorded by
    standing in for `GLib.idle_add`, because a real queue would need a loop."""
    from gi.repository import GLib

    m = object.__new__(mascot.Mascot)
    m._main_thread = "this is not any thread's ident"

    hopped = []

    def fake_idle_add(fn, *args):
        hopped.append((fn, args))
        return 0

    original = GLib.idle_add
    GLib.idle_add = fake_idle_add
    try:
        m._on_event("greet", "hi")
    finally:
        GLib.idle_add = original

    assert hopped and hopped[0][0].__func__ is mascot.Mascot._on_event_here, (
        "the event touched the mascot on its own thread instead of hopping - "
        "that is the undefined behaviour `ask_bridge`'s docstring warns about")


# --- the wires: producers really are called, where the decisions are made -------

CONVERSATION = package_source("app.conversation")
ASK_BRIDGE = package_source("ask_bridge")
PERMISSIONS = package_source("permissions")
MASCOT = package_source("mascot")


def test_a_tool_run_says_what_it_is_doing_from_the_callback():
    """`_on_tool_call` runs on AsyncBridge's thread; the step reaches the bus
    through the same `GLib.idle_add` hop the desktop cue already uses."""
    needle = "mascot_events.step(step)"
    assert CONVERSATION.count(needle) == 1, (
        f"`mascot_events.step(...)` appears {CONVERSATION.count(needle)} times, "
        f"expected exactly the one feed from the desktop cue's own words")
    # Inside the idle hop, not beside it - the callback's thread is not GTK's.
    site = CONVERSATION[CONVERSATION.index(needle) - 200:CONVERSATION.index(needle)]
    assert "GLib.idle_add(lambda" in site, "the step is emitted off the hop"


def test_a_finished_turn_wears_the_ok_face_before_the_queue_drains():
    """The success-path emit sits before `_turn_finished()`, so the face is
    this turn's ending and not the next turn's start."""
    needle = "mascot_events.finished()"
    assert CONVERSATION.count(needle) == 1, (
        f"`mascot_events.finished()` appears {CONVERSATION.count(needle)} "
        f"times - a turn that answered is exactly one ending")
    tail = CONVERSATION.rindex("self._turn_finished()")
    assert CONVERSATION.index(needle) + len(needle) <= tail, (
        "the finished face is emitted after the last `_turn_finished()` on the "
        "path, so whatever queue it starts could repaint it first")


def test_a_failed_turn_wears_the_concerned_face_before_the_queue_drains():
    needle = "mascot_events.error(str(result))"
    assert CONVERSATION.count(needle) == 1, (
        f"`mascot_events.error(...)` appears {CONVERSATION.count(needle)} times")
    end = CONVERSATION.index(needle) + len(needle)
    drain = CONVERSATION.index("self._turn_finished()", end)
    assert all("mascot_events" not in line for line in CONVERSATION[end:drain].splitlines()), (
        "something reaches the bus between the error face and the drain")


def test_a_question_passes_the_face_at_the_chokepoint_it_also_has():
    """`ask_bridge.ask` is the one chokepoint every question passes; the face
    is emitted before the presenter, so it shows while the person is asked."""
    assert '"greet"' in ASK_BRIDGE, "the default face is not named at the call"
    needle = "mascot_events.emit(kind, question)"
    assert ASK_BRIDGE.count(needle) == 1, (
        f"`mascot_events.emit(kind, ...)` appears {ASK_BRIDGE.count(needle)} "
        f"times in ask_bridge")
    presenter_at = ASK_BRIDGE.index("_presenter(question")
    assert ASK_BRIDGE.index(needle) < presenter_at, (
        "the question's face is emitted after the presenter appears, so the "
        "expression cannot be up when the widget is")


def test_the_gates_wear_the_approval_face_and_answer_with_one_face():
    """`permissions.decide` asks with `kind="meeting"` and settles once -
    one face for four exits, not four copies of the rule to drift apart."""
    assert PERMISSIONS.count('kind="meeting"') == 1, (
        "the approval question does not wear the approval face")
    needle = "mascot_events.settled("
    assert PERMISSIONS.count(needle) == 1, (
        f"`mascot_events.settled(` appears {PERMISSIONS.count(needle)} times - "
        f"the face for the answer is read once, after the answer")
    after = PERMISSIONS.index(needle) + len(needle)
    assert "ALLOW_ONCE" in PERMISSIONS[after:after + 200] or "ALLOW_TURN" in PERMISSIONS[after:after + 200], (
        "settled() does not read the allow decisions, so a refusal would wear "
        "the happy face")


def test_the_mascot_listens_while_its_window_exists():
    """Installed after the window is built, cleared before `stop()` tears it
    down - the source shapes the lifecycle, and the other tests pin the words."""
    assert MASCOT.count("mascot_events.install(self._on_event)") == 1, (
        "the mascot does not install exactly one listener")
    start_at = MASCOT.index("def start(self)")
    install_at = MASCOT.index("mascot_events.install(self._on_event)")
    stop_at = MASCOT.index("def stop(self)")
    assert start_at < install_at < stop_at, (
        "the listener is not installed inside `start()`")
    uninstall_at = MASCOT.index("mascot_events.uninstall(self._on_event)")
    assert stop_at < uninstall_at, (
        "the listener is not cleared at the top of `stop()`")
