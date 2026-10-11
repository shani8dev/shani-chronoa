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


class TestNecEncoder:
    def test_the_address_is_sent_least_significant_bit_first(self):
        """LSB-first is the one thing a matching decoder cannot catch."""
        timings = I.nec_encode(0x04, 0x00)
        bits = bits_from_spaces(spaces_of(timings))
        # 0x04 = 0b00000100, so LSB-first the bits are 0,0,1,0,0,0,0,0.
        assert bits[0:8] == [0, 0, 1, 0, 0, 0, 0, 0], (
            f"the address came out {bits[0:8]}, not least-significant first")

    def test_each_byte_is_followed_by_its_complement(self):
        timings = I.nec_encode(0x12, 0x34)
        bits = bits_from_spaces(spaces_of(timings))
        # The frame is address, ~address, command, ~command, eight bits each.
        for start in (0, 16):
            byte = bits[start:start + 8]
            complement = bits[start + 8:start + 16]
            assert len(byte) == 8 and len(complement) == 8
            assert all(b != c for b, c in zip(byte, complement)), (
                f"the bytes at {start} and {start + 8} are {byte} and "
                f"{complement}, which are not complements of each other - the "
                "protocol's own error check is absent")

    def test_the_address_and_command_come_from_the_right_bits(self):
        for address, command in ((0x00, 0x00), (0x04, 0x08), (0xFF, 0xFF),
                                 (0x12, 0x34), (0x01, 0x45)):
            bits = bits_from_spaces(spaces_of(I.nec_encode(address, command)))
            got_addr = sum(b << i for i, b in enumerate(bits[0:8]))
            got_cmd = sum(b << i for i, b in enumerate(bits[16:24]))
            assert (got_addr, got_cmd) == (address, command), (
                f"{address:#04x}/{command:#04x} encoded as "
                f"{got_addr:#04x}/{got_cmd:#04x}")


class TestNecFrameLength:
    """A NEC frame is always exactly 67420 us, and that is a theorem.

    Each byte travels with its complement, so of any 16 bits exactly 8 are ones:
    a byte with `k` ones has a complement with `8-k`. Four byte-pairs therefore
    always carry 16 ones and 16 zeros, whatever the payload.
    """

    @pytest.mark.parametrize("address,command", [
        (0x00, 0x00), (0x04, 0x08), (0xFF, 0xFF), (0x12, 0x34), (0x55, 0xAA),
    ])
    def test_every_frame_is_the_same_length(self, address, command):
        timings = I.nec_encode(address, command)
        assert sum(us for _, us in timings) == I.NEC_FRAME_US, (
            f"{address:#04x}/{command:#04x} encoded to "
            f"{sum(us for _, us in timings)} us, but the theorem says every "
            f"frame is {I.NEC_FRAME_US}")

    def test_the_constant_is_the_published_arithmetic(self):
        # Rebuilt from the spec so a change to the timings cannot silently
        # change the check as well.
        expected = 9000 + 4500 + 32 * 560 + 16 * 560 + 16 * 1690
        assert I.NEC_FRAME_US == expected == 67420

    def test_a_frame_that_is_not_the_right_length_is_refused(self):
        timings = I.nec_encode(0x04, 0x08)
        # Drop one bit's space: the frame is now too short to be a NEC frame.
        assert I.nec_decode(timings[:-2]) is None


class TestNecRoundTrip:
    @pytest.mark.parametrize("address,command", [
        (0x00, 0x00), (0x04, 0x08), (0xFF, 0xFF), (0x12, 0x34), (0x01, 0x45),
        (0x10, 0x0F), (0x7F, 0x80),
    ])
    def test_encode_then_decode_is_the_identity(self, address, command):
        got = I.nec_decode(I.nec_encode(address, command))
        assert got == (address, command, False), (
            f"{address:#04x}/{command:#04x} round-tripped to {got}")

    def test_the_repeat_frame_round_trips(self):
        timings = I.nec_encode(0x04, 0x08, repeat=True)
        assert I.nec_decode(timings) == (0, 0, True)
        assert len(timings) == 3
        assert sum(us for _, us in timings) == 9000 + 2250 + 560

    def test_a_real_receiver_jitter_is_tolerated(self):
        """A real capture is never exactly the nominal timings.

        The decoder's tolerance is what makes `receive` able to read a real
        remote at all, and the round trip above is silent about it because it
        feeds the decoder its own exact output.
        """
        timings = I.nec_encode(0x12, 0x34)
        jittered = [(kind, us + int(us * 0.20)) for kind, us in timings]
        got = I.nec_decode(jittered)
        assert got == (0x12, 0x34, False), (
            f"20% jitter turned {got!r} - a real receiver is noisier than this")


