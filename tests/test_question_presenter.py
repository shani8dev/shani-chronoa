"""The permission prompt was built, tested, and never installed.

`permissions.decide()` has one production caller and it is gated on
`permissions.can_ask()`, which is `ask_bridge.has_presenter()`, which is
`_presenter is not None`. Nothing in the application ever called
`set_presenter()` - only tests did. So in the running app `can_ask()` was always
False, `decide()` never ran, and every consent-gated tool (screenshot,
delete_file, connect_wifi, kill_process, ...) silently refused instead of
offering to ask. `skills/ask_user.py` is gated on the same predicate and was
dead for the same reason.

`gui.py` had the GTK half too, and equally unreachable: `ChronoaWindow.
show_question(question, options, resolve)` built a prompt row and had no callers.
What was missing was the presenter that calls it.

## What is pinned here, and what is not

Pinned: the application installs a presenter during its real startup, a
question survives the real off-thread-to-GTK-thread hand-off, a click on the real
button resolves the real event with that option, and every way of not answering
resolves `""` - never a choice.

**Not pinned: that any of it is visible to a person.** There is no compositor
and no window manager here, so legibility, layout under a real theme, window
placement, focus, and whether the prompt is on screen at all are unverified.
Every test below constructs the real GTK objects and fires their real signals;
none of them looks at a pixel. A green file in this section means "the wiring is
real", not "the dialog works".

## Why the wiring tests are a subprocess

`test_adw_initialisation.py` set the precedent: GTK holds a display connection
and libadwaita holds global state, neither of which can be set up and torn down
per test inside one interpreter. It also matters here for a sharper reason - the
whole defect was that a lifecycle hook nobody reached was the only place the
presenter was installed, so a test that calls `do_startup()` by hand has already
assumed away the thing under test. These drive `app.run([])` and report from
inside the child.

Two ordering facts the harness depends on, both found by running it and neither
obvious:

- The `startup` signal is RUN_LAST, so PyGObject's `do_startup` vfunc has
  **already** installed the presenter by the time a handler on it runs. The
  honest "before" baseline is read at module scope, before `app.run()`.
- `HANDLES_COMMAND_LINE` means `do_command_line` calls `activate()`, so the
  window already exists by the time a queued idle callback runs. Asking a
  question from inside `startup` deadlocks against itself: the call blocks the
  GTK thread that would have run the idle callback that shows the prompt. The
  first version of this harness did exactly that and hung for the full timeout.
"""

from __future__ import annotations

import json
import os
import pathlib
import subprocess
import sys
import textwrap
import threading
import time

import pytest

from gi.repository import GLib

_REPO = pathlib.Path(__file__).resolve().parents[1]
_PKG = _REPO / "usr" / "lib" / "shani-chronoa"
if str(_PKG) not in sys.path:
    sys.path.insert(0, str(_PKG))

from shani_chronoa import ask_bridge, permissions  # noqa: E402

_QUESTION = (
    "Shani wants to take a screenshot.\n\nThat needs the "
    "'vision-sense-enabled' permission, which is currently off. Allow it?"
)
_CHOICES = ["Allow this once", "Allow for this session", "No, don't allow"]

