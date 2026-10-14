"""A refusal records what was actually said, and a silence records nothing.

The prompt has always promised two things that the shipped app did not keep:

- **`ApprovalRequest.reject_line`** - "Saying no tells Chronoa why if you add a
  reason, so it can do something else instead."
- **`ApprovalRequest.escape_line`** - "Closing this question without answering
  counts as no, never as yes."

Both were half true. Measured through the real window, the real presenter and
the real 120-second `DECISION_TIMEOUT_SECONDS`:

| what the person did | `permissions.reasons()` | `permissions.rules()` |
|---|---|---|
| clicked "No, don't allow" | `{}` | `[('delete_file', path, 'deny_session')]` |
| **typed a reason and pressed Enter** | **`{}`** | `[('delete_file', path, 'deny_session')]` |
| **walked away / timed out** | `{}` | **`[('delete_file', path, 'deny_session')]`** |
| **the window closed with the prompt up** | `{}` | **`[('delete_file', path, 'deny_session')]`** |

So a reason could not reach `reasons()` by any route a person actually has -
the window's question row is buttons, and a button cannot carry a sentence.
And the absence of an answer became a **session-wide refusal** carrying the
word "the user", with a Settings row reading "refused" that was never pressed
and a `permits()` sentence telling the model *"a rule ... says deny_session"*
about a rule no user made.

Both had the same shape: an answer the person did not give was recorded as
one they did.

## What is pinned here

Driven through the **real** `ChronoaApplication`, the real
`questions.make_question_presenter` and the real `window.show_question` buttons
- a click, the composer, and a dismissal - so this is the shipped path and not a
hand-built presenter that could agree with a broken parser.

Plus the parser itself, because a witness that only drives the window cannot
tell "the reason is dropped here" from "the reason never arrives".

**Not pinned:** anything about how the prompt *looks*, and whether the
composer's placeholder invites a reason. The question row is not renderable on
this box - `render_ui.py`'s `composer-question` target drives
`gui/questions.py` (the bare `ask_user` label) rather than `gui/asking.py` (the
interactive presenter with buttons), which is a gap in the harness rather than a
claim about this code.
"""

from __future__ import annotations

import json
import os
import pathlib
import subprocess
import sys
import textwrap

import pytest

_REPO = pathlib.Path(__file__).resolve().parents[1]
_PKG = _REPO / "usr" / "lib" / "shani-chronoa"
if str(_PKG) not in sys.path:
    sys.path.insert(0, str(_PKG))

from shani_chronoa import ask_bridge, permissions  # noqa: E402

#: A destructive, bypass-immune action, so no session-grant shortcut applies and
#: the prompt really is the four-option one `tools.py` asks for.
ACTION = "delete_file"
RESOURCE = "/tmp/chronoa-refusal-witness/doomed.txt"
KEY = "file-delete-enabled"

# ---------------------------------------------------------------------------
# The parser, without a display.
# ---------------------------------------------------------------------------


