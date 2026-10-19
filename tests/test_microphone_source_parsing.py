"""The microphone skill reported that this machine has no microphone.

On a laptop with a working built-in microphone. That is the failure this project
treats as worst: a confident, plausible statement about hardware that is plainly
present, and one the user can disprove by looking at their own machine.

## Why it said that

`_default_source()` walked `wpctl status` itself, looking for a heading spelled
`Audio Sources:`. No such heading exists - `wpctl` prints `Sources:`, and prints
the whole status twice (once for the audio graph, once for video), so a scanner
has to pick the right one. The heading never matched, the loop never captured,
and the skill answered "no capture source was reported".

Parsing is now delegated to `senses.audio`, which already gets it right.

## wpctl can report a mute state, and the first version said it could not

Its commands are status, get-volume, inspect, set-default, set-volume, set-mute,
set-profile and clear-default. Mute can be set, and it can be read - but not
through a command of its own, which is what the first version believed.

**`wpctl inspect` genuinely does not expose it**: no key containing "mute"
appears in a live node's entire property dump. That part of the old belief was
right. The conclusion drawn from it was wrong, because the state comes back
through two other commands:

- `wpctl get-volume <id>` prints `Volume: 1.00 [MUTED]` when the node is
  muted, and `Volume: 1.00` when it is not;
- `wpctl status` prints the same state inside the volume annotation - a muted
  node's line ends `[vol: 1.00 MUTED]`.

Both were measured, by muting a live source and reading both commands back
(the source was unmuted again immediately afterwards). The first version of this
skill called `wpctl get-mute <id>`, which does not exist, so every status call
answered UNKNOWN.

So UNKNOWN is still a state this code can represent - it is what a *failed*
read reports - but it is no longer what a healthy machine produces.

And an unreadable state must still not block the change: `set-mute` takes an
absolute 1 or 0, so the action does not depend on what the current state was.
Refusing would have left the microphone permanently uncontrollable through this
skill on every machine, which is worse than setting it and saying so.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

_REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_REPO / "usr" / "lib" / "shani-chronoa"))

from shani_chronoa.skills import set_mic_mute as M  # noqa: E402

# Verbatim `wpctl status` output from this machine, box-drawing characters and
# all. Retyping it as ASCII was the first version of this fixture and it parsed
# to zero sources, because `_BOX` strips the real characters and nothing else -
# so the fixture would have been testing a shape the parser never sees. The bug
# this file documents was a heading that did not exist, and a fixture that
# misrepresents the input hides exactly that class of mistake.
STATUS = """\
PipeWire 'pipewire-0' [1.0.5, host@machine]
 \u251c\u2500 Devices:
 \u2502  *   47. Built-in Audio Analog Stereo [vol: 1.00]
 \u2502
 \u251c\u2500 Sinks:
 \u2502  *   54. Built-in Audio Analog Stereo [vol: 0.50]
 \u2502
 \u251c\u2500 Sources:
 \u2502      55. Headset Microphone [vol: 1.00]
 \u2502  *   56. Built-in Audio Analog Stereo [vol: 0.43]
 \u2502
 \u2514\u2500 Streams:

Video
 \u251c\u2500 Devices:
 \u2502  *   49. Integrated Camera (V4L2)
 \u2502
 \u251c\u2500 Sources:
 \u2502  *   49. Integrated Camera (V4L2)
 \u2502
 \u2514\u2500 Streams:
