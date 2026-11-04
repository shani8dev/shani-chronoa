"""The infrared codec, verified by round trip rather than by inspection.

There is no IR hardware on this box - no `/dev/lirc*`, nothing under
`/sys/class/rc`, and not one of `ir-ctl`/`irsend`/`irw`/`mode2`/`ir-keytable` -
so what can be *verified* here is the protocol, and that is what these tests
pin. A codec that only round-trips against itself is the specific failure mode:
a decoder written to match its own encoder never notices it is wrong, so the
timings are asserted against the published frame shapes and against each other,
not merely against the decoder's own reading.

What is deliberately **not** claimed: that a real remote recognises a frame this
module builds. That needs hardware nobody here owns.
"""


import pytest

from shani_chronoa.skills import ir_remote as I


def spaces_of(timings):
    """The 32 bit-spaces of a NEC data frame, in order."""
    assert len(timings) == 2 + 2 * 32
    return [timings[3 + 2 * i][1] for i in range(32)]


def bits_from_spaces(spaces):
    return [1 if s == I.NEC_ONE_SPACE else 0 for s in spaces]














class TestTheToolActuallyEmitsTheRightCarrier:
    """**`_run`'s carrier choice had no witness at all.**

    The constants were pinned and `to_irctl_file` was tested directly, but nothing
    drove the tool's own encode path - so `carrier = CARRIER_RC6` could become
    `CARRIER_NEC` and an RC-6 frame would be written at a frequency no RC-6
    receiver hears, with the suite green.
    """

    def _encode(self, monkeypatch, protocol):
        class _Config:
            def get_bool(self, key, default=False):
                return True

        monkeypatch.setattr(I, "ChronoaConfig", _Config)
        return I._run({"action": "encode", "protocol": protocol,
                       "address": 0x00, "command": 0x0C})

    def test_an_rc6_frame_is_written_at_36000(self, monkeypatch):
        out = self._encode(monkeypatch, "rc-6")
        assert "carrier 36000" in out, (
            f"an RC-6 frame was written at the wrong carrier: {out[:120]!r}")

    def test_a_nec_frame_is_written_at_38000(self, monkeypatch):
        out = self._encode(monkeypatch, "nec")
        assert "carrier 38000" in out, out[:120]

    def test_the_frame_round_trips_from_the_tools_own_output(self, monkeypatch):
        """The emitted file parses back to the same frame, which is the only
        thing that makes the carrier line worth emitting."""
        out = self._encode(monkeypatch, "nec")
        body = out.split("\n\n", 1)[1] if "\n\n" in out else out
        timings = I.parse_irctl_text(body)
        decoded = I.nec_decode(timings)
        assert decoded is not None and decoded[:2] == (0x00, 0x0C), decoded




class TestDevices:
    """The device probe is the part that runs on this box, so it is the part
    that must be honest rather than merely present."""

    def test_the_report_names_both_halves_when_neither_is_present(self):
        found, why = I.device_report()
        if found:
            pytest.skip("this machine actually has IR hardware")
        assert why, "a machine with no IR hardware reported nothing at all"
        assert "/dev/lirc" in why or "sys/class/rc" in why, (
            "the refusal does not say what was looked for, so a reader cannot "
            "tell 'no hardware' from 'the probe failed'")

    def test_no_device_is_reported_as_no_device(self):
        if I.lirc_devices():
            pytest.skip("this machine has lirc nodes")
        assert I.lirc_devices() == []
        assert I.rc_devices() == []

    def test_the_gate_is_off_by_default_and_refuses(self):
        class _Config:
            def get_bool(self, key, default=False):
                return False
        allowed, why = I._consent(_Config())
        assert allowed is False
        assert "ir-remote-enabled" in why
