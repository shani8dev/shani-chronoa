"""`bluetooth_gatt`'s `listen` action, and the two byte-parsing bugs behind it.

A `read` cannot answer "what is my heart rate right now". Heart Rate
Measurement (0x2A37) is notify-only: the device refuses the read and answers
only while it is *producing* a measurement. `listen` subscribes and collects,
which is what a phone app does - and is the one piece of the wearable surface
that Da Fit's `sendECGHeartRate` / streaming behaviour actually corresponds to.

**Both bugs in this file were found by reading the installed binary, not the
skill.** `strings $(which gatttool)` gives the formats:

    Characteristic value/descriptor: 0e 1e     <- a read
    handle: 0x0022 <tab> value: 0e 1e          <- a notification

1. `_READ_VALUE` was `([0-9a-fA-F]+)`, which stops at the **first space**. Every
   multi-byte read was truncated to its first byte. Battery Level (0x2A19) is
   one byte, so it read correctly and hid the defect completely - while a
   Firmware Revision String of `1.2.3` was reported as **`1`**, and every packed
   structure came back one byte long.
2. `_NOTIFY_LINE` had the same shape, capturing `0e` out of `0e 1e` - so every
   genuine reading arrived as a one-byte payload and was rejected as truncated.
   The skill would have reported every live heart rate as unreadable, forever,
   and the "nothing arrived" path - which is honest and correct for a device
   that is not being worn - would never have been reached, so the bug could not
   have been caught by watching the device sit unused.

And a third, which is the one that made the first two survivable:

3. `decode_heart_rate` read flags bit 0 as "uint8", so **every 8-bit
   measurement** - i.e. almost all of them - was rejected with "the measurement
   did not say whether the rate is 8 or 16 bit", the sentence you write for a
   device that failed to set a bit rather than one that correctly cleared it.

Run: `python3 -m pytest tests/test_bluetooth_listen_skill.py`
"""

from __future__ import annotations

import os
import subprocess
import sys

import pytest

sys.path.insert(0, "usr/lib/shani-chronoa")

from shani_chronoa.skills import bluetooth_gatt as bg  # noqa: E402


# --- the two parsers, against the format the installed binary prints ----------


#: `gatttool --char-desc` for a heart-rate characteristic whose value is at
#: 0x0022 and whose CCCD (0x2902) follows it, as the real tool prints it.
CHAR_DESC = ("handle = 0x0021, uuid = 00002803-0000-1000-8000-00805f9b34fb\n"
             "handle = 0x0022, uuid = 00002a37-0000-1000-8000-00805f9b34fb\n"
             "handle = 0x0023, uuid = 00002902-0000-1000-8000-00805f9b34fb\n")


class TestTheByteParsersTakeEveryByte:
    def test_a_read_of_several_bytes_keeps_all_of_them(self):
        m = bg._READ_VALUE.search("Characteristic value/descriptor: 0e 1e")
        assert m is not None
        assert "".join(m.group(1).split()) == "0e1e", (
            "a multi-byte read was truncated; the old pattern stopped at the "
            "first space"
        )

    def test_a_firmware_revision_string_is_no_longer_cut_to_its_first_character(self):
        """`31 2e 32 2e 33` is `1.2.3`; the old parser returned `31` -> `1`."""
        m = bg._READ_VALUE.search("Characteristic value/descriptor: 31 2e 32 2e 33")
        assert "".join(m.group(1).split()) == "312e322e33"
        assert bg.describe_value("Firmware Revision", "312e322e33")[0] == "1.2.3"

    def test_a_manufacturer_name_decodes_to_a_company_and_not_a_number(self):
        """`4d 65 74 61` is `Meta`, not `77`."""
        assert bg.describe_value("Manufacturer Name", "4d657461")[0] == "Meta"

    def test_a_single_byte_read_is_still_just_the_byte(self):
        m = bg._READ_VALUE.search("Characteristic value/descriptor: 12")
        assert "".join(m.group(1).split()) == "12"

    def test_a_notification_takes_every_byte_too(self):
        line = "handle: 0x0022 \t value: 0e 1e"
        m = bg._NOTIFY_LINE.search(line)
        assert m is not None
        assert "".join(m.group(1).split()) == "0e1e", (
            "the notification parser stopped at the first space, so every live "
            "reading arrived one byte long"
        )

    def test_a_notification_line_is_matched_in_the_whole_line_not_just_the_start(self):
        for line in ("handle: 0x0022 \t value: 06 4a 00",
                     "handle: 0x0022 \t value: 00 34 12 04 00"):
            m = bg._NOTIFY_LINE.search(line)
            assert m is not None, line
            assert len("".join(m.group(1).split())) >= 4, line