"""


class _Proc:
    def __init__(self, stdout="", returncode=0, stderr=""):
        self.stdout, self.returncode, self.stderr = stdout, returncode, stderr


@pytest.fixture
def status(monkeypatch):
    """A `wpctl` answering from the captured real output, with consent granted.

    Consent is granted here because most of these tests are about *what the
    skill does once it is allowed to act*. The gate has its own test below, and
    leaving it shut meant the action tests were all really testing the refusal.
    """
    class _On:
        def get_bool(self, key, default=False):
            return True

        def sense_allowed(self, name):
            return True
    monkeypatch.setattr(M, "ChronoaConfig", lambda: _On())
    calls = []
    state = {"muted": False}

    def run(*args):
        calls.append(list(args))
        if args[:1] == ("status",):
            return _Proc(STATUS)
        if args[:1] == ("get-volume",):
            # What the real wpctl prints for a node, measured on this machine.
            # The first version of this fixture answered `get-volume` with an
            # empty string, which is not a thing wpctl does - and an empty
            # answer is not an unmuted node, it is a command that said
            # nothing. The mute state is tracked so that a `set-mute` is
            # reflected in the read-back, which is what a real wpctl does.
            if state["muted"]:
                return _Proc("Volume: 0.43 [MUTED]")
            return _Proc("Volume: 0.43")
        if args[:1] == ("set-mute",):
            state["muted"] = args[2] == "1"
            return _Proc("")
        return _Proc("", 0)

    monkeypatch.setattr(M, "run_wpctl", run)
    # The mute state is read back through `pipewire.mute_state`, which calls
    # `pipewire.run_wpctl` - a different name from the skill's own import, so
    # stubbing only the skill left the *read* reaching the real machine. The
    # comment on the sense's own stub below says the same thing about the parse.
    from shani_chronoa import pipewire
    monkeypatch.setattr(pipewire, "run_wpctl", run)
    # `_default_source` delegates parsing to `senses.audio`, so that module has
    # its own wpctl call. Stubbing only the skill's left the *test* reading the
    # real machine's status - which is why one test asserted against this
    # fixture's names and found this machine's instead.
    from shani_chronoa.senses import audio as audio_sense
    monkeypatch.setattr(audio_sense, "_run_wpctl", run)
    return calls


@pytest.fixture
def unreadable(monkeypatch):
    """A `wpctl` whose volume read fails, so the mute state cannot be seen.

    The same consent and the same status, but `get-volume` fails the way a
    missing node does. This is the case UNKNOWN is for - a read that could not
    be made - and it is a different thing from a node that is known to be
    unmuted.
    """
    class _On:
        def get_bool(self, key, default=False):
            return True

        def sense_allowed(self, name):
            return True
    monkeypatch.setattr(M, "ChronoaConfig", lambda: _On())
    calls = []

    def run(*args):
        calls.append(list(args))
        if args[:1] == ("status",):
            return _Proc(STATUS)
        if args[:1] == ("get-volume",):
            return _Proc("", 1, "No such node")
        return _Proc("", 0)

    monkeypatch.setattr(M, "run_wpctl", run)
    from shani_chronoa import pipewire
    monkeypatch.setattr(pipewire, "run_wpctl", run)
    from shani_chronoa.senses import audio as audio_sense
    monkeypatch.setattr(audio_sense, "_run_wpctl", run)
    return calls


class TestItFindsTheMicrophone:
    def test_the_default_source_is_found(self, status):
        source = M._default_source()
        assert source is not None, (
            "no capture source found - this is the bug that made the skill "
            "report a working microphone as absent"
        )
        assert source[0] == "56"

    def test_it_picks_the_starred_default(self, status):
        """Not merely the first one. `*` is how wpctl marks the default.

        The fixture lists 55 before 56 on purpose, so "first" and "default" are
        different answers. Without that, deleting the starred check leaves every
        test green - which is what M2 did when it tried.
        """
        parsed = M._default_source()
        assert parsed[0] == "56", (
            f"picked {parsed[0]}, which is not the source wpctl marked as "
            f"default - first-listed was 55"
        )

    def test_the_parsed_status_really_contains_sources(self):
        """Guards the fixture itself.

        A test whose fake data does not contain what the code looks for proves
        nothing, and this bug was precisely a heading that did not exist.

        **Was 3, now 2** (2026-09-29). This fixture carries a `Video` block with
        its own `Sources:` heading, and the parser tracked only "am I inside
        Sources" - so the integrated camera was counted as a third microphone.
        That is the bug `senses.audio` now fixes by tracking the top-level block,
        and this assertion had been pinned to the buggy count. Two is the correct
        number: 55 and 56 are the microphones, 49 is a video device. A webcam
        that genuinely exposes an audio stream is still kept when PipeWire lists
        it under `Audio`; the filter is by block, not by name.
        """
        from shani_chronoa.senses.audio import _parse_status
        parsed = _parse_status(STATUS)
        assert len(parsed["sources"]) == 2, (
            f"the fixture no longer looks like real wpctl output - it parsed "
            f"to {parsed['sources']}. The heading this bug was about is "
            f"spelled differently again, or the parser no longer matches the "
            f"characters wpctl actually prints."
        )
        assert [s["id"] for s in parsed["sources"]] == ["55", "56"], (
            "the two microphones must be the sources; a video device reached "
            "the microphone list"
        )

    def test_the_fixture_lists_a_non_default_first(self, status):
        """Guards the guard.

        `wpctl` lists sources in an order that has nothing to do with which is
        default, so a fixture with the starred source first cannot tell "picked
        the default" apart from "picked the first". This test is what makes the
        starred check meaningful - without it, deleting that check leaves
        everything green, which is exactly what M2 did.
        """
        from shani_chronoa.senses.audio import _parse_status
        parsed = _parse_status(STATUS)
        assert parsed["sources"][0]["id"] == "55", (
            "the fixture's first source is the default, so preferring the "
            "default is indistinguishable from preferring the first"
        )
        assert M._default_source()[0] == "56"

    def test_a_heading_nobody_prints_is_not_what_it_looks_for(self):
        """The specific mistake, pinned.

        `Audio Sources:` was never a heading `wpctl` emits. If that string
        reappears in this module, the parser has been rewritten to match itself
        rather than the tool it reads.
        """
        source = (_REPO / "usr" / "lib" / "shani-chronoa" / "shani_chronoa"
                  / "skills" / "set_mic_mute.py").read_text()
        code = "\n".join(
            line for line in source.splitlines()
            if not line.strip().startswith(("#", "*", '"""'))
        )
        assert '"Audio Sources:"' not in code, (
            "the parser is matching a heading wpctl does not print"
        )