class TestWhatCountsAsAnAnswer:
    """`parse_reply()` is the one seam where a reason was dropped."""

    @pytest.mark.parametrize("answer", ["", "   ", "\n", None, 42, object()])
    def test_nothing_at_all_is_no_answer(self, answer):
        reply = permissions.parse_reply(answer)
        assert reply.reply == permissions.Decision.NO_ANSWER, (
            f"{answer!r} was read as {reply.reply!r}"
        )
        assert reply.is_refusal, "silence must still refuse the call"
        assert not reply.was_answered

    @pytest.mark.parametrize("said", [
        "no, use the trash instead",
        "not on this machine",
        "close",
        "cancelled_by_window",
        "later",
    ])
    def test_words_that_are_not_an_option_are_a_refusal_carrying_them(self, said):
        reply = permissions.parse_reply(said)
        assert reply.reply == permissions.Decision.DENY_SESSION
        assert reply.was_answered, (
            "words a person actually said must count as an answer, or they "
            "cannot be kept as a reason"
        )
        assert said in reply.message, (
            f"{said!r} was not kept as the reason: {reply.message!r}"
        )

    def test_the_options_themselves_are_unaffected(self):
        """The controls: a real answer parses exactly as it did before."""
        assert permissions.parse_reply(
            permissions.ALLOW_ONCE_CHOICE).reply == permissions.Decision.ALLOW_ONCE
        assert permissions.parse_reply(
            permissions.ALLOW_SESSION_CHOICE).reply \
            == permissions.Decision.ALLOW_SESSION
        assert permissions.parse_reply(
            permissions.CANCEL_CHOICE).reply == permissions.Decision.CANCEL
        assert permissions.parse_reply(
            permissions.FEEDBACK_CHOICE).reply == permissions.Decision.FEEDBACK

    def test_a_click_on_no_records_no_reason_of_its_own(self):
        """The over-fix of `words are kept`: the button's own label.

        The fallback keeps the whole answer as the reason, so the option that
        *is* an answer has to be handled before it. Without that, a plain click
        records "No, don't allow" as what the user said.
        """
        assert permissions.parse_reply(
            permissions.DENY_CHOICE).message == ""

    def test_a_reason_after_the_separator_still_round_trips(self):
        reply = permissions.parse_reply(
            permissions.encode_rejection("use the trash instead"))
        assert reply.reply == permissions.Decision.DENY_SESSION
        assert reply.message == "use the trash instead"