class TestNecDecoderRefuses:
    """A wrong scancode is an instruction to a device somebody owns."""

    def test_silence_is_not_a_frame(self):
        assert I.nec_decode([]) is None
        assert I.nec_decode([("pulse", 0)]) is None

    def test_a_frame_with_a_broken_complement_is_refused(self):
        timings = list(I.nec_encode(0x12, 0x34))
        # Bit 25 is the second bit of the command's complement. Its *space* is
        # at index 3 + 2*25 - the same off-by-one that made an earlier version
        # of this test flip the **pulse** at index 2 + 2*25 instead, which the
        # burst-width check refuses for an unrelated reason and which therefore
        # let the complement check be deleted without anything failing.
        index = 3 + 2 * 25
        assert timings[index][0] == "space"
        timings[index] = ("space", (I.NEC_ZERO_SPACE
                                    if timings[index][1] == I.NEC_ONE_SPACE
                                    else I.NEC_ONE_SPACE))
        assert I.nec_decode(timings) is None, (
            "a byte whose complement does not invert was accepted - that is the "
            "protocol's own error check and it has to catch it")

    def test_an_rc6_frame_is_not_a_nec_frame(self):
        assert I.nec_decode(I.rc6_encode(0, 0x0C, 0x00)) is None

    def test_a_frame_that_starts_with_a_space_is_refused(self):
        timings = I.nec_encode(0x12, 0x34)
        assert I.nec_decode([("space", 9000)] + timings[1:]) is None


class TestRc6:
    def test_every_field_is_least_significant_first(self):
        timings = I.rc6_encode(0, 0x01, 0x00)
        # Header + start bit is 4 timings, then 3 mode bits of two timings each.
        assert timings[0:4] == [("pulse", I.RC6_HEADER_PULSE), ("space", I.RC6_HEADER_SPACE),
                                ("pulse", I.RC6_START_PULSE), ("space", I.RC6_START_SPACE)]
        assert len(timings) == 4 + 6 + 2 + 16 * 2

    @pytest.mark.parametrize("control,info", [
        (0x00, 0x00), (0x00, 0x0C), (0x00, 0xFF), (0x3F, 0x01), (0xAA, 0x55),
    ])
    def test_round_trip(self, control, info):
        got = I.rc6_decode(I.rc6_encode(0, info, control))
        assert got == (0, control, info), (
            f"control {control:#04x} / info {info:#04x} round-tripped to {got}")

    def test_the_carrier_is_the_rc6_one_and_not_nec(self):
        """NEC is 38 kHz and RC-6 is 36 kHz, and a receiver tuned to one does
        not hear the other.

        **The constants alone were not a witness.** This test read `CARRIER_NEC`
        and `CARRIER_RC6` and never touched the code that *chooses* one, so a
        mutation that wrote an RC-6 frame at the NEC carrier left it green. The
        file the skill actually emits is asserted too.
        """
        assert I.CARRIER_NEC == 38000
        assert I.CARRIER_RC6 == 36000
        text = I.to_irctl_file(I.rc6_encode(0, 0x0C, 0x00), I.CARRIER_RC6)
        assert text.splitlines()[0] == "carrier 36000", (
            f"an RC-6 frame was written at the NEC carrier: {text[:60]!r}")
        nec = I.to_irctl_file(I.nec_encode(0x12, 0x34), I.CARRIER_NEC)
        assert nec.splitlines()[0] == "carrier 38000", nec.splitlines()[0]

    def test_a_nec_frame_is_not_an_rc6_frame(self):
        assert I.rc6_decode(I.nec_encode(0x12, 0x34)) is None

    def test_only_mode_zero_is_built(self):
        """A receiver reading a mode-14 frame as mode 0 takes the wrong bits as
        data, so another mode is refused rather than silently encoded wrong."""
        with pytest.raises(ValueError):
            I.rc6_encode(14, 0x01, 0x00)

    def test_the_trailer_space_is_doubled(self):
        """The doubled space is what tells a decoder the data field has begun.

        **The first version asserted against the module's own constants and could
        not fail** - `timings[5 + 6] == ("space", I.RC6_TRAILER_SPACE)` holds for
        any value of `RC6_TRAILER_SPACE`, so the constant could be halved and the
        test stayed green. The published arithmetic is pinned instead, rebuilt
        from the spec the same way `NEC_FRAME_US` is.
        """
        timings = I.rc6_encode(0, 0x0C, 0x00)
        # After the 3 mode bits the trailer is a burst then a doubled space.
        assert timings[4 + 6] == ("pulse", I.RC6_TRAILER_PULSE)
        assert timings[5 + 6] == ("space", I.RC6_TRAILER_SPACE)
        # The literals, rebuilt the same way `NEC_FRAME_US` is. **889, not
        # 2 x 444 = 888** - the first version of this asserted the product and
        # failed, because the RC-6 spec's trailer is 889 us rather than the
        # arithmetic of two 444 us half-periods. That is the shape this repo
        # records for `privilege` and `mokutil`: a number that looks derivable
        # and is not, so it is pinned as published.
        assert I.RC6_TRAILER_PULSE == 444
        assert I.RC6_TRAILER_SPACE == 889, (
            f"the trailer space is {I.RC6_TRAILER_SPACE}, not the published "
            f"889 - a decoder cannot tell where the data begins")