class TestBothPathsProduceTheSameShapeOfValue:
    """`read` and `listen` hand their bytes to the same decoders, so they have to
    agree on the form.

    `bytes.fromhex()` skips whitespace, so leaving the spaces in would not break
    the hex decoding - which is exactly why it can be dropped unnoticed. It does
    break the arithmetic the decoders do around it: `describe_value` branches on
    `len(raw) <= 2`, and a space makes a two-byte value look four characters
    long. `'0e 1e'` is len 5 and `'0e1e'` is len 4, so today both land on the
    same side of that test - but the count is being relied upon, and a path that
    disagreed with the other would be reading a length that includes formatting.
    """

    def test_a_read_returns_the_payload_with_no_spaces_in_it(self, monkeypatch):
        monkeypatch.setattr(bg, "_gatttool",
                            lambda mac, *a: "Characteristic value/descriptor: 06 4a 00")
        raw, _problem = bg.read_value("AA:BB:CC:DD:EE:FF", 0x22, "Heart Rate Measurement", "")
        assert raw == "064a00", f"a read left formatting in the payload: {raw!r}"

    def test_a_notification_returns_the_same_form_for_the_same_payload(self, monkeypatch):
        # `notifications` reads `--char-desc` to find the value's CCCD before
        # subscribing, so the fake gatttool answers that as well as the read.
        self_lines = ["handle: 0x0022 \t value: 06 4a 00"]
        read_fd, write_fd = os.pipe()

        class _Proc:
            pid = 1

            def __init__(self):
                self.stdout = os.fdopen(read_fd, encoding="utf-8")

            # `subprocess.run` does `with Popen(...) as process`, and
            # `subprocess.Popen` is the *same module attribute* this test just
            # replaced - so every busctl call in the module goes through here
            # too. The stub needs the context-manager protocol or the bluez
            # route raises TypeError instead of being skipped.
            def __enter__(self):
                return self

            def __exit__(self, *exc):
                return False

            def poll(self):
                return None

            def wait(self, timeout=None):
                return 0

        def fake_popen(argv, **kwargs):
            os.write(write_fd, ("\n".join(self_lines) + "\n").encode())
            os.close(write_fd)
            return _Proc()

        monkeypatch.setattr(bg.shutil, "which", lambda name: "/usr/bin/gatttool")
        monkeypatch.setattr(bg.subprocess, "Popen", fake_popen)
        monkeypatch.setattr(bg.os, "getpgid", lambda pid: pid)
        monkeypatch.setattr(bg.os, "killpg", lambda pgid, sig: None)
        # No such device on this bus, so the bluez route is skipped rather than
        # reached through the patched Popen.
        monkeypatch.setattr(bg, "_bluez_char_paths", lambda mac: {})

        monkeypatch.setattr(bg, "_gatttool",
                            lambda mac, *a: CHAR_DESC if "--char-desc" in a
                            else "Characteristic value/descriptor: 06 4a 00")
        read_raw, _p = bg.read_value("AA:BB:CC:DD:EE:FF", 0x22, "Heart Rate Measurement", "")
        heard, _q = bg.notifications("AA:BB:CC:DD:EE:FF", 0x22, 1)

        assert heard == [read_raw], (
            f"read returned {read_raw!r} and listen returned {heard!r}; the two "
            "paths hand the same bytes to the same decoders"
        )


# --- the heart-rate layout, including the inverted bit that hid it -----------