class TestWhatIsRemembered:
    """`decide()` through the real function, with a presenter, no GTK needed."""

    @pytest.fixture(autouse=True)
    def _clean(self):
        permissions.clear()
        ask_bridge.set_presenter(None)
        yield
        ask_bridge.set_presenter(None)
        permissions.clear()

    @staticmethod
    def _presenter(answer):
        """A presenter shaped like `ask_bridge`'s contract, not like a call.

        `make_event()` returns the **pair** `(event, resolve)` - the answer lives
        on the event object, which is the only place `ask_bridge` reads it from.
        Taking `[0]` and calling a method on the event raises, and
        `ask_bridge` catches it and answers `""`: a test whose fixture raises is
        a test measuring the catch.
        """
        def presenter(question, options):
            event, resolve = ask_bridge.make_event()
            resolve(answer)
            return event
        return presenter

    def _ask(self, answer, **kwargs):
        ask_bridge.set_presenter(self._presenter(answer))
        return permissions.decide(ACTION, RESOURCE, KEY,
                                  describe="Delete a file", **kwargs)

    def test_a_click_on_no_is_recorded(self):
        """The half that must survive, so the split cannot be over-applied."""
        assert self._ask(permissions.DENY_CHOICE, offer_cancel=True) is None
        assert permissions.evaluate(ACTION, RESOURCE) == \
            permissions.Decision.DENY_SESSION

    def test_a_typed_reason_is_recorded_as_a_reason(self):
        self._ask("no, use the trash instead", offer_cancel=True)
        assert permissions.rejection_reason(ACTION, RESOURCE) == \
            "no, use the trash instead"
        assert permissions.evaluate(ACTION, RESOURCE) == \
            permissions.Decision.DENY_SESSION

    def test_the_reason_reaches_the_model_on_the_next_attempt(self):
        """**The point of the whole reason field.** `permits()` is what the model
        reads, and it is the *only* copy that reaches it.

        Measured before the fix, `permits()` said "Permission denied ... a rule
        ... says deny_session. Do something else instead" with no reason at all,
        so the model was told a refusal and given nothing to adapt to.
        """
        self._ask("use the trash, not rm", offer_cancel=True)
        allowed, said = permissions.permits(ACTION, RESOURCE)
        assert allowed is False
        assert "The user said: use the trash, not rm" in said, (
            f"the reason did not reach the model: {said!r}"
        )

    def test_nothing_at_all_records_nothing(self):
        """The shipped defect: a dismissal became a session-wide refusal."""
        assert self._ask("") is None
        assert permissions.evaluate(ACTION, RESOURCE) == \
            permissions.Decision.FALL_THROUGH
        assert permissions.rules() == []
        assert permissions.reasons() == {}

    def test_a_non_string_answer_records_nothing(self):
        """A presenter returning the wrong type is `ask_bridge`'s documented
        "no answer", so it must take the same route as `""`."""
        ask_bridge.set_presenter(lambda q, o: "a bare string, not an event")
        assert permissions.decide(ACTION, RESOURCE, KEY) is None
        assert permissions.evaluate(ACTION, RESOURCE) == \
            permissions.Decision.FALL_THROUGH

    def test_the_next_attempt_after_a_dismissal_asks_again(self):
        """The behaviour that replaces the record, stated as what it is.

        Not "the same question is not put twice" - that is what the old
        `test_a_dismissal_is_recorded_so_it_is_not_re_asked` asserted, and it is
        bounded elsewhere: `assistant.DEFAULT_TOOL_ATTEMPTS_PER_ROUND` is
        `LOOP_THRESHOLD` (5) and "limit" is in every tool's allowed failure
        types, so the tool loop stops calling it regardless. What the record
        bought was a tool dead for the session after a two-minute absence.
        """
        ask_bridge.set_presenter(lambda q, o: ask_bridge.make_event()[0])
        permissions.DECISION_TIMEOUT_SECONDS = 0.05
        try:
            assert permissions.decide(ACTION, RESOURCE, KEY) is None
            # No rule, so the next call reaches the question again rather than
            # finding a refusal on record.
            assert permissions.evaluate(ACTION, RESOURCE) == \
                permissions.Decision.FALL_THROUGH
        finally:
            permissions.DECISION_TIMEOUT_SECONDS = 120.0

    def test_a_dismissal_still_refuses_the_tool_call(self):
        """The safe direction, through the dispatcher rather than `decide()`.

        `decide()` returning `None` is the refusal; this is the sentence a model
        reads, and it must not have changed.
        """
        from shani_chronoa.tools import execute_tool_outcome

        ask_bridge.set_presenter(self._presenter(""))
        out = execute_tool_outcome("screenshot", {}).text
        assert "did not allow" in out
        assert "Screenshot saved" not in out

    def test_a_chosen_option_is_remembered_even_when_its_text_never_arrives(self):
        """**The boundary of the split, and the mistake the split invites.**

        "Provide feedback" is an option; a person pressed it. The guidance box
        behind it produced nothing - a missing text presenter, one that raised,
        or one that returned the wrong type - and the call is refused.

        The refusal is still **recorded**, because the rule this change draws is
        *no option chosen -> record nothing*, not *no words reached `_reasons` ->
        record nothing*. A click on "No, don't allow" collects no reason either
        and is recorded all the same, so the two would disagree on the only
        thing that matters: whether a person said no.

        This is the over-reach the split invites, and it cost a real revert while
        this was written: the branch was changed first and the existing test
        caught it.
        """
        ask_bridge.set_presenter(self._presenter(permissions.FEEDBACK_CHOICE))
        ask_bridge.set_text_presenter(lambda prompt, placeholder: None)
        try:
            assert self._ask(permissions.FEEDBACK_CHOICE, offer_cancel=True) is None
        finally:
            ask_bridge.set_text_presenter(None)
        assert permissions.evaluate(ACTION, RESOURCE) == \
            permissions.Decision.DENY_SESSION, (
            "a chosen option stopped being remembered because its follow-up "
            "text box never answered, which is our bug and not their absence"
        )

    def test_a_hand_recorded_no_answer_still_refuses_and_says_what_it_is(self):
        """`permits()` fails closed on `NO_ANSWER`, and does not fake a denial.

        `decide()` writes none of these, so reaching one means somebody called
        `add_rule()` directly. The safe direction must not depend on who wrote
        the rule - and the sentence must not claim a user refused something the
        user never saw, which is the defect itself wearing a different route.
        """
        permissions.add_rule(ACTION, RESOURCE, permissions.Decision.NO_ANSWER,
                             session_only=True)
        allowed, said = permissions.permits(ACTION, RESOURCE)
        assert allowed is False, "a no-answer rule allowed the call"
        assert "not answered" in said, (
            f"the refusal claims something the user did not do: {said!r}"
        )
        assert "denied" not in said, (
            f"a no-answer was reported as a denial: {said!r}"
        )