class TestTheDecoderRefusesRatherThanGuesses:
    """**The refusals are the load-bearing half, and two of them had no witness.**

    `nec_decode` returns None for anything that is not a NEC frame, and the
    reasons it must are three: the wrong length, the wrong leader, and the wrong
    protocol. Only the first was tested with an input that could fail.
    """

    def test_a_frame_that_is_too_long_is_refused(self):
        """**This had no witness.** The length check was `!=`, and the only test
        fed a frame two timings *short* - which the `len(timings) != 2 + 2 * 32`
        check refuses before the arithmetic is reached, so widening the total's
        tolerance changed nothing anybody measured."""
        timings = I.nec_encode(0x04, 0x08)
        assert I.nec_decode(timings + [("space", 560)]) is None, (
            "a capture longer than a NEC frame was read as one with extra bits")

    def test_a_leader_of_the_wrong_width_is_refused(self):
        """**And this had no witness either.** `_match(timings[0][1],
        NEC_LEADER_PULSE)` was the only thing standing between an RC-6 header and
        a NEC read, and both refusal tests fed something the `!= "pulse"` check
        already caught."""
        timings = I.nec_encode(0x12, 0x34)
        # RC-6's header pulse is 2666 us, not NEC's 9000.
        assert I.nec_decode([("pulse", 2666)] + timings[1:]) is None, (
            "a 2666us leader was read as NEC's 9000us one")


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


class TestIrctlFile:
    """The `ir-ctl --send` file format, quoted from its man page."""

    def test_the_file_starts_with_the_carrier(self):
        body = I.to_irctl_file(I.nec_encode(0x04, 0x08))
        assert body.startswith("carrier 38000\n"), (
            "the carrier line is optional in the format and emitted because a "
            "transmitter's default is not the protocol's")

    def test_the_file_starts_with_a_pulse(self):
        """The man page's own example is `carrier 36000 pulse 940 space 860 ...`
        - a pulse first, and the format is strict about the alternation."""
        body = I.to_irctl_file(I.nec_encode(0x04, 0x08))
        words = [w for w in body.split() if w not in ("carrier", "38000")]
        assert words[0] == "pulse" and words[2] == "space", (
            f"the timings start {words[0]}/{words[1]}, not pulse")

    def test_it_parses_back(self):
        timings = I.nec_encode(0x12, 0x34)
        assert I.parse_irctl_text(I.to_irctl_file(timings)) == timings

    def test_it_parses_the_man_pages_own_example(self):
        """The rc-5 example verbatim from ir-ctl(1), so the parser is tested
        against the documented format and not only against its own writer."""
        text = ("carrier 36000\n"
                "pulse 940 space 860\npulse 1790 space 1750\n"
                "pulse 880 space 880\npulse 900 space 890\n"
                "pulse 870 space 900\npulse 1750 space 900\n"
                "pulse 890 space 910\npulse 840 space 920\n"
                "pulse 870 space 920\npulse 840 space 920\n"
                "pulse 870 space 1810\npulse 840\n")
        got = I.parse_irctl_text(text)
        assert got[0] == ("pulse", 940)
        assert got[1] == ("space", 860)
        assert ("pulse", 1810 - 900) not in got  # the last line is a pulse
        assert got[-1] == ("pulse", 840)
        assert len(got) == 23

    def test_unlabelled_numbers_alternate(self):
        """The format allows a bare list of integers, which alternates from the
        previous entry."""
        assert I.parse_irctl_text("940 860 1790") == [("pulse", 940),
                                                      ("space", 860),
                                                      ("pulse", 1790)]

    def test_a_keywords_value_is_not_read_as_a_timing(self):
        """`carrier 38000` is two tokens and neither is a timing.

        A parser that skipped only the keyword would take 38000 as a pulse and
        shift every timing after it out of the frame, which is invisible in a
        round trip that feeds the decoder its own exact output - so this states
        the two-token rule on its own.
        """
        timings = I.nec_encode(0x12, 0x34)
        body = I.to_irctl_file(timings)
        got = I.parse_irctl_text(body)
        assert got == timings, (
            f"the round trip through the carrier line gave {len(got)} timings, "
            f"not {len(timings)}")
        assert ("pulse", 38000) not in got, (
            "the carrier's frequency was parsed as a pulse length")

    def test_the_parser_stops_at_nothing_it_should_keep(self):
        """A `timeout` line is also a keyword-with-a-value, and ir-ctl --receive
        prints one, so it must not truncate the capture either."""
        text = ("carrier 38000\ntimeout 20000\n"
                "pulse 9000 space 4500\npulse 560 space 560\n")
        got = I.parse_irctl_text(text)
        assert got == [("pulse", 9000), ("space", 4500), ("pulse", 560), ("space", 560)], (
            f"the capture after a timeout line came back {got}")


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
