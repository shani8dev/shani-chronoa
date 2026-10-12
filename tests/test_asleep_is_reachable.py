"""`Asleep` was offered to the person and could not be reached, twice over.

`presence.py` documents three states and the cycle that reaches them, and
`AGENTS.md` records why the third matters: *"Asleep means stop it **and** do not
start it again to answer the next question, which no pair of Run/Stop buttons can
express."* Measured, it was unreachable and, had it been reached, ignored.

### Defect 1: the cycle read the machine, so the third state never appeared

`_cycle_presence` computed `current = presence.detect(local_llm.is_up)` and
cycled from there. `detect()` answers *"is the model resident"* - a question about
memory - and can only ever return ACTIVE or DROWSY. `tests/test_presence.py`
pins that deliberately (`test_detection_treats_a_stopped_server_as_drowsy_not_
sleeping`). So from a released model the target was always SLEEPING, `apply()`
answered "already not resident" **without doing anything**, and the button stayed
on "Go to sleep" forever. Measured, three identical presses, identical subtitles.
`Wake up` - the action that would have got out of SLEEPING - never appeared,
because nothing ever entered it.

`self.app._presence` was the missing half: written once at startup from
`detect()`, read once by `brain._use_local_server`, and **never written again**.
So the guard that implements "do not start it again" was permanently False.

### Defect 2: the fallback path started it anyway

`_explain_no_model` has its own `local_llm.start_service()`, unconditional, on
the one branch where a model is installed. So even with the guard wired, a
question would have loaded the model - measured, with a stub window and the
service stubbed: `start_service` was called and the response read "Starting the
local model". The guard returns False *precisely so nothing is started*, and the
very next statement started it.

### The fix, and the model it is built on

Two different questions, which the first version conflated:

- `detect()` - **is the model resident?** Observable, and the honest default.
- `_presence` - **what did the person choose?** SLEEPING is a decision about
  behaviour, not an observation of memory, so `detect()` cannot see it.

The cycle is driven by the choice, written back on success, and it is what the
row reports. For ACTIVE and DROWSY the two coincide, so nothing else changes.
`_explain_no_model` refuses to start when the choice is SLEEPING and names the
way out. `_run_model` sets ACTIVE, because asking for the model overrides Asleep.
"""

from __future__ import annotations

import pathlib
import sys

import pytest

gi = pytest.importorskip("gi")
gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, GLib, Gio, Gtk  # noqa: E402

Adw.init()

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]
                       / "usr" / "lib" / "shani-chronoa"))

from shani_chronoa import local_llm  # noqa: E402
from shani_chronoa.presence import Presence  # noqa: E402
from shani_chronoa.settings_window import window as settings_window  # noqa: E402


class _App(Gtk.Application):
    """The smallest application the settings window will build against."""

    def __init__(self, choice=None):
        from shani_chronoa.config import ChronoaConfig
        super().__init__(application_id="test.chronoa.presence",
                         flags=Gio.ApplicationFlags.NON_UNIQUE)
        self.config = ChronoaConfig()
        self.window = None
        self._wake_word_active = False
        if choice is not None:
            self._presence = choice

    def activate_action(self, name, arg=None):
        pass


def _drive(window, press, button, row, seconds=20.0):
    """Press a handler and run a main loop until the worker has reported.

    `_cycle_presence` and `_run_model` both do their `systemctl` work on a thread
    and report through `GLib.idle_add`, so without a loop the assertions below
    would read the *pre-press* state and pass. The deadline is generous because
    the handlers shell out; the loop quits on its own once the callback has run.
    """
    loop = GLib.MainLoop()
    seen = []

    original = row.set_subtitle

    def watch(text):
        seen.append(text)
        return original(text)

    row.set_subtitle = watch
    # `press` is a *bound* method (`window._cycle_presence`), so it takes the
    # button and the row and nothing else - passing the window again used to
    # raise `takes 3 positional arguments but 4 were given`.
    press(button, row)

    def stop():
        if seen:
            loop.quit()
            return False
        return True

    GLib.timeout_add(100, stop)
    GLib.timeout_add_seconds(seconds, lambda: (loop.quit(), False)[1])
    loop.run()
    row.set_subtitle = original
    return seen


# ---------------------------------------------------------------------------
# the cycle reaches all three states, and records the choice
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("start,target", [
    (Presence.ACTIVE, Presence.DROWSY),
    (Presence.DROWSY, Presence.SLEEPING),      # the state that was unreachable
    (Presence.SLEEPING, Presence.ACTIVE),      # the label that never appeared
])
def test_every_state_in_the_cycle_is_reachable(start, target, monkeypatch):
    """Pressing the button moves to the next state, whichever one we are in.

    The middle row is the defect: from DROWSY the old code also targeted
    SLEEPING, but it *read the machine* to decide where it was, so after the
    first release every press was the same press. Driving the cycle from the
    stored choice is what makes the second and third presses different.
    """
    calls = []
    monkeypatch.setattr(local_llm, "is_up", lambda: False)
    monkeypatch.setattr(local_llm, "start_service",
                        lambda: (calls.append("wake"), "")[1])
    monkeypatch.setattr(local_llm, "stop_service",
                        lambda: (calls.append("sleep"), "")[1])

    app = _App(choice=start)
    window = settings_window.SettingsWindow(app)
    button = Gtk.Button()
    row = Adw.ActionRow()
    _drive(window, window._cycle_presence, button, row)

    assert app._presence is target, (
        f"from {start.name} the choice ended at {getattr(app, '_presence', None)}; "
        f"expected {target.name}")