class TestThePromptKeepsItsPromises:
    """Both sentences are obligations on the prompt's *content*."""

    def _ask(self):
        return permissions.approval_request(ACTION, RESOURCE, KEY)

    def test_it_says_a_dismissal_is_never_an_allow(self):
        line = self._ask().escape_line
        assert "counts as no" in line and "never as" in line

    def test_it_says_a_dismissal_is_not_remembered(self):
        """The half that was false while the first was true."""
        assert "nothing is remembered" in self._ask().escape_line

    def test_it_says_a_reason_can_be_given(self):
        assert "reason" in self._ask().reject_line.lower()

    def test_it_names_where_to_type_it(self):
        """A promise with no route is the defect this file exists for.

        The window's question row is buttons; the composer at the bottom of the
        main window is the only place a sentence can be typed, and the question
        puts its own placeholder up there. Saying so is what turns the promise
        into something a person can act on.
        """
        assert "type or say it" in self._ask().reject_line.lower()


# ---------------------------------------------------------------------------
# The same three things through the real window.
# ---------------------------------------------------------------------------

_HARNESS = textwrap.dedent(
    """
    import json, sys, threading, time
    import gi
    gi.require_version("Gtk", "4.0")
    from gi.repository import Gtk, GLib, Gdk

    from shani_chronoa import permissions
    from shani_chronoa.app import ChronoaApplication

    ACTION = "delete_file"
    RESOURCE = "/tmp/chronoa-refusal-witness/doomed.txt"
    KEY = "file-delete-enabled"

    out = {}

    def pump(predicate, timeout=25.0):
        ctx = GLib.MainContext.default()
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if predicate():
                return True
            ctx.iteration(False)
            time.sleep(0.01)
        return bool(predicate())

    def buttons(window):
        if len(window._question_widgets) < 2:
            return []
        got, child = [], window._question_widgets[1].get_first_child()
        while child is not None:
            got.append(child)
            child = child.get_next_sibling()
        return got

    def ask(window, label):
        answers = []
        def run():
            answers.append(permissions.decide(ACTION, RESOURCE, KEY,
                                              describe="Delete a file",
                                              offer_cancel=True))
        threading.Thread(target=run, daemon=True).start()
        shown = pump(lambda: window._pending_question is not None)
        out[label + "_prompt_shown"] = shown
        out[label + "_options"] = [b.get_label() for b in buttons(window)]
        return answers

    def snapshot(label, answers):
        out[label + "_decided"] = answers[0] if answers else "STILL BLOCKED"
        out[label + "_reasons"] = {
            f"{k[0]}|{k[1]}": v for k, v in permissions.reasons().items()}
        out[label + "_rules"] = [list(r) for r in permissions.rules()]
        out[label + "_evaluate"] = permissions.evaluate(ACTION, RESOURCE)
        out[label + "_permits"] = str(permissions.permits(ACTION, RESOURCE))

    def stage(app):
        window = app.window

        # 1. A click on "No, don't allow" - the shipped refusal.
        answers = ask(window, "click")
        for b in buttons(window):
            if b.get_label() == permissions.DENY_CHOICE:
                b.emit("clicked")
                break
        pump(lambda: answers != [])
        snapshot("click", answers)

        # 2. The same refusal with a reason typed into the composer. Enter goes
        #    through the window's own `_submit_input`, so this is the route a
        #    person takes, not a call to the presenter.
        permissions.clear()
        answers = ask(window, "typed")
        window._set_input_text("no, use the trash instead")
        out["typed_composer_holds"] = window.get_input_text()
        window._on_input_key_pressed(None, Gdk.KEY_Return, 0, 0)
        pump(lambda: answers != [])
        snapshot("typed", answers)

        # 3. Nobody answering: the window goes away with the prompt up, which is
        #    `application.py`'s own shutdown path.
        permissions.clear()
        answers = ask(window, "walkaway")
        window.abandon_pending_question()
        pump(lambda: answers != [])
        snapshot("walkaway", answers)

        app.quit()
        return False

    app = ChronoaApplication()
    GLib.idle_add(stage, app)
    app.run([])
    print("RESULT" + json.dumps(out))
    sys.stdout.flush()
    """
)