class TestHeartRateIsDecodedRatherThanPrintedAsBytes:
    def test_an_eight_bit_rate_is_the_common_case_and_is_decoded(self):
        # 0x06 = 8-bit rate, sensor-contact bit present; rate 0x4a = 74.
        assert bg.decode_heart_rate("06 4a 00") == ("74", "sensor contact LOST - the reading is unreliable")

    def test_flags_bit_zero_means_uint16_not_uint8(self):
        """0x01 set -> two bytes, little-endian. Inverted, this rejected every
        8-bit measurement, which is nearly all of them."""
        assert bg.decode_heart_rate("01 48 00")[0] == "72"

    def test_a_plain_eight_bit_measurement_with_no_extras(self):
        assert bg.decode_heart_rate("00 48") == ("72", "")

    def test_sensor_contact_detected_is_reported_as_such(self):
        bpm, note = bg.decode_heart_rate("04 48 01")
        assert bpm == "72"
        assert note == "sensor contact detected"

    def test_a_lost_contact_reading_is_flagged_as_unreliable(self):
        _bpm, note = bg.decode_heart_rate("06 48 00")
        assert "LOST" in note and "unreliable" in note, (
            "a dropped sensor contact reported as a clean number is the "
            "confidently-wrong answer this skill must not give"
        )

    def test_an_rr_interval_is_converted_from_sixty_fourths_of_a_second(self):
        # 0x0400 = 1024 / 1024 = 1.000 s.
        _bpm, note = bg.decode_heart_rate("14 48 01 00 04 00")
        assert "1.000s" in note, note

    def test_a_truncated_measurement_says_which_half_is_missing(self):
        bpm, note = bg.decode_heart_rate("01 48")
        assert bpm == "" and "16-bit" in note

    def test_payloads_that_are_not_measurements_are_refused_not_guessed_at(self):
        for raw, fragment in (("", "empty"), ("zz", "hex")):
            bpm, note = bg.decode_heart_rate(raw)
            assert bpm == ""
            assert fragment in note.lower()

    def test_a_read_that_does_get_a_value_reports_beats_not_bytes(self):
        """`describe_value` is the `read` path; it must not still say "hex"."""
        readable, _note = bg.describe_value("Heart Rate Measurement", "06 4a 00")
        assert readable == "74 bpm"


# --- `listen` itself ---------------------------------------------------------


class TestListenCollectsFromARealStream:
    """`notifications()` is driven over a **real pipe carrying the real format**.

    Only the gatttool binary is replaced. The parsing, the reading loop and the
    teardown are the module's own, because those are what is under test: a stub
    that skipped straight to a returned list would pass with the parser reverted,
    which is exactly how the one-byte bug survived a green suite.
    """

    def _listen(self, monkeypatch, lines: list[str], handle: int = 0x22):
        seen: list[list[str]] = []
        read_fd, write_fd = os.pipe()

        # Force the gatttool route. On a machine with bluez installed the bluez
        # route is tried first, so a test that meant to exercise gatttool would
        # otherwise depend on whether `AA:BB:CC:DD:EE:FF` happens to be paired.
        monkeypatch.setattr(bg, "_listen_over_bluez", lambda *a: None)
        # The CCCD lookup that precedes subscribing (`gatttool --char-desc`).
        monkeypatch.setattr(bg, "_gatttool", lambda mac, *a: CHAR_DESC)

        class _Proc:
            def __init__(self):
                self.pid = 1
                self.stdout = os.fdopen(read_fd, encoding="utf-8")

            def poll(self):
                return None

            def wait(self, timeout=None):
                return 0

        def fake_popen(argv, **kwargs):
            seen.append(argv)
            os.write(write_fd, ("\n".join(lines) + "\n").encode())
            os.close(write_fd)
            return _Proc()

        monkeypatch.setattr(bg.shutil, "which", lambda name: "/usr/bin/gatttool")
        monkeypatch.setattr(bg.subprocess, "Popen", fake_popen)
        monkeypatch.setattr(bg.os, "getpgid", lambda pid: pid)
        monkeypatch.setattr(bg.os, "killpg", lambda pgid, sig: None)
        return bg.notifications("AA:BB:CC:DD:EE:FF", handle, 1), seen

    def test_it_asks_gatttool_to_listen_on_the_handle_it_was_given(self, monkeypatch):
        (_values, _problem), seen = self._listen(
            monkeypatch, ["handle: 0x0022 \t value: 0e 1e"])
        # Subscribing is a write of 0x0100 to the value's own CCCD - 0x0023 in
        # CHAR_DESC, looked up rather than assumed - with `--listen` after it.
        assert seen and "--listen" in seen[0]
        argv = seen[0]
        assert argv[argv.index("-a") + 1] == "0x0023" and "0100" in argv, argv

    def test_it_collects_several_readings_and_keeps_their_order(self, monkeypatch):
        (values, problem), _seen = self._listen(monkeypatch, [
            "handle: 0x0022 \t value: 064a00",
            "handle: 0x0022 \t value: 064c00",
            "handle: 0x0022 \t value: 064a00",   # a repeat, not a new reading
        ])
        assert problem == ""
        assert values == ["064a00", "064c00"], values

    def test_a_whole_payload_survives_the_collector_not_just_the_regex(self, monkeypatch):
        """The end-to-end version of the one-byte bug: if `notifications()`
        truncated, this is where it would show, and every reading would be
        reported as unreadable."""
        (values, _problem), _seen = self._listen(
            monkeypatch, ["handle: 0x0022 \t value: 064a00"])
        assert values == ["064a00"], values
        assert bg.decode_heart_rate(values[0])[0] == "74"

    def test_a_stream_that_sends_nothing_yields_nothing_rather_than_an_error(self, monkeypatch):
        (values, problem), _seen = self._listen(monkeypatch, [])
        assert values == [] and problem == ""

    def test_a_missing_gatttool_is_reported_rather_than_guessed_at(self, monkeypatch):
        monkeypatch.setattr(bg, "_listen_over_bluez", lambda *a: None)
        monkeypatch.setattr(bg.shutil, "which", lambda name: None)
        with pytest.raises(bg.GattUnavailable) as exc:
            bg.notifications("AA:BB:CC:DD:EE:FF", 0x22, 1)
        assert "bluez" in str(exc.value) or "gatttool" in str(exc.value)


