"""Listening to the room: the sense that was missing from the sound path.

`sounds.py` (the classifier) and the `sound` trigger both existed. What did not
exist was `senses/sounds.py` - the part that decides *when* to listen. These
tests pin the two properties that matter about it.

The first is consent. The microphone hears the room including any conversation
in it, so the key defaults to off and the refusal names the reason. The
negative control is the point: a test that passes whether or not the gate works
proves nothing, so `TestTheNegativeControl` re-runs the same assertions with the
gate removed and fails.

The second is honesty about absence. A missing model, a busy microphone and a
genuinely quiet room all have to come back as refusals or as "nothing
recognisable" - never as "the room is silent". Only the first two are failures
and only the last is an observation, and collapsing them would let the
assistant state something it has no evidence for.

`listen` is never really called here: it opens the microphone. The recording
path is the classifier's own concern and is tested there.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

_REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_REPO / "usr" / "lib" / "shani-chronoa"))

from shani_chronoa import sounds as S  # noqa: E402
from shani_chronoa.config import ChronoaConfig  # noqa: E402
from shani_chronoa.senses import SENSITIVITY_PRIVATE, discover_senses  # noqa: E402
from shani_chronoa.senses import sounds as sense_sounds  # noqa: E402


def _run(allowed: bool, heard=None, problem: str = "", side_effect=None):
    """Call the sense with the mic and the model both stubbed out."""
    from unittest import mock

    listen = mock.Mock(side_effect=side_effect) if side_effect else mock.Mock(return_value=heard or [])
    with mock.patch.object(ChronoaConfig, "sense_allowed", lambda self, n: allowed), \
         mock.patch.object(ChronoaConfig, "sense_allowed_reason",
                           lambda self, n: "the sound sense is turned off (enable 'sound-sense-enabled')"), \
         mock.patch.object(S, "problem", lambda: problem), \
         mock.patch.object(S, "listen", listen):
        return sense_sounds.SENSES[0].run({}), listen


class TestItIsASense:
    def test_it_registers(self):
        assert "heard-sound" in discover_senses()

    def test_listening_is_private(self):
        """Not merely personal: this hears conversations, not just presence."""
        assert sense_sounds.SENSES[0].sensitivity == SENSITIVITY_PRIVATE

    def test_it_is_never_polled(self):
        """A background sense that opens the mic on a timer is a mic that is
        never off. The `sound` trigger is the opt-in continuous path."""
        assert sense_sounds.SENSES[0].poll_interval is None

    def test_it_declares_a_listen_length(self):
        """And it is bounded, so a request cannot hold the microphone open."""
        props = sense_sounds.SENSES[0].schema["function"]["parameters"]["properties"]
        assert props["seconds"]["type"] == "number"


class TestConsentIsTheGate:
    def test_refused_by_default(self):
        """The key defaults off, so an unconfigured assistant never listens."""
        assert ChronoaConfig().sense_allowed("sound") is False

    def test_refusal_names_the_key(self):
        out, listen = _run(allowed=False)
        assert "sound-sense-enabled" in out
        listen.assert_not_called()

    def test_refusal_says_why_it_is_private(self):
        """The user is owed the reason, not just a refusal."""
        out, _ = _run(allowed=False)
        assert "private" in out.lower()


class TestWhatItSays:
    def test_reports_what_it_heard(self):
        out, _ = _run(True, heard=[S.Heard("Doorbell", 0.71), S.Heard("Speech", 0.22)])
        assert "doorbell" in out and "71%" in out

    def test_quiet_is_not_claimed_to_be_silent(self):
        """The sharp edge. An unrecognised sound is an observation about the
        classifier, not evidence about the room."""
        out, _ = _run(True, heard=[S.Heard("Silence", 0.04)])
        assert "not proof the room was silent" in out

    def test_a_missing_model_is_a_refusal(self):
        out, listen = _run(True, problem="recognising sounds is not set up")
        assert "Not listening" in out and "not set up" in out
        listen.assert_not_called()

    def test_a_busy_microphone_is_reported_not_raised(self):
        """A read failure must not escape as an exception at the user."""
        out, _ = _run(True, side_effect=RuntimeError("the microphone is busy"))
        assert "microphone is busy" in out
        assert "unknown" in out


class TestListenLengthIsBounded:
    """The recorder gets a sane length whatever the model asked for."""

    @pytest.mark.parametrize("asked,expected", [(900, 30.0), (0, 1.0), ("abc", 3.0)])
    def test_out_of_range_and_junk_are_clamped(self, asked, expected):
        from unittest import mock

        listen = mock.Mock(return_value=[S.Heard("Knock", 0.9)])
        with mock.patch.object(ChronoaConfig, "sense_allowed", lambda self, n: True), \
             mock.patch.object(S, "problem", lambda: ""), \
             mock.patch.object(S, "listen", listen):
            sense_sounds.SENSES[0].run({"seconds": asked})
        assert listen.call_args.kwargs["seconds"] == expected

    def test_nan_falls_back_to_the_default(self):
        """NaN compares false against every bound, so it needs its own check."""
        assert sense_sounds._clamp_seconds(float("nan")) == 3.0


class TestTheNegativeControl:
    """A green line above means nothing unless the gate is what produced it.

    So the consent assertions are re-run with `sense_allowed` forced to always
    return True. If the refusal tests were passing for some other reason - a
    typo in the message, an exception being swallowed - this fails.
    """

    def test_the_refusal_tests_would_fail_without_the_gate(self):
        from unittest import mock

        forced, _ = _run(allowed=True, heard=[S.Heard("Doorbell", 0.9)])
        assert "sound-sense-enabled" not in forced, (
            "with consent forced on, the sense listened - so the refusal above "
            "was produced by the gate, which is what this control proves")
        assert "Not listening" not in forced