def test_the_model_is_not_started_when_the_choice_is_to_stay_asleep(monkeypatch):
    """**The half `_explain_no_model` defeated, proved from the other side.**

    Going SLEEPING -> ACTIVE must *wake* the server (that is what Wake up means),
    and the test above covers it. This one pins the opposite direction: entering
    SLEEPING from a released model must not start anything, and `apply()`'s own
    "already not resident" answer must not be read as a wake.
    """
    woke = []
    monkeypatch.setattr(local_llm, "is_up", lambda: False)
    monkeypatch.setattr(local_llm, "start_service",
                        lambda: (woke.append("wake"), "")[1])
    monkeypatch.setattr(local_llm, "stop_service", lambda: "should not be called")

    app = _App(choice=Presence.DROWSY)
    window = settings_window.SettingsWindow(app)
    _drive(window, window._cycle_presence, Gtk.Button(), Adw.ActionRow())
    assert app._presence is Presence.SLEEPING
    assert woke == [], "entering Asleep started the server"


def test_the_row_reports_the_choice_not_the_machine():
    """SLEEPING is a decision, so the row says so - `detect()` cannot see it.

    `detect()` on a released machine answers DROWSY and `detail()` promises
    *"the next question loads it again"*, which is false once the choice is
    SLEEPING. A row that reports the machine would keep promising a wake that
    `brain` now refuses.
    """
    app = _App(choice=Presence.SLEEPING)
    window = settings_window.SettingsWindow(app)
    window._refresh_presence_row()
    said = window._presence_row.get_subtitle()
    assert "quiet until you ask" in said, (
        "the row did not describe Asleep:\n" + said)
    assert window._presence_cycle_button.get_label() == "Wake up", (
        "the row did not offer the way out: "
        + window._presence_cycle_button.get_label())


def test_a_released_model_shows_the_verb_for_a_released_model():
    """**Both labels were written once at build time and never read again.**

    Open Settings after releasing the model and it still said "Free the model" -
    the verb for a model that is loaded. The row is refreshed from the choice
    now, on build and on every show.

    Built, and *not* refreshed by hand: the first version of this test called
    `_refresh_presence_row()` itself, so deleting the call from `_build_models`
    left it green. Three of nine mutations survived that way - witnesses that
    tested the helper and not the site that calls it, which is this repo's own
    "a test that calls the function is not a test that the skill is reachable"
    rule, hit three times in one file.
    """
    app = _App(choice=Presence.DROWSY)
    window = settings_window.SettingsWindow(app)
    assert window._presence_cycle_button.get_label() == "Go to sleep", (
        "a released model did not offer its own next action: "
        + window._presence_cycle_button.get_label())


def test_the_cycle_reports_the_choice_it_reached(monkeypatch):
    """**The cycle's own report, not `_refresh_presence_row`'s.**

    From a released model the machine and the choice disagree: `detect()` sees
    DROWSY and its sentence promises *"the next question loads it again"*, while
    the choice is SLEEPING and `brain` now refuses to load anything. A row that
    reports the machine promises a wake it will not perform.

    Drives `_cycle_presence`'s `done()` rather than the refresh helper, because
    the mutation that put `detect()` back into the cycle left every other test
    in this file green.
    """
    monkeypatch.setattr(local_llm, "is_up", lambda: False)
    monkeypatch.setattr(local_llm, "stop_service", lambda: "should not be called")
    app = _App(choice=Presence.DROWSY)
    window = settings_window.SettingsWindow(app)
    driven = Adw.ActionRow()
    _drive(window, window._cycle_presence, Gtk.Button(), driven)
    assert app._presence is Presence.SLEEPING
    # Read the row that was actually driven, not the one `_build_models` made.
    said = driven.get_subtitle()
    assert "quiet until you ask" in said, (
        "the cycle reported the machine rather than the choice it reached:\n"
        + said)
    assert "loads it again" not in said, (
        "the row still promises a wake that `brain` refuses to perform:\n" + said)


def test_running_the_model_clears_an_asleep_choice(monkeypatch):
    """`_run_model` must set ACTIVE, or a running server stays one the next
    question refuses.

    Drives the handler rather than reading the source: the mutation that removed
    the write left all nine tests green, because nothing pressed the button.
    """
    monkeypatch.setattr(local_llm, "start_service", lambda: "")
    monkeypatch.setattr(local_llm, "is_up", lambda: True)
    app = _App(choice=Presence.SLEEPING)
    window = settings_window.SettingsWindow(app)
    button, row = Gtk.Button(), Adw.ActionRow()
    _drive(window, window._run_model, button, row)
    assert app._presence is Presence.ACTIVE, (
        "pressing Run the model left an Asleep choice standing, so the server "
        "runs and the next question still refuses it")


