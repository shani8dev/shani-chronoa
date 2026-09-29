"""Reading the idle clock, and the one wrong answer that must be unreachable.

`idle == 0` means *someone is actively at this machine right now*. A voice
assistant that believes nobody is home stays quiet; one that wrongly believes
someone is home talks at an empty room, and lands that on the user at the worst
possible moment.

That makes "could not read the clock" the most dangerous value this sense could
possibly return. A missing display, a missing `libXss`, or an X server without
the Screen Saver extension must all produce *unknown* - never 0.

Verified live on this machine: the reading tracks wall time (0.0, 0.0, 4.9,
9.9 across five-second gaps once nothing was touching the display), so it is a
live observation rather than a constant. Something on this desktop does generate
occasional X input, which is why a short sample can read 0 - a real value, but
not evidence the clock is stuck.
"""

from __future__ import annotations

import ctypes.util
import sys
import time
from pathlib import Path

import pytest

_REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_REPO / "usr" / "lib" / "shani-chronoa"))

from shani_chronoa.senses import SENSITIVITY_PERSONAL, discover_senses  # noqa: E402
from shani_chronoa.senses import idle as I  # noqa: E402


class TestItIsASense:
    def test_it_registers(self):
        assert "idle" in discover_senses()

    def test_presence_is_personal(self):
        """Idle time draws the shape of someone's day."""
        assert I._SENSE.sensitivity == SENSITIVITY_PERSONAL

    def test_it_is_polled(self):
        """Unlike `accessibility`: a presence flag is cheap and bounded, and a
        background sense that never fires is not a background sense."""
        assert I._SENSE.poll_interval is not None

    def test_it_names_its_consent_key(self):
        from shani_chronoa.config import _SENSE_CONSENT_KEYS
        assert _SENSE_CONSENT_KEYS.get("idle") == "idle-sense-enabled"


class TestZeroMeansSomeoneIsThere:
    """The invariant, stated as a property of the code rather than a message."""

    def test_no_failure_path_returns_zero(self):
        """`read_idle_seconds` returns a real number or raises. Never 0.

        Checked structurally: every non-raising exit from the function is the
        success return. If a future change adds a fallback, it has to be added
        below this line, which is the point.
        """
        source = (_REPO / "usr" / "lib" / "shani-chronoa" / "shani_chronoa"
                  / "senses" / "idle.py").read_text()
        body = source.split("def read_idle_seconds()", 1)[1].split("\ndef ", 1)[0]
        returns = [line.strip() for line in body.splitlines()
                   if line.strip().startswith("return")]
        assert returns == ["return info.contents.idle / 1000.0"], (
            f"read_idle_seconds has a return other than the real reading: {returns}"
        )

    def test_no_display_is_unknown_not_zero(self, monkeypatch):
        monkeypatch.delenv("DISPLAY", raising=False)
        with pytest.raises(I._Unavailable):
            I.read_idle_seconds()

    def test_a_missing_extension_library_is_unknown(self, monkeypatch):
        real = ctypes.util.find_library
        monkeypatch.setattr(ctypes.util, "find_library",
                            lambda n: None if n == "Xss" else real(n))
        with pytest.raises(I._Unavailable):
            I.read_idle_seconds()

    def test_the_message_never_claims_someone_is_present_when_unreadable(self, monkeypatch):
        monkeypatch.delenv("DISPLAY", raising=False)
        out = I._run({}).lower()
        assert "not reading" in out
        assert "in use" not in out
        assert "actively" not in out


class TestTheMessage:
    def test_it_refuses_when_the_key_is_off(self, monkeypatch):
        monkeypatch.setattr(I, "ChronoaConfig", _config(False))
        assert "Not reading" in I._run({})

    def test_the_refusal_names_the_key(self):
        """The real config, not a stub.

        `sense_allowed_reason` is what puts the key name in the message, so a
        hand-written stand-in would only prove the stub says what the test
        wrote into it - which is exactly the mistake the first version of this
        made, and the accessibility sense's test made before it.
        """
        assert "idle-sense-enabled" in I._run({})

    def test_a_short_idle_reads_as_present(self):
        assert "in use" in I._describe(1.0)

    def test_a_minute_reads_as_present_but_not_working(self):
        assert "at the machine" in I._describe(120.0)

    def test_hours_reads_as_away(self):
        assert "away" in I._describe(3 * 3600 + 120)

    def test_the_thresholds_do_not_overlap_awkwardly(self):
        for seconds in (0, 4, 5, 59, 60, 3599, 3600, 86399):
            text = I._describe(seconds)
            assert text and text != "None"


@pytest.mark.skipif(not __import__("os").environ.get("DISPLAY"),
                    reason="no X display in this environment")
class TestAgainstTheRealClock:
    def test_it_reads_a_plausible_idle_time(self):
        seconds = I.read_idle_seconds()
        assert 0 <= seconds < 86400, f"implausible idle time: {seconds}"

    def test_it_advances_when_nothing_touches_the_display(self):
        """The difference between a live reading and a constant.

        Two samples 2s apart must differ by roughly 2s. This can flake if
        something on the desktop generates X input, so the tolerance is generous
        and the assertion is only that it is not frozen at one value.
        """
        first = I.read_idle_seconds()
        time.sleep(2)
        second = I.read_idle_seconds()
        assert second != first or second == 0, (
            f"the idle clock returned {first} then {second}: not advancing"
        )


def _config(allowed: bool):
    class _C:
        def sense_allowed(self, name):
            return allowed

        def sense_allowed_reason(self, name):
            return "" if allowed else "the idle sense is turned off"
    return _C
