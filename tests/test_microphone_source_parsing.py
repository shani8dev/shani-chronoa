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

## wpctl cannot report a mute state at all

Its commands are status, get-volume, inspect, set-default, set-volume, set-mute,
set-profile and clear-default. Mute can be *set*; it cannot be *read*. `wpctl
inspect` does not expose it either - no key containing "mute" appears in a live
node's entire property dump.

The first version called `wpctl get-mute <id>`, which does not exist, so every
status call answered UNKNOWN. UNKNOWN is honest, and it is now said *with its
reason* rather than left looking like a finding.

And an unreadable state must not block the change: `set-mute` takes an absolute 1
or 0, so the action does not depend on what the current state was. Refusing would
have left the microphone permanently uncontrollable through this skill on every
machine, which is worse than setting it and saying so.
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

    def run(*args):
        calls.append(list(args))
        if args[:1] == ("status",):
            return _Proc(STATUS)
        return _Proc("", 0)

    monkeypatch.setattr(M, "run_wpctl", run)
    # `_default_source` delegates parsing to `senses.audio`, so that module has
    # its own wpctl call. Stubbing only the skill's left the *test* reading the
    # real machine's status - which is why one test asserted against this
    # fixture's names and found this machine's instead.
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
        """
        from shani_chronoa.senses.audio import _parse_status
        parsed = _parse_status(STATUS)
        assert len(parsed["sources"]) == 3, (
            f"the fixture no longer looks like real wpctl output - it parsed "
            f"to {parsed['sources']}. The heading this bug was about is "
            f"spelled differently again, or the parser no longer matches the "
            f"characters wpctl actually prints."
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


class TestMuteStateIsHonestAboutBeingUnreadable:
    def test_it_does_not_call_a_command_that_does_not_exist(self, status):
        """`wpctl get-mute` is not a command; `set-mute` is the only one."""
        M._run({"action": "status"})
        assert ["get-mute", "56"] not in status, (
            "a status call reached for a wpctl subcommand that does not exist"
        )

    def test_status_says_why_it_is_unknown(self, status):
        out = M._run({"action": "status"})
        assert "UNKNOWN" in out
        assert "cannot report it" in out, (
            "a bare UNKNOWN looks like a finding about the microphone rather "
            "than an admission that the tool cannot answer"
        )

    def test_status_still_names_the_microphone(self, status):
        out = M._run({"action": "status"})
        assert "Built-in Audio Analog Stereo" in out


class TestAnUnreadableStateDoesNotBlockTheChange:
    """`set-mute` is absolute, so the current state is not needed."""

    def test_mute_still_works(self, status):
        out = M._run({"action": "mute"})
        assert ["set-mute", "56", "1"] in status, (
            "an unreadable starting state blocked the change, which would "
            "leave the microphone uncontrollable on every machine"
        )
        assert "Muted" in out

    def test_unmute_still_works(self, status):
        M._run({"action": "unmute"})
        assert ["set-mute", "56", "0"] in status

    def test_and_the_answer_says_it_set_rather_than_changed(self, status):
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