def test_showing_the_window_rereads_the_choice(monkeypatch):
    """**The on-show refresh, which nothing drove.**

    The presence row's two buttons are verbs, so they are true only of the moment
    they are read - and a person releases the model from the *Models panel*, then
    opens Settings to a row still offering "Free the model". `_on_window_visible`
    re-reads it now.

    `get_visible()` is False for a window that was built and never shown, which
    is the early return this path starts with - so a window built in a test would
    skip it and pass with or without the refresh. It is answered directly here,
    because the question under test is what the refresh *does*, not what GTK
    thinks of an unmapped window.
    """
    app = _App(choice=Presence.DROWSY)
    window = settings_window.SettingsWindow(app)
    assert window._presence_cycle_button.get_label() == "Go to sleep"
    # The choice changed underneath the window - the Models panel released it.
    app._presence = Presence.ACTIVE
    monkeypatch.setattr(window, "get_visible", lambda: True)
    window._on_window_visible(window, None)
    assert window._presence_cycle_button.get_label() == "Free the model", (
        "showing the window did not re-read the choice: "
        + window._presence_cycle_button.get_label())


# ---------------------------------------------------------------------------
# the guard in _explain_no_model
# ---------------------------------------------------------------------------


def _brain_with(choice):
    from shani_chronoa.app import brain

    class _Win:
        def __init__(self):
            self.said = []

        def set_orb_state(self, _s):
            pass

        def set_response(self, text):
            self.said.append(text)

        def set_status(self, _s):
            pass

    class _App(brain.BrainMixin):
        def __init__(self):
            self.window = _Win()
            self._presence = choice

        def _router_fallback(self, _text):
            return False

        def _submit(self, _text):
            pass

        def _open_setup(self):
            pass

    return _App()


def test_asleep_refuses_to_start_and_names_the_way_out(monkeypatch):
    """**The second defect.** `_explain_no_model` started the service
    unconditionally on its "a model is installed" branch, which is the one path
    that defeats `_use_local_server`'s guard: the guard returns False so nothing
    is started, and the next statement started it anyway.
    """
    started = []
    monkeypatch.setattr(local_llm, "installed", lambda: True)
    monkeypatch.setattr(local_llm, "server_binary", lambda: True)
    monkeypatch.setattr(local_llm, "start_service",
                        lambda: (started.append("wake"), "")[1])
    monkeypatch.setattr(local_llm, "is_up", lambda: True)

    app = _brain_with(Presence.SLEEPING)
    app._explain_no_model("what time is it")
    assert started == [], "Asleep started the model anyway"
    said = " ".join(app.window.said)
    assert "asleep" in said.lower(), "the refusal did not say the model was asleep"
    assert "Wake up" in said, "the refusal did not name the way out"


def test_drowsy_still_starts_it(monkeypatch):
    """The control: the same code path with DROWSY must still start the model.

    Without this, a guard that simply never started anything would pass the test
    above - the same "a negative control that cannot fail" trap, in the direction
    of over-refusing.
    """
    started = []
    monkeypatch.setattr(local_llm, "installed", lambda: True)
    monkeypatch.setattr(local_llm, "server_binary", lambda: True)
    monkeypatch.setattr(local_llm, "start_service",
                        lambda: (started.append("wake"), "")[1])
    monkeypatch.setattr(local_llm, "is_up", lambda: True)

    app = _brain_with(Presence.DROWSY)
    app._explain_no_model("what time is it")
    assert started == ["wake"], "Drowsy no longer starts the local model"
    assert "asleep" not in " ".join(app.window.said).lower()


def test_the_guard_is_live_where_it_is_read(monkeypatch):
    """`brain._use_local_server` refuses a wake while SLEEPING, and allows it
    while DROWSY - which is what the missing write used to make impossible."""
    from shani_chronoa.app import brain

    for choice, expected in ((Presence.SLEEPING, False),
                             (Presence.DROWSY, True)):
        started = []
        monkeypatch.setattr(local_llm, "is_up", lambda: False)
        monkeypatch.setattr(local_llm, "start_service",
                            lambda: (started.append("wake"), "")[1])

        class _App(brain.BrainMixin):
            _presence = choice

        # is_up stays False only until start_service is called; the second call
        # answers True so the "started but not answering" branch is not taken.
        up = {"now": False}
        monkeypatch.setattr(local_llm, "is_up", lambda: up["now"])

        def start():
            started.append("wake")
            up["now"] = True
            return ""

        monkeypatch.setattr(local_llm, "start_service", start)
        used = _App()._use_local_server(wake=True)
        assert used is expected, (
            f"{choice.name}: _use_local_server returned {used}, expected {expected}")
        assert (started == ["wake"]) is expected, (
            f"{choice.name}: start_service called={started}")
