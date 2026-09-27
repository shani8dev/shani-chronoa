"""The machine-state senses, and the re-arm contract they rely on.

These tests exist mostly because running the senses for real found four bugs
that reading the code did not: a schema in the wrong shape so the sense
silently refused to load, a `/dev` scan that missed every audio node because
ALSA lives in `/dev/snd`, an absolute-pattern `Path().glob` that raised
`NotImplementedError` instead of returning nothing, and a package-ownership
parse that marked every distribution daemon as third-party. Each of those
failed silently or inverted, so each has a test pinned to the real
behaviour.
"""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, "usr/lib/shani-chronoa")
from shani_chronoa.senses import latch  # noqa: E402
from shani_chronoa.senses.contention import _SOUND_NODE, _VIDEO_NODE  # noqa: E402


class TestReArm:
    def test_the_first_observation_always_emits(self):
        assert latch.Latch().should_emit("anything") is True

    def test_an_unchanged_value_is_suppressed(self):
        l = latch.Latch(rearm_seconds=900)
        l.should_emit("same")
        assert l.should_emit("same") is False

    def test_a_changed_value_emits_immediately(self):
        l = latch.Latch(rearm_seconds=900)
        l.should_emit("first")
        assert l.should_emit("second") is True

    def test_a_steady_value_re_emits_after_the_quiet_window(self):
        now = [0.0]
        l = latch.Latch(rearm_seconds=60, clock=lambda: now[0])
        l.should_emit("steady")
        now[0] = 59.0
        assert l.should_emit("steady") is False
        now[0] = 61.0
        assert l.should_emit("steady") is True, "a long-lived fact must re-state itself"

    def test_a_zero_window_is_rejected_rather_than_always_firing(self):
        with pytest.raises(ValueError):
            latch.Latch(rearm_seconds=0)

    def test_a_broken_equality_comparison_does_not_crash_the_poller(self):
        class Hostile:
            def __eq__(self, other):
                raise RuntimeError("no comparison today")

        l = latch.Latch(rearm_seconds=900)
        l.should_emit(Hostile())
        # Emits rather than suppresses: an object that cannot be compared is
        # not evidence of "unchanged", and silently dropping observations
        # because a sense returned something exotic is the failure direction.
        assert l.should_emit(Hostile()) is True

    def test_reset_makes_the_next_observation_emit(self):
        l = latch.Latch(rearm_seconds=900)
        l.should_emit("x")
        l.reset()
        assert l.should_emit("x") is True

    def test_the_registry_keeps_latches_independent(self):
        reg = latch.LatchRegistry(rearm_seconds=900)
        assert reg.should_emit("a", "value") is True
        assert reg.should_emit("b", "value") is True


class TestFingerprint:
    def test_an_unhashable_observation_becomes_hashable(self):
        assert isinstance(latch.fingerprint({"a": [1, 2]}), tuple)

    def test_key_order_does_not_change_the_fingerprint(self):
        assert latch.fingerprint({"a": 1, "b": 2}) == latch.fingerprint({"b": 2, "a": 1})

    def test_list_order_does_change_it(self):
        assert latch.fingerprint([1, 2]) != latch.fingerprint([2, 1])

    def test_an_unserialisable_value_still_produces_something_stable(self):
        assert latch.fingerprint({1, 2, 3}) == latch.fingerprint({3, 2, 1})


class TestDeviceNodePatterns:
    """Pinned to the node names a real machine actually has.

    The original pattern enumerated suffixes and matched 2 of a real machine's
    8 audio nodes - missing `pcmC0D0c`, the capture node, which is the
    microphone the sense exists to watch.
    """

    @pytest.mark.parametrize(
        "name",
        [
            "video0", "video1", "video10", "video99",
        ],
    )
    def test_video_nodes_match(self, name):
        assert _VIDEO_NODE.match(name)

    @pytest.mark.parametrize(
        "name", ["videoX", "videography", "video", "myvideo0", "video0extra"]
    )
    def test_non_video_names_do_not_match(self, name):
        assert not _VIDEO_NODE.match(name)

    @pytest.mark.parametrize(
        "name",
        [
            "controlC0",
            "hwC0D0",
            "hwC0D2",
            "pcmC0D0p",    # playback
            "pcmC0D0c",    # capture: the microphone
            "pcmC0D6c",    # capture
            "pcmC0D31p",   # two-digit device number
            "pcmC0D4p",
            "dmixC0D0p",
        ],
    )
    def test_real_alsa_nodes_match(self, name):
        assert _SOUND_NODE.match(name), f"{name} is a real ALSA node and must be seen"

    @pytest.mark.parametrize(
        "name", ["seq", "timer", "by-path", "snd", "pcm", "random", "null"]
    )
    def test_directories_and_unrelated_nodes_do_not_match(self, name):
        assert not _SOUND_NODE.match(name)


