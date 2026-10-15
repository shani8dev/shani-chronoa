"""A refusal the user cannot act on is not consent, it is a dead end.

Until this, a gated tool whose consent key was off simply returned "the vision
sense is turned off (enable 'vision-sense-enabled')". That is honest and it is
correct - but it leaves the only real decision with nobody able to make it. The
user could go and open Settings, or accept that this capability is off.

mini-swe-agent asks before running anything irreversible, and puts the ask where
the decision is actually made rather than in a prompt the model can talk past.
The same shape here: when a gate is shut *and* someone is present to answer, ask
them, once, and record what they said.

The two failure modes this module exists to prevent are both quiet:

- Asking when nobody can answer, which hangs the turn until a timeout.
- Replacing a useful refusal with a bare "the user did not allow", when there
  was no user - losing the one line that told them which setting to turn on.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

_REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_REPO / "usr" / "lib" / "shani-chronoa"))

from shani_chronoa import ask_bridge, permissions  # noqa: E402
from shani_chronoa.tools import execute_tool_outcome  # noqa: E402


def _presenter(choice: str, seen: list | None = None):
    """A presenter that answers `choice` immediately, recording the question."""
    def show(question, options):
        if seen is not None:
            seen.append((question, list(options)))
        done, resolve = ask_bridge.make_event()
        resolve(choice)
        return done
    return show


@pytest.fixture(autouse=True)
def a_screen_capture_tool(tmp_path, monkeypatch):
    """A `grim` that writes a real 2x2 PNG to stdout, first on PATH.

    These tests are about the approval flow, and they use `screenshot` as the
    gated tool. Without a capture tool on the host, "allow once" correctly ran
    the tool and the tool correctly said "no Wayland screen capture tool is
    installed" - so the two allow tests failed on any machine without grim or
    gnome-screenshot, for a reason that has nothing to do with permissions.
    An executable rather than a monkeypatch, because the skill runs in a
    sandboxed child that inherits PATH and not this process's patches.
    """
    import struct
    import zlib

    def chunk(kind, data):
        return (struct.pack(">I", len(data)) + kind + data
                + struct.pack(">I", zlib.crc32(kind + data) & 0xFFFFFFFF))

    png = (b"\x89PNG\r\n\x1a\n"
           + chunk(b"IHDR", struct.pack(">IIBBBBB", 2, 2, 8, 2, 0, 0, 0))
           + chunk(b"IDAT", zlib.compress(b"\x00" + b"\xff\x00\x00" * 2 + b"\x00" + b"\x00\xff\x00" * 2))
           + chunk(b"IEND", b""))
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    (bin_dir / "image.png").write_bytes(png)
    tool = bin_dir / "grim"
    tool.write_text(f"#!/bin/sh\nexec cat '{bin_dir / 'image.png'}'\n")
    tool.chmod(0o755)
    monkeypatch.setenv("PATH", f"{bin_dir}:{__import__('os').environ.get('PATH', '')}")
    # **And a display, because the tool is never reached without one.**
    # `screengrab.capture_screen` calls `display_environment()` first and
    # raises "there is no display to capture" when neither `WAYLAND_DISPLAY`
    # nor `DISPLAY` is set - so on a headless runner the fake `grim` above is
    # never executed and the two allow tests fail with *"no display"*, which
    # is about the machine and not about permissions at all.
    #
    # This is why they were on the environmental list as *"no Wayland
    # screen-capture tool"* when the actual cause was one level earlier and
    # fixable here: supplying the *tool* does not supply the *display*.
    #
    # `WAYLAND_DISPLAY` first, because `display_environment()` prefers it -
    # setting only `DISPLAY` on a Wayland desktop would make the code capture
    # the XWayland root, which is the mistake that function's own docstring
    # warns about, and it would do it in this test.
    monkeypatch.setenv("WAYLAND_DISPLAY", "wayland-test")


@pytest.fixture(autouse=True)
def clean_state():
    permissions.clear()
    ask_bridge.set_presenter(None)
    yield
    permissions.clear()
    ask_bridge.set_presenter(None)


class TestNobodyToAsk:
    def test_a_headless_run_refuses_without_asking(self):
        ask_bridge.set_presenter(None)
        out = execute_tool_outcome("screenshot", {}).text
        assert "not permitted" in out.lower() or "turned off" in out.lower()

    def test_headless_keeps_the_refusal_that_names_the_setting(self):
        """The bare "the user did not allow" loses the only actionable line.

        There was no user, so there is nothing to allow - reporting otherwise
        invents an actor, and throws away the instruction to turn the switch on.

        **This asserted the raw gsettings key until 2026-10-14**, which was the
        defect it names after: it checked that the refusal named
        `vision-sense-enabled` while the Settings row is titled "Take and
        describe photos". Both halves now, so a sentence naming both things at
        once cannot satisfy it.
        """
        ask_bridge.set_presenter(None)
        out = execute_tool_outcome("screenshot", {}).text
        assert "Take and describe photos" in out, out
        assert "vision-sense-enabled" not in out, (
            f"the refusal named the raw gsettings key, which no settings window "
            f"spells that way: {out!r}"
        )

    def test_can_ask_is_false_without_a_presenter(self):
        ask_bridge.set_presenter(None)
        assert permissions.can_ask() is False


class TestAllowing:
    def test_allow_once_runs_the_tool(self):
        ask_bridge.set_presenter(
            _presenter(permissions.ALLOW_ONCE_CHOICE))
        assert "Screenshot saved" in execute_tool_outcome("screenshot", {}).text

    def test_allow_once_does_not_leak_to_the_next_call(self):
        ask_bridge.set_presenter(_presenter(permissions.ALLOW_ONCE_CHOICE))
        execute_tool_outcome("screenshot", {})
        ask_bridge.set_presenter(None)
        second = execute_tool_outcome("screenshot", {}).text
        assert "not permitted" in second.lower() or "turned off" in second.lower()

    def test_allow_session_survives_the_prompt_gone(self):
        ask_bridge.set_presenter(
            _presenter(permissions.ALLOW_SESSION_CHOICE))
        first = execute_tool_outcome("screenshot", {}).text
        ask_bridge.set_presenter(None)
        second = execute_tool_outcome("screenshot", {}).text
        assert "Screenshot saved" in first
        assert "Screenshot saved" in second, (
            "a session grant evaporated once the presenter was removed"
        )


class TestRefusing:
    def test_an_explicit_no_does_not_run_the_tool(self):
        ask_bridge.set_presenter(_presenter(permissions.DENY_CHOICE))
        out = execute_tool_outcome("screenshot", {}).text
        assert "did not allow" in out
        assert "Screenshot saved" not in out

    def test_a_dismissed_prompt_counts_as_no(self):
        """Dismissed, timed out, and "no" are the same answer: nobody said yes."""
        ask_bridge.set_presenter(_presenter(""))
        out = execute_tool_outcome("screenshot", {}).text
        assert "did not allow" in out

    def test_a_refusal_is_not_asked_again(self):
        """The tool loop retries. A repeated question gets clicked through."""
        ask_bridge.set_presenter(_presenter(permissions.DENY_CHOICE))
        execute_tool_outcome("screenshot", {})
        seen: list = []
        ask_bridge.set_presenter(
            _presenter(permissions.ALLOW_SESSION_CHOICE, seen))
        execute_tool_outcome("screenshot", {})
        assert seen == [], (
            f"the same refusal was put to the user {len(seen)} more time(s) "
            f"after they had already answered it"
        )


#: The two halves of a permission prompt, as the *module* spells them. Derived
#: from `permissions`' own constants rather than typed out, because the shape
#: being asserted below is "every allow comes before every refusal" - a claim
#: about the partition, which a hand-copied list can only state by accident.
#:
#: **Both assertions below used to hold a positional literal and both rotted**,
#: for the same reason and in the same way this class's own comment already
#: recorded for `offer_cancel=True`: `options[:2] == [ONCE, SESSION]` became
#: false when `ALLOW_TURN_CHOICE` was added between them, and a failure that
#: *looks* like "the option list drifted" is indistinguishable from "the option
#: list is wrong". A prefix that breaks when a correct option is inserted is a
#: test that penalises the fix, which is why the property is stated instead.
_ALLOWS = (permissions.ALLOW_ONCE_CHOICE, permissions.ALLOW_TURN_CHOICE,
           permissions.ALLOW_SESSION_CHOICE)
_REFUSALS = (permissions.DENY_CHOICE, permissions.CANCEL_CHOICE)


def _leading_allows(options):
    """The run of options before the first refusal - i.e. what a glance sees
    first, and therefore everything a careless press could grant."""
    allows = set(_ALLOWS)
    run = []
    for option in options:
        if option in _REFUSALS or option not in allows:
            break
        run.append(option)
    return run


class TestWhatThePromptSays:
    def test_it_names_the_tool_and_the_consent_key(self):
        seen: list = []
        ask_bridge.set_presenter(_presenter(permissions.DENY_CHOICE, seen))
        execute_tool_outcome("screenshot", {})
        assert seen, "nothing was asked at all"
        question, options = seen[0]
        assert "screenshot" in question.lower()
        assert "vision-sense-enabled" in question
        # **Membership and order, not an exact list.** The exact list stopped
        # being true on 2026-10-06 when `tools.dispatch` began passing
        # `offer_cancel=True`, which appends "No, and stop this turn" - and the
        # failure was correct: the option should now be offered. Asserting the
        # whole list would have made adding it look like a regression. It then
        # rotted a second time in the same shape when the turn grant landed,
        # which is what `_ALLOWS` above is for.
        #
        # What this test is actually for is that the prompt *names the tool and
        # its consent key*, so that is still asserted above; here the claims are
        # that the allow choices come first and that a refusal is on offer.
        #
        # The first comparison is the load-bearing one: every allow-shaped
        # option appears in the leading run **and** the leading run holds
        # nothing else, so "an allow offered after a refusal" fails it. Either
        # half alone is satisfiable by a list that is all allows, then a denial,
        # then a second allow - which is the order this module's own
        # `options_with_cancel` docstring says it once got wrong.
        #
        # That order is not hypothetical, and it is the reason this is a
        # partition rather than a count. `options_with_cancel` places the
        # session grant with `base.insert(2, ALLOW_SESSION_CHOICE)` - a
        # **positional** insert whose correctness depends on `base` being
        # exactly the length it is. Measured here: dropping `ALLOW_TURN_CHOICE`
        # from that list alone moves the session grant to index 2, which is
        # *after* the denial, so the prompt would offer "Allow for this session"
        # below "No, don't allow". The shipped list is the right length, so this
        # is a guard on the coupling rather than a fix to it.
        assert options[0] == permissions.ALLOW_ONCE_CHOICE, options
        assert _leading_allows(options) == [o for o in options if o in _ALLOWS], (
            "an allow-shaped option is not in the leading run, or something that "
            f"is not an allow is: {options}")
        assert set(_ALLOWS) <= set(options), (
            f"a grant this prompt can honour is not on offer: {options}")
        assert permissions.DENY_CHOICE in options, options
        assert len(options) >= 3, options

    def test_the_refusal_is_offered_as_an_option_not_the_default(self):
        """Ordering carries meaning: the allow choices come first, but the
        outcome is identical for a dismissal, so nothing is granted by default
        and the caller must treat a non-match as no."""
        seen: list = []
        ask_bridge.set_presenter(_presenter("", seen))
        execute_tool_outcome("screenshot", {})
        options = seen[0][1]
        # **"not the default" was never about the index.** The claim is that a
        # dismissed prompt grants nothing, and that every refusal-shaped option
        # sits after every allow-shaped one. The last index used to hold the
        # refusal; it now holds "No, and stop this turn", which is if anything
        # more conservative, so an index assertion would have inverted the test.
        assert options[0] == permissions.ALLOW_ONCE_CHOICE, options
        refusals = set(_REFUSALS)
        assert refusals & set(options), options
        first_refusal = min(options.index(o) for o in refusals if o in options)
        assert set(options[:first_refusal]) == set(options) & set(_ALLOWS), (
            "the options before the first refusal are not exactly the ones that "
            f"grant: {options}")


class TestDecideRecordsWhatItWasTold:
    def test_allow_once_is_recorded_as_a_session_grant(self):
        ask_bridge.set_presenter(_presenter(permissions.ALLOW_ONCE_CHOICE))
        execute_tool_outcome("screenshot", {})
        assert permissions.session_grant("screenshot", "*") is None, (
            "the one-shot grant was not consumed by being used"
        )

    def test_a_refusal_is_recorded_so_the_question_stays_answered(self):
        ask_bridge.set_presenter(_presenter(permissions.DENY_CHOICE))
        execute_tool_outcome("screenshot", {})
        assert permissions.evaluate("screenshot", "*") == \
            permissions.Decision.DENY_SESSION


class _Asked(list):
    """The questions `decide()` put to the user, and each call's `preview`.

    A list, so `len(asked) == 1` reads as it always did, carrying the preview
    alongside rather than beside - because the double now sees a keyword the
    real `ask` grew, and a fixture that quietly swallowed it would be the
    "carries the field and does nothing with it" shape this repo keeps
    recording. `test_the_preview_reaches_ask` is what reads them.
    """

    def __init__(self):
        super().__init__()
        self.previews: list = []


class TestTheOptionsMeanWhatTheySay:
    """**"Allow for this session" asked again on the very next call.**

    `decide()` short-circuited on a rule already on record, but the set it
    checked was only the three *refusals*. A session **grant** was written to
    `_grants` and then never read, so choosing the option recorded a decision
    that changed nothing and put the same question back on screen immediately.

    Measured before the fix, with a presenter that always picks the session
    option: **three calls, three prompts**, two of them on the same channel, and
    `_grants` holding the same rule twice.

    The inbound gateway surfaced this first — a phone channel with a session
    grant re-prompted on every message — but it is not the gateway's bug, and
    `decide()` is shared by every tool, so the fix is here rather than there.

    `ALLOW_ONCE` is deliberately **not** short-circuited, and that is the test
    that stops this being over-fixed into "the prompt never appears".
    """

    @staticmethod
    def _answer(monkeypatch, choice):
        """Patch the two `ask_bridge` seams `decide()` uses, so no UI is needed."""
        from shani_chronoa import ask_bridge

        asked = _Asked()
        monkeypatch.setattr(ask_bridge, "has_presenter", lambda: True)

        # **`**kwargs`, not a hand-written signature.** `ask` grew `preview`
        # and `decide()` passes it by keyword, so a double written against the
        # old three parameters raised `TypeError: ... got an unexpected keyword
        # argument 'preview'` from inside `permissions.decide` - five failures
        # reading as a permissions defect rather than a double that predated the
        # signature. The same lesson as the `__defaults__` triples in
        # `tests/_ask.py`, on the other seam.
        def fake_ask(question, options, timeout=0.0, **kwargs):
            asked.append(question)
            asked.previews.append(kwargs.get("preview"))
            assert choice in options, (
                f"{choice!r} was not offered; the test would be asserting "
                f"behaviour nobody can reach: {options!r}")
            return choice

        monkeypatch.setattr(ask_bridge, "ask", fake_ask)
        return asked

    @staticmethod
    def _clear():
        from shani_chronoa import permissions
        permissions.clear(session_only=True)

    def test_a_session_grant_does_not_ask_again(self, monkeypatch):
        from shani_chronoa import permissions

        self._clear()
        asked = self._answer(monkeypatch, permissions.ALLOW_SESSION_CHOICE)
        try:
            results = [permissions.decide("act", "res", "key", "d")
                       for _ in range(3)]
        finally:
            self._clear()
        assert len(asked) == 1, (
            f"the person was asked {len(asked)} times after choosing "
            "'Allow for this session' - the option recorded a decision and then "
            "ignored it")
        assert results == [permissions.Decision.ALLOW_SESSION] * 3, results

    def test_allow_once_still_asks_every_time(self, monkeypatch):
        """"This once" means once. Without this, the fix above is a regression."""
        from shani_chronoa import permissions

        self._clear()
        asked = self._answer(monkeypatch, permissions.ALLOW_ONCE_CHOICE)
        try:
            results = [permissions.decide("act", "res", "key", "d")
                       for _ in range(3)]
        finally:
            self._clear()
        assert len(asked) == 3, (
            f"asked {len(asked)} times; 'Allow this once' must not suppress the "
            "question, or the grant outlives the thing it was granted for")
        assert results == [permissions.Decision.ALLOW_ONCE] * 3, results

    def test_a_denial_still_settles_it(self, monkeypatch):
        """The behaviour the short-circuit was originally written for."""
        from shani_chronoa import permissions

        self._clear()
        asked = self._answer(monkeypatch, permissions.DENY_CHOICE)
        try:
            results = [permissions.decide("act", "res", "key", "d")
                       for _ in range(3)]
        finally:
            self._clear()
        assert len(asked) == 1, asked
        assert results == [None, None, None], results

    def test_a_session_grant_is_scoped_to_its_own_resource(self, monkeypatch):
        """Answering for one channel must not answer for another."""
        from shani_chronoa import permissions

        self._clear()
        asked = self._answer(monkeypatch, permissions.ALLOW_SESSION_CHOICE)
        try:
            permissions.decide("act", "phone", "key", "d")
            permissions.decide("act", "phone", "key", "d")
            permissions.decide("act", "laptop", "key", "d")
        finally:
            self._clear()
        assert len(asked) == 2, (
            f"asked {len(asked)} times; the second channel must still be asked")

    def test_an_identical_rule_is_not_recorded_twice(self, monkeypatch):
        """One rule per decision, not one per call - measured growing per message."""
        from shani_chronoa import permissions

        self._clear()
        self._answer(monkeypatch, permissions.ALLOW_SESSION_CHOICE)
        try:
            for _ in range(5):
                permissions.decide("act", "res", "key", "d")
            recorded = [r for r in permissions.rules() if r[1] == "res"]
        finally:
            self._clear()
        assert len(recorded) == 1, (
            f"the same rule is recorded {len(recorded)} times: {recorded!r} - it "
            "grew by one entry per approved call for the whole session, and "
            "evaluate() walks the list on every check")

    def test_recording_an_identical_rule_preserves_order(self):
        """Replacing rather than appending must not reorder anything."""
        from shani_chronoa import permissions

        permissions.clear()
        try:
            permissions.add_rule("a", "*", "deny_once")
            permissions.add_rule("b", "*", "allow_once")
            permissions.add_rule("a", "*", "deny_once")   # duplicate, first entry
            assert permissions.rules() == [("a", "*", "deny_once"),
                                           ("b", "*", "allow_once")], (
                f"order changed: {permissions.rules()!r}")
        finally:
            permissions.clear()


class TestThePreviewReachesTheQuestion:
    """**The approval said what it would do, and the window was never told.**

    `ask_bridge.ask` grew a `preview` parameter, `approval_preview` works out
    what a gated call *will* do, and `tools._dispatch` hands the result to
    `permissions.decide`, which forwards it. Three hops.

    **The only witness for the chain was a double whose `**kwargs` swallowed
    the keyword** - and a double that swallows it answers exactly the same
    whether the chain works or not. That is how a dropped preview reads as
    coverage: the fixture was widened to stop raising, and widening a fixture
    is not the same as checking what it swallows.

    So this drives the **real** dispatch and reads what the window would have
    been handed.

    **`edit_file`, not `write_text_file`.** Only the first is consent-gated
    (`file-edit-enabled`); an ungated call returns before `decide()` is
    reached, so a preview assertion on one would pass forever against a
    dispatch path that never asked anything.
    """

    #: A refusal, so nothing is written and no second axis (does a sandboxed
    #: child succeed?) is under test. The preview is captured when the question
    #: goes out, which is before the answer matters.
    _ANSWER = permissions.DENY_CHOICE

    @staticmethod
    def _recorder(monkeypatch):
        """A window that records what it was handed, keyword and all."""
        asked = _Asked()

        def fake_ask(question, options, timeout=0.0, **kwargs):
            asked.append(question)
            asked.previews.append(kwargs.get("preview"))
            return permissions.DENY_CHOICE

        monkeypatch.setattr(ask_bridge, "has_presenter", lambda: True)
        monkeypatch.setattr(ask_bridge, "ask", fake_ask)
        return asked

    @staticmethod
    def _shut(key):
        """Turn a consent key **off**, and prove it took.

        **Off, not on - and the first version of this file granted it.**
        `tools._dispatch` asks only inside the `else` of
        `permissions.session_grant(...)`, and only when the key is
        *definitively shut*: granting the key is precisely what makes the
        prompt disappear, so a fixture that grants it observes no question at
        all. The refusal path is what the preview lives on, and a test that
        grants its way past it was measuring the absence of a gate and calling
        it a preview.

        **The round-trip is the point, not the tidiness.** `set` and a
        read-back of a value that was never written are the same call and the
        same exit code. And `file-edit-enabled` is classified destructive while
        the `everyday` preset ships it **on**, so relying on a default here
        would make this class a silent no-op the day a preset changes - the
        consent-default drift recorded in `test_the_defaults_are_the_everyday_
        preset.py`. The key is therefore set explicitly in both directions and
        the value is read back.
        """
        from shani_chronoa.config import ChronoaConfig

        config = ChronoaConfig()
        config.set(key, "false")
        assert config.get_bool(key, True) is False, (
            f"{key} could not be shut, so the gate would take the session-grant "
            "branch and no question would be asked - this would be measuring "
            "the absence of a gate and calling it a preview")

    @staticmethod
    def _in_home(name):
        """A path inside the hermetic home, because the dispatcher refuses
        anything outside it **before** the gate runs.

        `tmp_path` is not inside `HOME` - `conftest._hermetic_env` puts home at
        `tmp_path/home` - so a file written to `tmp_path` is turned away with
        "Not touching ...: it is outside your home directory" and the question
        is never asked. Measured, and the failure reads as "the gate did not
        fire" rather than as a path the fixture got wrong. The same reason
        `test_trigger_event_reachability.py` puts its repository under `$HOME`.
        """
        import os

        return Path(os.environ["HOME"]) / name

    def test_an_edit_is_described_before_it_is_made(
            self, monkeypatch, gsettings_env):
        asked = self._recorder(monkeypatch)
        self._shut("file-edit-enabled")
        target = self._in_home("greet.py")
        before = "def greet():\n    return 'hi'\n"
        target.write_text(before, encoding="utf-8")

        execute_tool_outcome("edit_file", {
            "path": str(target),
            "old_string": "'hi'",
            "new_string": "'hello'",
        })

        assert asked, "the gate never asked, so nothing here was measured"
        preview = asked.previews[0]
        assert preview is not None, (
            f"the question went out with no preview: {asked[0]!r}")
        path, was, will_be = preview

        # **A `Path`, because that is what `files.resolve` returns and what
        # `InlineDiff` is handed.** A `str` takes its whole-path fallback and
        # draws the filename where the change should be - measured, at 117px
        # against the 380px the window permits, in
        # `tests/test_the_question_fits_the_window.py`. Asserting the value
        # alone would pass on the wrong type, because `==` compares a `Path` to
        # a `Path` happily and a str to a str just as happily.
        assert isinstance(path, Path), (
            f"the preview carries a {type(path).__name__}, not a Path, which "
            f"InlineDiff cannot draw a change under: {path!r}")
        assert path == target.resolve()
        # The *file's own bytes* before, and exactly what the call would write
        # after - not the model's two arguments. The two differ the moment the
        # file holds anything else, and the difference is the whole point of
        # showing the change rather than describing it.
        assert was == before, repr(was)
        assert will_be == before.replace("'hi'", "'hello'", 1), repr(will_be)

    def test_an_edit_that_cannot_be_previewed_is_still_asked(
            self, monkeypatch, gsettings_env):
        """`approval_preview` returns `None` for a match it cannot work out, and
        its docstring says the question is still asked.

        A preview is never a reason not to ask - asking with nothing to show is
        the old behaviour, and asking *nothing* would be a gate that silently
        skips calls it cannot describe.
        """
        asked = self._recorder(monkeypatch)
        self._shut("file-edit-enabled")
        target = self._in_home("greet.py")
        target.write_text("def greet():\n    return 'hi'\n", encoding="utf-8")

        execute_tool_outcome("edit_file", {
            "path": str(target),
            "old_string": "a string that is not in the file",
            "new_string": "'hello'",
        })

        assert asked, "an unpreviewable call must still be asked about"
        assert asked.previews[0] is None, (
            f"a preview was invented for a match that does not exist: "
            f"{asked.previews[0]!r}")

    def test_a_tool_with_no_preview_concept_is_asked_without_one(
            self, monkeypatch, gsettings_env):
        """The other direction, so "computed" is distinguishable from "invented".

        Without this, a `decide()` that passed `preview=None` unconditionally
        would be caught by the first test and nothing would say whether a
        preview is ever *not* computed when it should not be.

        **This cannot witness the guard in `approval_preview`, and should not be
        read as though it does.** Removing that guard changes no single tool's
        answer (measured below), and `screenshot` in particular has no `path` at
        all - so it is turned away by the resolve, not by the guard. What this
        test holds is the property a person can see: a screenshot is asked
        about with nothing to show, because there is no file and no command.
        """
        asked = self._recorder(monkeypatch)
        self._shut("vision-sense-enabled")

        execute_tool_outcome("screenshot", {})

        assert asked, "the gate never asked, so nothing here was measured"
        assert asked.previews[0] is None, (
            f"a screenshot has no preview to describe and was handed one: "
            f"{asked.previews[0]!r}")

    def test_only_the_tools_with_something_to_describe_get_one(self, gsettings_env):
        """**The count, because no single tool can witness the guard.**

        `approval_preview` turns away a tool that is neither a file write nor a
        command with

            if name not in ("edit_file", "write_text_file"): return None

        That guard is **redundant for every other tool as shipped**, and the
        redundancy is measured rather than argued: driven across **every tool in
        the registry**, each handed the richest arguments its own schema admits,
        exactly **three** produce a preview - `edit_file` and `write_text_file`
        (the two the guard names) and `run_command`, which is handled above it.
        A tool with no `path` fails the resolve, and a tool with a `path` and no
        `old_string` fails the match, so every other tool arrives at the same
        `None` whether or not the guard is there.

        **So deleting it is an equivalent mutation and no test in this file can
        catch it.** What this assertion is for is the direction the guard does
        reach: a *fourth* tool growing a preview is the thing it exists to
        prevent, and one test naming one tool could not see that either. A count
        fails in both directions - a preview appearing where there should be
        none, and any of the three losing theirs - which is the `skills_total`
        and `KV_CACHE_MEASUREMENTS` shape: a derived fact, so it is asserted as
        one rather than restated.
        """
        from shani_chronoa import skills
        from shani_chronoa import tools as tools_mod

        schemas, _ = skills.discover_skills()
        target = self._in_home("preview-count.txt")
        target.write_text("alpha beta\n", encoding="utf-8")

        described = {}
        for schema in schemas:
            name = schema["function"]["name"]
            props = schema["function"]["parameters"].get("properties", {})
            arguments = {}
            if "path" in props:
                arguments["path"] = str(target)
            for key in ("old_string", "find", "content", "text", "command"):
                if key in props:
                    arguments[key] = "alpha"
            for key in ("new_string", "replace"):
                if key in props:
                    arguments[key] = "omega"
            if "where" in props:
                arguments["where"] = "sandbox"
            preview = tools_mod.approval_preview(name, arguments)
            if preview is not None:
                described[name] = type(preview).__name__

        assert described == {
            "edit_file": "tuple",
            "write_text_file": "tuple",
            "run_command": "dict",
        }, (
            "the set of tools carrying a preview changed: "
            f"{sorted(described)} - a call is being described before it "
            "happens, which is a preview nothing asked for, or a tool that "
            "meant to show its change has stopped doing so")