def _child_env(runtime: pathlib.Path) -> dict:
    """The child's environment, with the parent's importable paths preserved.

    A fresh interpreter recomputes `sys.path` from a replaced `HOME` and loses
    `site-packages`, so `httpx` disappears and the application cannot be
    imported at all. Every existing entry is forwarded rather than hardcoded to
    a Python version.

    **`XDG_RUNTIME_DIR` is deliberately NOT pointed at `runtime`.** Other
    harnesses here do that, and it works for them because their display is X11,
    where `DISPLAY` carries the socket. Broadway's socket is a file in GLib's
    runtime directory, so redirecting it makes GDK find no display, GTK refuses
    to initialise, `app.run([])` returns immediately and the child prints
    `RESULT {}` - which the fixture below reads as a real result with no keys in
    it, so every assertion below fails with a `KeyError` that points at the test
    rather than at the harness. `runtime` is still made and still passed in, so
    the child has a private directory to work in; it is only the runtime
    variable that is left alone.
    """
    env = dict(os.environ)
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    existing = [p for p in sys.path if p and pathlib.Path(p).is_dir()]
    env["PYTHONPATH"] = os.pathsep.join([str(_PKG), *existing])
    return env


def _have_display() -> bool:
    """Whether *any* GTK backend is advertised.

    **All three names are checked, and `screengrab.display_environment()` is not
    used** - it reads `DISPLAY` and `WAYLAND_DISPLAY` only, so it answers
    `"none"` for a broadway session, which is a display this suite runs on and
    the one it is measured on. A guard that skips the tests on the only backend
    available reads as coverage and is nothing of the kind.

    Deliberately a *probe*, not a verdict: it cannot tell whether a window can
    actually be built. The fixture below is what tells - a backend that is
    advertised and cannot render produces no `RESULT` line, which fails loudly
    rather than skipping. That split is the point: absence of a display is a
    skip, absence of a result is a failure.
    """
    return any(os.environ.get(name, "").strip() for name in
               ("BROADWAY_DISPLAY", "WAYLAND_DISPLAY", "DISPLAY"))


needs_display = pytest.mark.skipif(
    not _have_display(),
    reason="no GTK backend is advertised (DISPLAY, WAYLAND_DISPLAY, "
           "BROADWAY_DISPLAY all unset)",
)


@pytest.fixture(scope="module")
def real_window(tmp_path_factory):
    """The real application, started the real way, reporting from inside itself."""
    work = tmp_path_factory.mktemp("refusal-witness")
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
    # **`RESULT {}` is not a result.** The child prints whatever `out` holds
    # after `app.run([])` returns, and that call returns immediately when GTK
    # could not open a display - so a harness that did nothing produces a
    # well-formed empty JSON line that every assertion below reads as a real
    # measurement with its keys missing, failing with a `KeyError` that names
    # the test and not the cause. The absence of a measurement is the finding.
    assert payload, (
        "the real app reported nothing at all, so the window was never built "
        f"(rc={proc.returncode}). Most often: no display the client can reach.\n"
        f"{proc.stdout}\n{proc.stderr[-3000:]}"
    )
    missing = sorted(k for k in (
        "click_options", "click_decided", "click_reasons", "click_rules",
        "click_evaluate", "click_permits",
        "typed_composer_holds", "typed_decided", "typed_reasons", "typed_permits",
        "walkaway_decided", "walkaway_rules", "walkaway_evaluate",
    ) if k not in payload)
    assert not missing, (
        f"the harness ran but reported nothing for: {missing}. It reached the "
        f"window - the keys it did report were {sorted(payload)} - so one of "
        f"the three questions did not complete."
    )
    return payload