class TestTheMuteStateIsReadBack:
    """It is readable, so a healthy machine must not be answered UNKNOWN."""

    def test_it_does_not_call_a_command_that_does_not_exist(self, status):
        """`wpctl get-mute` is not a command; `set-mute` is the only one."""
        M._run({"action": "status"})
        assert ["get-mute", "56"] not in status, (
            "a status call reached for a wpctl subcommand that does not exist"
        )

    def test_status_reads_the_state_back(self, status):
        """`wpctl get-volume` prints the mute state, so the skill asks for it.

        Measured on this machine: `wpctl get-volume 61` printed `Volume: 1.00`
        for an unmuted source and `Volume: 1.00 [MUTED]` for a muted one, the
        marker having been put there and taken away again.
        """
        M._run({"action": "status"})
        assert ["get-volume", "56"] in status, (
            "the mute state was not read back, so the answer cannot be a fact"
        )

    def test_status_reports_the_state_it_read(self, status):
        out = M._run({"action": "status"})
        assert "Mute state: unmuted" in out, (
            "a readable state was reported as UNKNOWN, which is a refusal to "
            "answer on a machine that answered"
        )

    def test_status_still_names_the_microphone(self, status):
        out = M._run({"action": "status"})
        assert "Built-in Audio Analog Stereo" in out


class TestTheChangeIsVerifiedByReadingItBack:
    """A mute that worked, confirmed by reading the state back.

    This is the symptom the whole cluster produced on this skill: `_is_muted`
    returned `None` on every path, so the read-back after `set-mute` was
    always "could not be read" and a microphone that really was muted was
    reported as **not verified**.
    """

    def test_a_mute_is_confirmed_by_reading_it_back(self, status):
        out = M._run({"action": "mute"})
        assert "verified by reading it back" in out, (
            "a mute that worked was reported as unverified, because the state "
            "could not be read back"
        )

    def test_an_unmute_is_confirmed_too(self, status):
        M._run({"action": "mute"})
        out = M._run({"action": "unmute"})
        assert "verified by reading it back" in out

    def test_the_post_condition_verifies_the_mute(self, status):
        """The skill's own post-condition, which read the same broken state."""
        M._run({"action": "mute"})
        ok, evidence = M.POST_CONDITION({"action": "mute"})
        assert ok is True, evidence
        assert "read back" in evidence

    def test_the_post_condition_refuses_to_claim_an_unmute_that_did_not_happen(self, status):
        """The other direction: a mute that did not take must not verify."""
        ok, evidence = M.POST_CONDITION({"action": "mute"})
        assert ok is False, (
            "the post-condition verified a mute the microphone was not muted for"
        )


class TestAnUnreadableStateIsSaidAsSuch:
    """UNKNOWN is for a read that could not be made - not for a healthy machine."""

    def test_status_says_the_state_could_not_be_read(self, unreadable):
        out = M._run({"action": "status"})
        assert "UNKNOWN" in out
        assert "could not read" in out, (
            "a bare UNKNOWN looks like a finding about the microphone rather "
            "than an admission that the tool could not answer"
        )

    def test_it_does_not_claim_wpctl_cannot_report_it(self, unreadable):
        """The old sentence was a claim about the tool, and it was false.

        `wpctl get-volume` does report the mute state; what failed here was
        this one read. Saying the command has no way to report it would send
        the next reader looking for a missing capability rather than a broken
        read.
        """
        out = M._run({"action": "status"})
        assert "cannot report it" not in out, (
            "the answer blames wpctl for a read this skill could not make"
        )


class TestAnUnreadableStateDoesNotBlockTheChange:
    """`set-mute` is absolute, so the current state is not needed."""

    def test_mute_still_works(self, unreadable):
        out = M._run({"action": "mute"})
        assert ["set-mute", "56", "1"] in unreadable, (
            "an unreadable starting state blocked the change, which would "
            "leave the microphone uncontrollable on every machine"
        )
        assert "Muted" in out

    def test_unmute_still_works(self, unreadable):
        M._run({"action": "unmute"})
        assert ["set-mute", "56", "0"] in unreadable

    def test_and_the_answer_says_it_set_rather_than_changed(self, unreadable):
        """So the result is checkable by the user instead of resting on a
        claim about state nobody could read."""
        out = M._run({"action": "mute"})
        assert "could not be read" in out
        assert "rather than changing it" in out


class TestConsentStillGatesTheChange:
    def test_a_shut_key_still_refuses(self, status, monkeypatch):
        class _Off:
            def get_bool(self, key, default=False):
                return False
        monkeypatch.setattr(M, "ChronoaConfig", lambda: _Off())
        out = M._run({"action": "mute"})
        assert "Refusing" in out
        assert ["set-mute", "56", "1"] not in status, (
            "the consent gate was bypassed"
        )