_HARNESS = textwrap.dedent(
    """
    import json, sys, threading, time
    import gi
    gi.require_version("Gtk", "4.0")
    from gi.repository import Gtk, GLib

    from shani_chronoa import ask_bridge, permissions
    from shani_chronoa.app import ChronoaApplication

    QUESTION = (
        "Shani wants to take a screenshot.\\n\\nThat needs the "
        "'vision-sense-enabled' permission, which is currently off. Allow it?")
    CHOICES = ["Allow this once", "Allow for this session", "No, don't allow"]

    out = {}
    answers = []
    quit_seen = []

    def box_children(box):
        got = []
        child = box.get_first_child()
        while child is not None:
            got.append(child)
            child = child.get_next_sibling()
        return got

    def pump(predicate, timeout=15.0):
        ctx = GLib.MainContext.default()
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if predicate():
                return True
            ctx.iteration(False)
            time.sleep(0.01)
        return bool(predicate())

    def ask_from_a_thread(question, choices, sink, timeout=90.0):
        def run():
            sink.append(ask_bridge.ask(question, choices, timeout=timeout))
        threading.Thread(target=run, daemon=True).start()

    def stage_two(app):
        window = app.window
        out["window_in_stage2"] = window is not None
        out["window_type"] = type(window).__name__

        ask_from_a_thread(QUESTION, CHOICES, answers)
        shown = pump(lambda: window._pending_question is not None)
        out["prompt_shown"] = shown
        out["row_visible"] = window._question_row.get_visible()
        buttons = box_children(window._question_widgets[1]) if shown else []
        out["button_labels"] = [b.get_label() for b in buttons]
        out["blocked_before_click"] = answers == []
        if len(buttons) == 3:
            buttons[1].emit("clicked")        # "Allow for this session"
        out["answer_arrived"] = pump(lambda: answers != [], timeout=15.0)
        out["answer"] = answers[0] if answers else None
        out["row_hidden_after"] = not window._question_row.get_visible()
        out["pending_cleared_after"] = window._pending_question is None

        # A prompt still on screen when the user quits. The tool loop is blocked
        # on it and do_shutdown joins that thread.
        ask_from_a_thread("second question?", ["yes", "no"], quit_seen)
        out["second_shown"] = pump(lambda: window._pending_question is not None)
        out["quit_started"] = time.monotonic()
        app.quit()
        return False

    def on_startup(app):
        out["after_startup"] = ask_bridge.has_presenter()
        out["can_ask"] = permissions.can_ask()
        out["window_at_startup"] = app.window is not None
        out["settings_window_at_startup"] = app._settings_window is not None

    def on_activate(app):
        GLib.idle_add(stage_two, app)

    app = ChronoaApplication()
    out["before_run"] = ask_bridge.has_presenter()
    out["window_before_run"] = app.window is not None
    app.connect("startup", on_startup)
    app.connect("activate", on_activate)
    app.run([])
    out["quit_seconds"] = round(time.monotonic() - out.pop("quit_started"), 3)
    out["second_answer"] = quit_seen[0] if quit_seen else None

    print("RESULT" + json.dumps(out))
    sys.stdout.flush()
    """
)


def _child_env(runtime: pathlib.Path) -> dict:
    """The child's environment, with the parent's importable paths preserved.

    The harness runs under a fixture that replaces HOME, and a fresh interpreter
    then recomputes `sys.path` from that fake HOME and loses
    `~/.local/lib/python3.12/site-packages` - so `httpx` disappears and `app.py`
    cannot be imported at all. That is why the other subprocess harnesses here
    pass PYTHONPATH explicitly. This forwards every existing entry instead of
    hardcoding a Python version.
    """
    env = dict(os.environ)
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    env["XDG_RUNTIME_DIR"] = str(runtime)
    existing = [p for p in sys.path if p and pathlib.Path(p).is_dir()]
    env["PYTHONPATH"] = os.pathsep.join([str(_PKG), *existing])
    return env


def _have_display() -> bool:
    from shani_chronoa import screengrab

    return screengrab.display_environment() != "none"


needs_display = pytest.mark.skipif(
    not _have_display(),
    reason="GTK cannot construct a widget without a display server",
)


@pytest.fixture(scope="module")
def started_app(tmp_path_factory):
    """The real application, started the real way, reported from inside itself."""
    work = tmp_path_factory.mktemp("presenter")
    runtime = work / "runtime"
    runtime.mkdir()
    os.chmod(runtime, 0o700)
    proc = subprocess.run(
        [sys.executable, "-c", _HARNESS],
        capture_output=True, text=True, timeout=600,
        env=_child_env(runtime), cwd=str(work),
    )
    payload = None
    for line in proc.stdout.splitlines():
        if line.startswith("RESULT"):
            payload = json.loads(line[len("RESULT"):])
    assert payload is not None, (
        f"the real app produced no result (rc={proc.returncode}):\n"
        f"{proc.stdout}\n{proc.stderr[-3000:]}"
    )
    payload["_returncode"] = proc.returncode
    payload["_stderr"] = proc.stderr
    return payload


@pytest.fixture(autouse=True)
def _no_leaked_presenter():
    ask_bridge.set_presenter(None)
    permissions.clear()
    yield
    ask_bridge.set_presenter(None)
    permissions.clear()


# --- the defect itself ------------------------------------------------------