class TestTheBluezRoute:
    """The route that actually works on a desktop.

    `gatttool` needs a connection of its own and bluez holds the link to any
    in-range paired device - measured here: `bluetoothctl info` reporting
    `Connected: yes` at RSSI -67, with every gatttool call answering
    `Device or resource busy (16)`. So a gatttool-only `listen` works only in the
    seconds after a manual disconnect.

    Two bugs in this route were introduced *by* fixing the contention, and both
    were caught by running against the real device:

    - **`busctl get-property` takes the service name too** -
      `get-property SERVICE OBJECT INTERFACE PROPERTY`. Omitting it returns empty
      for every property, so all 21 characteristics of a connected device read
      as absent. Found because a lookup that silently finds nothing is
      indistinguishable from a device with nothing.
    - **`ay 0` is an empty array, not a zero reading.** The first version read it
      as the byte `0` and reported **"Battery Level: 0%"** for a device that had
      never sent anything - a believable, confidently wrong number about a
      battery. And `ay 1 00` parsed as `100`, because the array length was taken
      for a byte, so every real value would have been off by one byte.
    """

    def test_an_empty_array_is_not_a_zero_reading(self):
        """The control for the worst bug above: `ay 0` must yield nothing."""
        assert bg._parse_bluez_value("ay 0") is None

    def test_the_array_length_is_not_mistaken_for_a_byte(self):
        assert bg._parse_bluez_value("ay 1 00") == "00"
        assert bg._parse_bluez_value("ay 1 12") == "12"

    def test_several_bytes_are_all_kept_in_order(self):
        assert bg._parse_bluez_value("ay 2 06 4a") == "064a"
        assert bg._parse_bluez_value("ay 11 74 76 81 70 78 74 70 68 49 46 48") == \
            "7476817078747068494648"

    def test_a_truncated_array_is_refused_rather_than_halved(self):
        assert bg._parse_bluez_value("ay 3 0a 0b") is None, (
            "two of three bytes reported as a value would be inventing the third"
        )

    def test_anything_unrecognisable_is_refused(self):
        for text in ("nonsense", "", "int 5"):
            assert bg._parse_bluez_value(text) is None

    def test_it_falls_back_to_gatttool_when_bluez_has_no_such_characteristic(self, monkeypatch):
        monkeypatch.setattr(bg, "_bluez_char_paths", lambda mac: {})
        called: list[int] = []

        class _Proc:
            pid = 1
            stdout = None

            def poll(self):
                return 0

            def wait(self, timeout=None):
                return 0

        read_fd, write_fd = os.pipe()

        def fake_popen(argv, **kwargs):
            called.append(1)
            os.write(write_fd, b"handle: 0x0022 \t value: 06 4a 00\n")
            os.close(write_fd)
            proc = _Proc()
            proc.stdout = os.fdopen(read_fd, encoding="utf-8")
            return proc

        monkeypatch.setattr(bg.shutil, "which", lambda name: "/usr/bin/gatttool")
        monkeypatch.setattr(bg.subprocess, "Popen", fake_popen)
        monkeypatch.setattr(bg.os, "killpg", lambda pgid, sig: None)
        monkeypatch.setattr(bg, "_gatttool", lambda mac, *a: CHAR_DESC)
        values, _problem = bg.notifications("AA:BB:CC:DD:EE:FF", 0x22, 1)
        assert called and values == ["064a00"], (called, values)

    def test_a_bluez_property_read_includes_the_service_name(self, monkeypatch):
        """The live bug this file exists beside, and the one no other test sees.

        Every other test in this class stubs `_system_busctl`, so the
        **argument list is never checked** - and the omission that actually
        happened was an argument. `busctl get-property` is
        `get-property SERVICE OBJECT INTERFACE PROPERTY`; leaving the service
        out returns empty for every property without raising, so all 21
        characteristics of a connected device read as absent, and no assertion
        anywhere could tell.

        So the shape of the command is asserted directly - the same trade the
        rest of this repo makes for interfaces: pin the shape, not the text.
        """
        seen: list[tuple[str, ...]] = []

        def fake(*a, **k):
            seen.append(a)
            return subprocess.CompletedProcess(a, 0, "ay 1 06", "")

        monkeypatch.setattr(bg, "_system_busctl", fake)
        assert bg._bluez_property("/org/bluez/hci0/dev_X/service0001/char0002", "Value") \
            == "ay 1 06"
        args = seen[0]
        assert args[:2] == ("get-property", "org.bluez"), (
            f"busctl get-property needs the service name first; got {args!r}. "
            "Without it every property comes back empty and every "
            "characteristic reads as absent."
        )
        assert args[2].startswith("/org/bluez/"), args
        assert args[3] == "org.bluez.GattCharacteristic1", args
        assert args[4] == "Value", args

    def test_a_bluez_property_that_fails_reads_as_absent_not_as_a_value(self, monkeypatch):
        monkeypatch.setattr(
            bg, "_system_busctl",
            lambda *a, **k: subprocess.CompletedProcess(a, 1, "", "No such object"))
        assert bg._bluez_property("/nope", "Handle") is None

    def test_the_bluez_route_subscribes_and_always_unsubscribes(self, monkeypatch):
        """A notify session left running would hold the connection and make
        every later read slower or impossible."""
        calls: list[tuple[str, ...]] = []
        device = "/org/bluez/hci0/dev_AA_BB_CC_DD_EE_FF"
        char = device + "/service0021/char0022"

        def fake(*args, **kwargs):
            calls.append(args)
            if args[0] == "tree":
                return subprocess.CompletedProcess(
                    args, 0, f"  ├─ {device}/service0021\n"
                             f"  │ │ └─ {char}\n", "")
            if args[0] == "get-property":
                return subprocess.CompletedProcess(
                    args, 0, "ay 1 06" if args[-1] == "Value" else "34", "")
            return subprocess.CompletedProcess(args, 0, "", "")

        monkeypatch.setattr(bg, "_system_busctl", fake)
        monkeypatch.setattr(bg.shutil, "which", lambda name: "/usr/bin/gatttool")
        # The window has to close; the clock is the only thing that stops the loop.
        ticks = iter([0.0, 0.1, 0.2, 99.0])
        monkeypatch.setattr(bg.time, "monotonic", lambda: next(ticks, 99.0))
        monkeypatch.setattr(bg.time, "sleep", lambda _s: None)

        values, problem = bg.notifications("AA:BB:CC:DD:EE:FF", 0x22, 5)
        methods = [c[4] for c in calls if c[0] == "call"]
        assert methods == ["StartNotify", "StopNotify"], calls
        assert values == ["06"], values
        assert problem == ""

    def test_a_bluez_start_that_fails_falls_back_rather_than_claiming_success(self, monkeypatch):
        monkeypatch.setattr(bg, "_bluez_char_paths", lambda mac: {0x22: "/some/char0022"})

        def fake(*args, **kwargs):
            if args[0] == "call" and args[4] == "StartNotify":
                return subprocess.CompletedProcess(args, 1, "", "No such method")
            return subprocess.CompletedProcess(args, 0, "", "")

        monkeypatch.setattr(bg, "_system_busctl", fake)
        assert bg._listen_over_bluez("AA:BB:CC:DD:EE:FF", 0x22, 1, lambda v: None) is None

    def test_a_bluez_device_absent_from_the_tree_is_a_fallback_not_a_failure(self, monkeypatch):
        monkeypatch.setattr(bg, "_bluez_char_paths", lambda mac: {})
        monkeypatch.setattr(bg, "_system_busctl",
                            lambda *a, **k: subprocess.CompletedProcess(a, 0, "", ""))
        assert bg._listen_over_bluez("AA:BB:CC:DD:EE:FF", 0x22, 1, lambda v: None) is None