class TestTheShippedSensesAllLoad:
    """A malformed schema is skipped by the loader with a log line, so a sense
    that is registered-but-never-runnable looks merely absent."""

    @pytest.mark.parametrize(
        "name",
        ["contention", "thermal", "display", "network", "bluetooth", "camera", "privilege"],
    )
    def test_it_is_registered(self, name):
        from shani_chronoa.senses import discover_senses

        assert name in discover_senses()

    @pytest.mark.parametrize(
        "name",
        ["contention", "thermal", "display", "network", "bluetooth", "camera", "privilege"],
    )
    def test_it_declares_a_valid_schema_and_is_ambient(self, name):
        from shani_chronoa.senses import discover_senses, is_valid_schema

        sense = discover_senses()[name]
        assert is_valid_schema(sense.schema), f"{name}'s schema would be skipped at load"
        assert sense.is_ambient(), f"{name} is a polled sense and must say so"


class TestDeviceScanCoversBothDirectories:
    """ALSA nodes live in `/dev/snd/`, not `/dev/`.

    Reverting the scan to `/dev` alone was the one mutation these tests did
    *not* catch, because every other test pinned the regex - which was correct
    all along - and none of them asked whether the sound nodes were ever
    candidates. That bug hid every audio device including `pcmC0D0c`, the
    microphone the contention sense exists to watch, while the sense still
    loaded, still passed its schema check and still reported a plausible
    answer. So the directories themselves are asserted here, not the pattern.
    """

    def test_both_dev_and_dev_snd_are_enumerated(self, monkeypatch):
        from shani_chronoa.senses import contention

        listed = []

        def _fake_listdir(path):
            listed.append(str(path))
            if str(path) == "/dev":
                return ["video0", "video1", "snd", "null", "random"]
            if str(path) == "/dev/snd":
                return ["controlC0", "pcmC0D0c", "pcmC0D0p", "hwC0D0", "seq", "timer"]
            raise OSError(path)

        monkeypatch.setattr(contention.os, "listdir", _fake_listdir)
        found = {str(p) for p in contention._dev_nodes()}

        assert "/dev" in listed, "the video nodes are never looked for"
        assert "/dev/snd" in listed, (
            "ALSA nodes live in /dev/snd; without it the microphone node "
            "pcmC0D0c is invisible and every audio device reports as absent"
        )
        assert "/dev/video0" in found
        assert "/dev/snd/pcmC0D0c" in found, "the capture node is the microphone"
        assert "/dev/snd/pcmC0D0p" in found
        assert "/dev/snd/hwC0D0" in found
        assert not any(p.endswith(("/seq", "/timer", "/by-path")) for p in found)

    def test_an_unreadable_directory_is_skipped_not_raised(self, monkeypatch):
        from shani_chronoa.senses import contention

        def _fake_listdir(path):
            if str(path) == "/dev/snd":
                raise PermissionError(path)
            return ["video0"]

        monkeypatch.setattr(contention.os, "listdir", _fake_listdir)

        assert [str(p) for p in contention._dev_nodes()] == ["/dev/video0"]