class TestTheApplicationInstallsAPresenter:
    """`ask_bridge` is inert until something installs a presenter."""

    @needs_display
    def test_startup_alone_is_enough(self, started_app):
        """The defect in one assertion: nothing installed a presenter.

        `has_presenter()` is `_presenter is not None`, so this was False in every
        real run, `can_ask()` was therefore always False, `permissions.decide()`
        was unreachable, and every gated tool silently refused instead of asking.
        """
        assert started_app["before_run"] is False, (
            "a presenter was already installed before the app ran, so this "
            "harness cannot prove startup installed it"
        )
        assert started_app["after_startup"] is True, (
            "ChronoaApplication.do_startup never installed a presenter: "
            "permissions.can_ask() is False in the real app, so "
            "permissions.decide() never runs and every consent-gated tool "
            "refuses without ever offering to ask"
        )
        assert started_app["can_ask"] is True

    @needs_display
    def test_the_settings_window_is_not_what_installs_it(self, started_app):
        """Placement is load-bearing, and this is what makes it so.

        The settings window is built lazily on the first Ctrl+, so a presenter
        installed from `_open_settings` would not exist for a normal run - dead
        code behind a rarer one, which is exactly the shape of defect being
        repaired here. `do_startup` is the only hook that runs on every launch.
        """
        assert started_app["settings_window_at_startup"] is False, (
            "the settings window already existed at startup, so this test can "
            "no longer tell do_startup apart from _open_settings"
        )
        assert started_app["after_startup"] is True

    @needs_display
    def test_the_presenter_exists_before_the_window_does(self, started_app):
        """Startup precedes `do_activate`, which is what builds the window.

        So the prompt path is armed on every launch instead of depending on the
        window having been built - and a question asked in that gap is answered
        "nobody is here" rather than waiting for a prompt that cannot appear.
        """
        assert started_app["window_before_run"] is False
        assert started_app["window_at_startup"] is False, (
            "the window already existed during do_startup, so the presenter's "
            "window getter is not being exercised against a window that does "
            "not exist yet"
        )
        assert started_app["window_in_stage2"] is True
        assert started_app["window_type"] == "ChronoaWindow", (
            "the prompt was put on something other than the application's own "
            f"window (got {started_app['window_type']})"
        )


# --- the threading contract -------------------------------------------------

class TestTheQuestionCrossesTheThreadBoundary:
    @needs_display
    def test_the_real_window_shows_the_real_question(self, started_app):
        assert started_app["prompt_shown"] is True, (
            "the idle_add callback never reached show_question, so nothing was "
            "put on screen"
        )
        assert started_app["row_visible"] is True
        assert started_app["button_labels"] == _CHOICES, (
            "the prompt did not offer the three real permission answers, so a "
            "click could never return what permissions.decide() compares"
        )

    @needs_display
    def test_the_caller_is_blocked_until_the_user_answers(self, started_app):
        """The wait is the feature, not a delay to be optimised away.

        `ask_bridge`'s own docstring is explicit that the alternative - a skill
        shelling out, or a file the GUI polls - turns a turn into a timeout race,
        and that the caller blocks on an event only the user's click can set. A
        presenter returning an already-set event would pass every "the tool ran"
        assertion while making the prompt meaningless.
        """
        assert started_app["blocked_before_click"] is True, (
            "ask() returned before the click, so the prompt and the answer are "
            "not connected at all"
        )

    @needs_display
    def test_a_click_on_the_real_button_resolves_the_event(self, started_app):
        assert started_app["answer"] == _CHOICES[1], (
            "clicking the real button did not resolve the real event with that "
            f"option (got {started_app['answer']!r})"
        )
        assert started_app["row_hidden_after"] is True, (
            "the prompt stayed on screen after it was answered"
        )
        assert started_app["pending_cleared_after"] is True

    @needs_display
    def test_the_child_itself_was_healthy(self, started_app):
        """A control on the harness. Every assertion above is meaningless if the
        application the results came from exited badly."""
        assert started_app["_returncode"] == 0, started_app["_stderr"][-2000:]


class TestEveryWayOfNotAnsweringIsNotAChoice:
    """`""` is "no answer was given". Never a choice, never a grant."""

    @needs_display
    def test_the_window_going_away_answers_no_answer(self, started_app):
        """The dismissal path, and the one that was a full-timeout hang.

        Quitting with a prompt up leaves nobody who will ever click it. The tool
        loop is blocked on that event and `do_shutdown` joins its thread, so an
        unanswered event is a quit that looks frozen.
        """
        assert started_app["second_shown"] is True, (
            "the second prompt was never shown, so the quit path was not "
            "exercised against a real pending question"
        )
        assert started_app["second_answer"] == "", (
            "quitting with a prompt on screen resolved it as something other "
            f"than no answer (got {started_app['second_answer']!r}); for a "
            "permission prompt that would be a grant nobody gave"
        )
        assert started_app["quit_seconds"] < 8.0, (
            "quitting with a prompt up took "
            f"{started_app['quit_seconds']}s, which is the decision timeout "
            "being paid by a user who already quit"
        )