class TestThroughTheRealWindow:
    """The three rows of the table in the module docstring."""

    @needs_display
    def test_the_prompt_offers_the_answers_this_action_may_offer(self,
                                                                real_window):
        """**Three, not four** - and the missing one is the control.

        `ACTION` is `delete_file`, which is bypass-immune, and
        `options_with_cancel()` withholds "Allow for this session" for exactly
        those actions because the standing grant would be ignored anyway. So the
        prompt offering it would be a label promising something the mechanism
        cannot keep - the failure this module's own sibling (`tools.py`'s
        consent work) records as recurring.

        Asserted as an absence deliberately. Listing the four strings was my
        first version and it failed against correct code, which is the way this
        repository records: a fixture shaped unlike the real data tests the
        fixture.
        """
        options = real_window["click_options"]
        assert permissions.ALLOW_SESSION_CHOICE not in options, (
            "'Allow for this session' was offered for a bypass-immune action, "
            "where the grant would be ignored: it is a promise the mechanism "
            "cannot keep"
        )
        assert options == [
            permissions.ALLOW_ONCE_CHOICE,
            permissions.DENY_CHOICE,
            permissions.CANCEL_CHOICE,
            permissions.FEEDBACK_CHOICE,
        ], (
            "the prompt did not offer the options `tools.py` asks for, so "
            f"nothing below is the shipped answer: {options}"
        )

    @needs_display
    def test_a_click_is_refused_and_recorded(self, real_window):
        assert real_window["click_decided"] is None, "a click must refuse"
        assert real_window["click_rules"] == [
            [ACTION, RESOURCE, permissions.Decision.DENY_SESSION]], (
            f"a click stopped being recorded: {real_window['click_rules']}"
        )
        assert real_window["click_reasons"] == {}, (
            "a button press invented a reason: "
            f"{real_window['click_reasons']}"
        )

    @needs_display
    def test_a_typed_reason_reaches_the_prompt_and_then_the_model(self, real_window):
        """**The reason field, through the window's own composer and Enter.**

        Before the fix this was `{}` on every route a person has: the question
        row is buttons, and a button cannot carry a sentence.
        """
        assert real_window["typed_composer_holds"] == "no, use the trash instead"
        assert real_window["typed_decided"] is None, "a refusal with a reason still refuses"
        assert real_window["typed_reasons"] == {
            f"{ACTION}|{RESOURCE}": "no, use the trash instead"}, (
            "the typed reason did not reach reasons(): "
            f"{real_window['typed_reasons']}"
        )
        assert "The user said: no, use the trash instead" \
            in real_window["typed_permits"], (
            "the reason did not reach the model: "
            f"{real_window['typed_permits']}"
        )

    @needs_display
    def test_a_dismissal_refuses_and_records_nothing(self, real_window):
        """**The timeout half, through the window's own shutdown path.**

        Before the fix this wrote `deny_session`, so a person who closed the
        window mid-question came back to a tool refused for the session.
        """
        assert real_window["walkaway_decided"] is None, (
            "a dismissal must still refuse the call"
        )
        assert real_window["walkaway_rules"] == [], (
            "a dismissal left a rule behind, so the tool stays refused for the "
            f"session: {real_window['walkaway_rules']}"
        )
        assert real_window["walkaway_evaluate"] == \
            permissions.Decision.FALL_THROUGH