class TestRadioSense:
    """`/proc/net/wireless` is fixed-width with trailing decimal points.

    Reading it as plain integers skipped every line, and the sense reported
    "no wireless interface" on a machine that had one - a confident wrong
    answer rather than a failure, which is the worst shape for a sensor.
    """

    _SAMPLE = (
        "Inter-| sta-|   Quality        |   Discarded packets               | Missed\n"
        " face | tus | link level noise |  nwid  crypt   frag  retry   misc | beacon | 22\n"
        "wlp0s20f3: 0000   70.  -40.  -256        0      0      0      0    104        0\n"
    )

    def test_it_parses_the_trailing_dot_format(self, monkeypatch):
        from shani_chronoa.senses import rfsense
        from pathlib import Path

        monkeypatch.setattr(
            rfsense, "_WIRELESS", type("P", (), {"read_text": staticmethod(lambda: self._SAMPLE)})()
        )
        links = rfsense.read_links()
        assert len(links) == 1, f"the fixed-width format was not parsed: {links}"
        assert links[0]["interface"] == "wlp0s20f3"
        assert links[0]["link"] == 70
        assert links[0]["level"] == -40

    def test_a_malformed_line_is_skipped_not_guessed(self, monkeypatch):
        from shani_chronoa.senses import rfsense

        text = "h1\nh2\ngarbage\nwlp0s20f3: 0000   70.  -40.  -256  0 0 0 0 0 0\n"
        monkeypatch.setattr(
            rfsense, "_WIRELESS", type("P", (), {"read_text": staticmethod(lambda: text)})()
        )
        assert len(rfsense.read_links()) == 1

    def test_movement_needs_a_real_spread_not_one_sample(self, monkeypatch):
        from shani_chronoa.senses import rfsense

        rfsense._HISTORY_BY_IFACE.clear()
        first = rfsense.movement("wlan0", -40)
        assert first["moving"] is False, "a single sample cannot show variance"
        rfsense.movement("wlan0", -38)
        steady = rfsense.movement("wlan0", -39)
        assert steady["moving"] is False
        rfsense.movement("wlan0", -50)
        jumpy = rfsense.movement("wlan0", -39)
        assert jumpy["moving"] is True, "a 11dB swing is movement"
        rfsense._HISTORY_BY_IFACE.clear()

    def test_the_window_is_bounded(self, monkeypatch):
        from shani_chronoa.senses import rfsense

        rfsense._HISTORY_BY_IFACE.clear()
        for i in range(rfsense._HISTORY * 3):
            rfsense.movement("wlan0", -40 + (i % 2))
        assert len(rfsense._HISTORY_BY_IFACE["wlan0"]) == rfsense._HISTORY
        rfsense._HISTORY_BY_IFACE.clear()

    def test_the_ceiling_names_the_actual_hardware_limit(self):
        from shani_chronoa.senses import rfsense

        text = rfsense.sensing_ceiling()
        assert "CSI" in text or "not verified" in text
        assert "ESP32" in text or "5300" in text or "not verified" in text


class TestThermalGridSense:
    def test_it_is_registered_under_a_name_a_gschema_key_can_derive(self):
        """gschema rejects underscores, so `thermal_array` was ungrantable."""
        from shani_chronoa.senses import discover_senses, is_valid_schema

        registry = discover_senses()
        assert "thermalgrid" in registry
        assert "_" not in registry["thermalgrid"].name
        assert is_valid_schema(registry["thermalgrid"].schema)
        assert registry["thermalgrid"].schema["function"]["name"] == registry["thermalgrid"].name

    def test_every_sense_name_is_a_legal_gschema_key_fragment(self):
        import re as _re
        from shani_chronoa.senses import discover_senses

        # glib-compile-schemas discards the ENTIRE schema file on one illegal
        # key name, taking every other key with it, so the rule is worth
        # asserting across the whole registry rather than for one sense.
        for name in discover_senses():
            assert _re.fullmatch(r"[a-z0-9-]+", name), (
                f"{name!r} cannot appear in a gschema key; the whole schema "
                "file is discarded if one key name is illegal"
            )