# --- the presenter itself, without a display --------------------------------

def _drain(seconds: float = 0.2) -> None:
    """Let any queued `GLib.idle_add` callback run.

    The presenter hands its widget work to the main loop, so a callback queued by
    one test can otherwise fire inside the next one. Ten seconds of nothing is
    exactly how a broken hand-off looks, so this never fails - it only isolates.
    """
    ctx = GLib.MainContext.default()
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        if not ctx.iteration(False):
            time.sleep(0.005)


def _ask_on_a_worker(make_call, timeout: float = 30.0):
    """Run a blocking `ask()`-style call off-thread while this thread pumps.

    `GLib.idle_add` only fires if a main loop is being iterated, and `ask()`
    blocks whoever calls it - so the pump and the ask have to be on different
    threads, which is the real arrangement in the application. Calling `ask()`
    inline and waiting for the idle callback would deadlock until the timeout,
    and that is a true statement about the code rather than a test artefact: the
    first version of this file did it and burned 150s proving it.
    """
    answers: list = []
    threading.Thread(
        target=lambda: answers.append(make_call()), daemon=True).start()
    ctx = GLib.MainContext.default()
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline and not answers:
        if not ctx.iteration(False):
            time.sleep(0.005)
    return answers[0] if answers else None


class TestThePresenterContract:
    """Shape of the hand-off: one event, one direction, `""` on every failure.

    In-process because these assertions are about the contract rather than about
    pixels. They cover nothing a person would see; the display-bound tests above
    drive the real widgets for that.
    """

    @pytest.fixture(autouse=True)
    def _drain_between_tests(self):
        _drain()
        yield
        _drain()

    def test_a_missing_window_answers_immediately_with_no_answer(self):
        from shani_chronoa.gui import make_question_presenter

        ask_bridge.set_presenter(make_question_presenter(lambda: None))
        started = time.monotonic()
        answer = _ask_on_a_worker(
            lambda: ask_bridge.ask("q?", ["a", "b"], timeout=30.0))
        assert answer == ""
        assert time.monotonic() - started < 1.0, (
            "a question with no window waited, so a headless-shaped call would "
            "hold the tool loop for the full decision timeout"
        )

    def test_the_event_is_a_real_event_from_make_event(self):
        """Not an invented event with the answer stashed somewhere else.

        `ask_bridge` reads the answer with `getattr(done, "chronoa_answer", "")`
        and nothing else, so an event without that attribute is a permanent "no
        answer" no matter what the user clicks.
        """
        from shani_chronoa.gui import make_question_presenter

        ask_bridge.set_presenter(make_question_presenter(lambda: object()))
        done = ask_bridge._presenter("q?", ["a", "b"])
        assert isinstance(done, threading.Event)
        assert hasattr(done, "chronoa_answer")
        _drain()

    def test_the_widget_work_is_deferred_to_the_main_loop(self):
        """`present()` must return before any widget exists.

        This is here because of a mutation that stayed green. Replacing the
        `GLib.idle_add` with a direct `window.show_question(...)` on the calling
        thread leaves every other test in this file passing - off-thread widget
        construction is *undefined* behaviour, not reliably broken behaviour, and
        on this build it does not visibly break. A suite that read as coverage of
        the threading contract and was not is worse than no coverage.

        What is not undefined is the hand-off itself: the caller must get its
        event back before the main loop has touched a widget, or the two threads
        share the widget tree. That is observable, and it is what this asserts.
        """
        from shani_chronoa.gui import make_question_presenter

        class _Watcher:
            def __init__(self):
                self.called = threading.Event()

            def show_question(self, *_args):
                self.called.set()

        window = _Watcher()
        ask_bridge.set_presenter(make_question_presenter(lambda: window))
        done = ask_bridge._presenter("q?", ["a", "b"])
        assert isinstance(done, threading.Event)
        assert not window.called.is_set(), (
            "show_question ran before present() handed back the event, so the "
            "widget tree was touched on the tool loop's thread rather than the "
            "GTK thread"
        )
        _drain(0.5)
        assert window.called.is_set(), (
            "the deferred widget work never ran on the main loop, so nothing "
            "would ever be put on screen"
        )

    def test_a_prompt_that_cannot_be_shown_is_not_a_choice(self):
        """A window that raises must not leave the caller waiting."""
        from shani_chronoa.gui import make_question_presenter

        class _Broken:
            def show_question(self, *_args):
                raise RuntimeError("the window went away")

        ask_bridge.set_presenter(make_question_presenter(lambda: _Broken()))
        assert _ask_on_a_worker(
            lambda: ask_bridge.ask("q?", ["a", "b"], timeout=30.0)) == ""

    def test_only_the_three_offered_choices_are_taken_as_an_answer(self):
        """Anything else - including what the user *said* - stays no answer.

        `show_question`'s second path resolves with the raw typed or spoken text,
        which will essentially never equal one of the three choice strings. If
        that were treated as an answer, a user saying "later" would grant a
        permission.
        """
        from shani_chronoa.gui import make_question_presenter

        class _Spoken:
            def show_question(self, _question, _options, resolve):
                resolve("maybe later")

        ask_bridge.set_presenter(make_question_presenter(lambda: _Spoken()))
        granted = _ask_on_a_worker(
            lambda: permissions.decide("screenshot", None,
                                       "vision-sense-enabled"),
            timeout=permissions.DECISION_TIMEOUT_SECONDS + 5.0)
        assert granted is None
        assert permissions.evaluate("screenshot", "*") == \
            permissions.Decision.DENY_SESSION, (
            "a refusal that was not one of the three choices was not recorded, "
            "so the same question would be put again this session"
        )