class TestTheListenActionThroughTheRealDispatcher:
    """Driven through `_run`, so the consent gate and the refusals are the
    module's own rather than this file's re-implementation."""

    #: The exact SIG name. `_find` matches on the name this module knows, so
    #: "heart rate" is refused and the refusal lists what is on offer - that is
    #: the existing, deliberate behaviour for every characteristic.
    HR = "Heart Rate Measurement"

    def _device(self, monkeypatch):
        monkeypatch.setattr(bg, "paired", lambda: [("B3:69:73:62:B2:B6", "FB BGS002")])
        monkeypatch.setattr(bg, "attributes", lambda mac: [
            (0x22, "00002a37-0000-1000-8000-00805f9b34fb", "Characteristic"),
            (0x19, "00002a19-0000-1000-8000-00805f9b34fb", "Characteristic"),
        ])

    def _granted(self, monkeypatch):
        class _On:
            def get_bool(self, key, default=False):
                return True
        monkeypatch.setattr(bg, "ChronoaConfig", _On)

    def test_it_refuses_when_the_consent_key_is_off_and_names_it(self, monkeypatch):
        self._device(monkeypatch)

        class _Off:
            def get_bool(self, key, default=False):
                return default
        monkeypatch.setattr(bg, "ChronoaConfig", _Off)
        out = bg._run({"action": "listen", "device": "FB", "characteristic": self.HR})
        assert bg._CONSENT_KEY in out, out

    def test_a_whole_number_of_seconds_is_the_only_thing_accepted(self, monkeypatch):
        self._device(monkeypatch)
        self._granted(monkeypatch)
        for bad in ("soon", "abc"):
            out = bg._run({"action": "listen", "device": "FB",
                           "characteristic": self.HR, "seconds": bad})
            assert "seconds" in out, out

    def test_the_listening_window_is_bounded(self, monkeypatch):
        self._device(monkeypatch)
        self._granted(monkeypatch)
        for bad in (0, 61, -5):
            out = bg._run({"action": "listen", "device": "FB",
                           "characteristic": self.HR, "seconds": bad})
            assert "1 and 60" in out, (bad, out)

    def test_a_device_that_pushes_nothing_says_so_without_pretending_to_have_looked(
        self, monkeypatch
    ):
        """The honest negative: a strap on a table has no heart rate to send,
        and "nothing arrived" is a true answer, not a failure to hide behind."""
        self._device(monkeypatch)
        self._granted(monkeypatch)
        monkeypatch.setattr(bg, "notifications", lambda mac, h, s: ([], ""))
        out = bg._run({"action": "listen", "device": "FB",
                       "characteristic": self.HR, "seconds": 5})
        assert f"sent no {self.HR}" in out, out
        assert "real answer" in out, out

    def test_the_readings_are_reported_as_a_series_and_decoded(self, monkeypatch):
        self._device(monkeypatch)
        self._granted(monkeypatch)
        monkeypatch.setattr(bg, "notifications",
                            lambda mac, h, s: (["064a00", "064c00"], ""))
        out = bg._run({"action": "listen", "device": "FB",
                       "characteristic": self.HR, "seconds": 5})
        assert "2 reading(s)" in out, out
        assert "74 bpm" in out and "76 bpm" in out, out

    def test_a_read_of_a_notify_only_characteristic_points_at_listen(self, monkeypatch):
        """The dead end this action exists to remove: `read` cannot answer it,
        so the refusal has to name the thing that can."""
        self._device(monkeypatch)
        self._granted(monkeypatch)
        monkeypatch.setattr(bg, "read_value",
                            lambda mac, h, label, exp: ("", bg._NOTIFY_ONLY.get(label, "nope")))
        out = bg._run({"action": "read", "device": "FB", "characteristic": self.HR})
        assert "listen" in out, out

    def test_an_unknown_action_lists_the_ones_that_exist(self, monkeypatch):
        self._device(monkeypatch)
        self._granted(monkeypatch)
        out = bg._run({"action": "stream", "device": "FB"})
        assert "listen" in out, out

    def test_the_schema_advertises_the_action_so_the_model_can_choose_it(self):
        """A tool the schema does not offer is a tool the model cannot call."""
        enum = bg.SCHEMA["function"]["parameters"]["properties"]["action"]["enum"]
        assert "listen" in enum
        assert "seconds" in bg.SCHEMA["function"]["parameters"]["properties"]