class TestThermalGridProbeHonesty:
    """A scan that could not read anything must not report "nothing found".

    `i2cdetect` prints "Permission denied" on stderr and exits 0, so an
    unprivileged scan is byte-for-byte indistinguishable from a clean one that
    found no device. Collapsing them produced a confident false negative: on a
    machine that *had* a thermal array, Chronoa would have said there was
    none, with nothing anywhere to suggest otherwise.
    """

    _DENIED = "Error: Could not open file `/dev/i2c-1': Permission denied\nRun as root?"

    def _fake_run(self, stdout, stderr=""):
        import subprocess as sp

        def _run(argv, **kwargs):
            return sp.CompletedProcess(argv, 0, stdout=stdout, stderr=stderr)

        return _run

    def test_a_denied_scan_is_not_reported_as_a_clean_one(self, monkeypatch):
        import subprocess as sp
        from shani_chronoa.senses import thermalgrid

        monkeypatch.setattr(sp, "run", self._fake_run("", self._DENIED))
        result = thermalgrid.probe()

        assert result.arrays == []
        assert result.determined is False, "nothing was read, so nothing was determined"
        assert result.denied, "the refusal must be recorded"

    def test_a_clean_scan_of_an_empty_bus_is_a_determined_negative(self, monkeypatch):
        import subprocess as sp
        from shani_chronoa.senses import thermalgrid

        empty = "     0  1  2  3  4  5  6  7  8  9  a  b  c  d  e  f\n" \
                "00:                         -- -- -- -- -- -- -- --\n"
        monkeypatch.setattr(sp, "run", self._fake_run(empty, ""))
        result = thermalgrid.probe()

        assert result.determined is True
        assert result.found is False

    def test_a_present_array_is_detected(self, monkeypatch):
        import subprocess as sp
        from shani_chronoa.senses import thermalgrid

        hit = "     0  1  2  3  4  5  6  7  8  9  a  b  c  d  e  f\n" \
              "00:                         -- -- -- 33 -- -- -- --\n"
        monkeypatch.setattr(sp, "run", self._fake_run(hit, ""))
        result = thermalgrid.probe()

        assert result.found is True
        assert result.arrays[0]["part"] == "MLX90640"
        assert result.arrays[0]["pixels"] == 768

    def test_a_refusal_overrides_a_table_that_arrived_anyway(self, monkeypatch):
        """The stderr check is not redundant with the empty-stdout check.

        With a denied scan the two agree, so a test that only supplies empty
        stdout cannot tell whether the permission branch is doing any work -
        removing it entirely left the suite green. A table that arrives on
        stdout *despite* the refusal is the case that separates them, and
        trusting that table would be believing a scan that never completed.
        """
        import subprocess as sp
        from shani_chronoa.senses import thermalgrid

        table = (
            "     0  1  2  3  4  5  6  7  8  9  a  b  c  d  e  f\n"
            "00:  -- -- -- 33 -- -- -- --\n"
        )
        monkeypatch.setattr(
            sp, "run",
            lambda argv, **kw: sp.CompletedProcess(
                argv, 0, stdout=table,
                stderr="Error: Permission denied\nRun as root?",
            ),
        )
        result = thermalgrid.probe()

        assert result.determined is False
        assert result.found is False, "a refused scan must not be believed"
        assert result.denied

    def test_escalation_is_opt_in_and_argv_only(self, monkeypatch):
        """A sense the model can call must not silently ask for a password."""
        import subprocess as sp
        from shani_chronoa.senses import thermalgrid

        seen = []

        def _run(argv, **kwargs):
            seen.append(argv)
            return sp.CompletedProcess(argv, 0, stdout="", stderr="")

        monkeypatch.setattr(sp, "run", _run)
        thermalgrid.probe(escalate=False)
        assert all("pkexec" not in argv for argv in seen), "escalated without being asked"

        seen.clear()
        thermalgrid.probe(escalate=True)
        assert any("pkexec" in argv for argv in seen)
        for argv in seen:
            assert argv[0] == "pkexec" and argv[1].endswith("i2cdetect")
            assert argv[-1].isdigit(), "the bus number must reach argv as digits only"

    def test_a_bus_name_that_is_not_a_bus_number_is_never_passed_on(self):
        from pathlib import Path as _P
        from shani_chronoa.senses import thermalgrid

        assert thermalgrid._bus_number(_P("/dev/i2c-7")) == "7"
        assert thermalgrid._bus_number(_P("/dev/i2c-;rm -rf /")) is None
