"""The organ tone: a shipped switch that did nothing.

`organ-audible-enabled` says, in the schema's own words:

    When true, starting to listen, look at the screen or the camera, speak,
    reach the network or act on something plays a short quiet tone naming the
    organ class. Off by default: the lights in the window are always on, and a
    sound people have learned to mute is worse than no sound.

So the feature is described, the switch is on the Voice page, and the body has
the API for it - `announce()` asks, `set_audible()` installs the answer - and
the organ strip has the tone player in `OrganStrip.play`. **Nothing joined
them**: the hook was never installed, so `announce` asked a question with no
listener, and the switch did nothing at all. A person who turned it on heard
silence, and a person who left it off could not tell the difference.

These are the three ends of that wire, asserted apart, because "the switch is
on the page" and "the switch does something" are different claims and only the
second is worth anything.
"""

from __future__ import annotations

import os
import sys

import pytest

_REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(_REPO, "usr", "lib", "shani-chronoa"))

gi = pytest.importorskip("gi")
gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
Gtk = pytest.importorskip("gi.repository.Gtk", reason="GTK 4 is not installed")


class _Config:
    def __init__(self, audible: bool = False):
        self._audible = audible

    def get_bool(self, key, default=False):
        if key == "organ-audible-enabled":
            return self._audible
        return default


class TestTheBodyAsks:
    """`body.use` asks whoever installed the sound hook."""

    def test_starting_an_organ_asks_for_its_tone(self):
        from shani_chronoa import body as body_mod

        heard = []
        body_mod.body.set_audible(lambda organ, what: heard.append((organ, what)))
        try:
            activity = body_mod.body.use("ears", "listening", "the microphone")
        finally:
            # **Closed in a finally**, so a failure closes it too: an organ
            # left lit is an indicator that stays on after the thing it
            # describes has stopped.
            body_mod.body.done(activity)
            body_mod.body.set_audible(None)
        assert heard == [("ears", "listening")], (
            f"the body did not ask for a tone: {heard}")

    def test_a_pulse_asks_too_because_reaching_the_network_is_one(self):
        """The schema names "reach the network", and that is a pulse.

        `pulse` is "something that has already finished, as a brief flash" -
        and the egress event is exactly that: the request left. So it is a
        tone like any other.
        """
        from shani_chronoa import body as body_mod

        heard = []
        body_mod.body.set_audible(lambda organ, what: heard.append((organ, what)))
        try:
            activity = body_mod.body.pulse("skin", "reached the network", "")
        finally:
            body_mod.body.done(activity)
            body_mod.body.set_audible(None)
        assert heard == [("skin", "reached the network")], (
            f"a pulse did not ask for a tone: {heard}")

    def test_with_nobody_listening_it_is_silence_not_an_error(self):
        """A daemon, or a skill's own process, has no hook - and no voice.

        That is the design, not a failure: a sound a person has learned to
        mute is worse than no sound.
        """
        from shani_chronoa import body as body_mod

        body_mod.body.set_audible(None)
        try:
            activity = body_mod.body.use("hands", "doing a thing", "")
            assert activity is not None
        finally:
            body_mod.body.done(activity)

    def test_a_hook_that_raises_does_not_take_the_activity_with_it(self):
        """One broken listener must not stop the light.

        The light is the indicator; the sound is a courtesy on top of it. A
        callback that raises is swallowed and logged, exactly as the notice
        hook's is.
        """
        from shani_chronoa import body as body_mod

        def broken(organ, what):
            raise RuntimeError("no sound here")

        body_mod.body.set_audible(broken)
        try:
            activity = body_mod.body.use("mouth", "speaking", "")
            assert activity is not None
        finally:
            body_mod.body.done(activity)
            body_mod.body.set_audible(None)


class TestTheStripHonoursTheSwitch:
    """`OrganStrip.play` is the answer, and it asks the switch first."""

    @staticmethod
    def _strip(monkeypatch, audible: bool, played):
        from shani_chronoa.gui import organs

        strip = organs.OrganStrip.__new__(organs.OrganStrip)
        strip._config = _Config(audible)
        monkeypatch.setattr(organs, "tone_wav",
                            lambda path, frequency, milliseconds=140: played.append(
                                (path, frequency)) or True)
        monkeypatch.setattr(organs, "_which", lambda program: "/usr/bin/" + program)
        monkeypatch.setattr(organs.subprocess, "Popen",
                            lambda *a, **k: played.append("played"))
        return strip

    def test_the_switch_off_means_no_tone(self, monkeypatch):
        played = []
        strip = self._strip(monkeypatch, False, played)
        strip.play("ears")
        assert played == [], (
            f"the tone played with the switch off: {played}")

    def test_the_switch_on_means_a_tone(self, monkeypatch):
        from shani_chronoa.gui import organs

        played = []
        strip = self._strip(monkeypatch, True, played)
        strip.play("ears")
        assert "played" in played, (
            f"the switch is on and nothing was played: {played}")
        # **The tone names the organ's class**, so two organs of the same
        # class share a pitch and two classes do not.
        assert organs.ORGAN_TONE["ears"][0] == organs.ORGAN_TONE["eyes"][0]
        assert organs.ORGAN_TONE["ears"][0] != organs.ORGAN_TONE["mouth"][0]