# --- nobody to ask must say so -----------------------------------------------

def _gated_tools() -> list:
    from shani_chronoa.skills import discover_skills
    from shani_chronoa.tools import _consent_key_for

    tools, handlers = discover_skills()
    out = []
    for entry in tools:
        name = (entry.get("function") or {}).get("name")
        key = _consent_key_for(name)
        if key:
            out.append((name, key, handlers[name]))
    return out


class TestNobodyToAskSaysNobodyIsThere:
    """`can_ask()`'s contract, which is what makes its False case harmless.

    The docstring says the gate exists "to let the skill produce its own refusal
    - which names the consent key the user could turn on - instead of replacing
    a useful message with 'the user did not allow'". That matters for the paths
    that are legitimately headless: the MCP server (stdio-only, same-user
    trusted, per `mcp.py`'s own trust model) and unattended trigger runs.

    The contract is only true if the refusal actually names the key, for every
    gated tool. That is the assertion that goes red if one of them ever degrades
    into a bare "the user did not allow".
    """

    def test_the_registry_is_not_silently_empty(self, gsettings_env):
        """A control that cannot fail is not a control."""
        gated = _gated_tools()
        assert len(gated) >= 20, (
            f"only {len(gated)} gated tools were found, so the sweep below "
            "would prove nothing"
        )

    def test_every_gated_tool_names_its_consent_key_with_nobody_to_ask(
        self, gsettings_env
    ):
        import importlib

        from shani_chronoa.config import ChronoaConfig

        ask_bridge.set_presenter(None)
        assert permissions.can_ask() is False, (
            "the harness is not actually exercising the nobody-to-ask case"
        )

        config = ChronoaConfig()
        gated = _gated_tools()
        # The ones that gate inline in `_run` rather than through a `_consent`
        # helper - they consult `config.sense_allowed(...)` or a sense property
        # directly, which is the documented precedent (`screenshot.py` does the
        # same). Each is called with arguments that would otherwise *act* - a
        # Ctrl+C, a real capture, a real notification, a real outbound request
        # - so a gate that had quietly stopped working would do the thing
        # instead of refusing it, which is the failure this is looking for.
        #
        # This list has to be completed whenever a skill starts gating inline:
        # an unlisted one is reported as "no _consent() gate and no probe",
        # which reads as a missing gate rather than a missing entry here.
        probes = {
            "list_wifi_networks": {},
            "notify": {"summary": "consent sweep probe"},
            "press_key": {"key": "ctrl+c"},
            "screenshot": {},
            "maps": {"action": "find", "place": "the consent sweep probe"},
            "news": {"topic": "the consent sweep probe"},
            "take_photo": {},
        }

        problems = []
        for name, key, handler in gated:
            config.set(key, "false")
            module = importlib.import_module(handler.__module__)
            gate = getattr(module, "_consent", None)
            if gate is not None:
                allowed, reason = gate(config)
                if allowed or key not in reason:
                    problems.append(f"{name}: {reason!r}")
                continue
            probe = probes.get(name)
            if probe is None:
                problems.append(f"{name}: no _consent() gate and no probe")
                continue
            reason = module._run(probe)
            if key not in reason:
                problems.append(f"{name}: {reason!r}")

        assert problems == [], (
            "with nobody to ask, these tools did not produce a refusal naming "
            "the consent key the user could turn on:\n  "
            + "\n  ".join(problems)
        )
