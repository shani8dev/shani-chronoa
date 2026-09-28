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

    @pytest.fixture(autouse=True)
    def _i2cdetect_present(self, monkeypatch):
        """pretend i2cdetect is installed, so the mocked run is actually reached.

        probe() returns early when `shutil.which("i2cdetect") is None`, before
        it ever calls subprocess. These tests mock subprocess.run precisely so
        the scan is exercised, but on a runner i2c-tools is absent, so the early
        return fired and every assertion below was made against an empty result
        the mock never produced. The point of the class is how a scan is
        REPORTED, not whether the tool is installed, so the tool's presence is
        stubbed rather than installed.
        """
        import shani_chronoa.senses.thermalgrid as tg

        monkeypatch.setattr(tg.shutil, "which",
                            lambda name, *a, **k: "/usr/sbin/i2cdetect"
                            if name == "i2cdetect" else None)

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


class TestHwmonSentinelHandling:
    """A driver declares more channels than the board wires up.

    On a real machine `thinkpad` exposes temp1..temp8 and temp4..temp8 all read
    back exactly `0` - 0.0 degrees, impossible on a running laptop. Reporting
    those as readings would put five fictitious zero-degree sensors in front of
    a user, and a plausible zero is worse than an absent value because it is
    indistinguishable from a real reading until something acts on it.
    """

    def _chip(self, tmp_path, name, attrs):
        chip = tmp_path / f"hwmon_{name}"
        chip.mkdir(parents=True, exist_ok=True)
        (chip / "name").write_text(name)
        for filename, value in attrs.items():
            (chip / filename).write_text(str(value))
        return chip

    def test_a_zero_temperature_is_unpopulated_not_zero_degrees(self, tmp_path, monkeypatch):
        from shani_chronoa.senses import hwmon

        chip = self._chip(tmp_path, "thinkpad", {
            "temp1_input": "90000", "temp1_label": "CPU",
            "temp4_input": "0",
        })
        monkeypatch.setattr(hwmon, "_chips", lambda: [chip])

        out = hwmon.read_chips()[0]
        temps = {c["channel"]: c for c in out["channels"]["temp"]}
        assert temps["temp1"]["celsius"] == 90.0
        assert temps["temp1"]["state"] == "ok"
        assert temps["temp4"]["state"] == "unpopulated"
        assert "celsius" not in temps["temp4"], "an unwired slot must not carry a value"

    def test_an_implausibly_hot_reading_is_a_bad_read_not_a_temperature(self, tmp_path, monkeypatch):
        """The kernel docs call >127C a BIOS/driver read error, not a value."""
        from shani_chronoa.senses import hwmon

        chip = self._chip(tmp_path, "thinkpad", {
            "temp1_input": "200000", "temp1_label": "CPU",
            "temp2_input": "71000",
        })
        monkeypatch.setattr(hwmon, "_chips", lambda: [chip])

        temps = {c["channel"]: c for c in hwmon.read_chips()[0]["channels"]["temp"]}
        assert temps["temp1"]["state"] == "bad-reading"
        assert "celsius" not in temps["temp1"]
        assert temps["temp2"]["state"] == "ok"

    def test_fans_and_voltages_are_scaled_and_labelled(self, tmp_path, monkeypatch):
        from shani_chronoa.senses import hwmon

        chip = self._chip(tmp_path, "thinkpad", {
            "fan1_input": "3300", "fan1_label": "CPU fan",
            "in0_input": "12752",
        })
        monkeypatch.setattr(hwmon, "_chips", lambda: [chip])

        ch = hwmon.read_chips()[0]["channels"]
        assert ch["fan"][0]["value"] == 3300 and ch["fan"][0]["unit"] == "RPM"
        assert ch["fan"][0]["label"] == "CPU fan"
        assert ch["voltage"][0]["value"] == 12.752

    def test_an_unreadable_chip_is_skipped_not_fatal(self, tmp_path, monkeypatch):
        from shani_chronoa.senses import hwmon

        monkeypatch.setattr(hwmon, "_chips", lambda: [tmp_path / "does-not-exist"])
        assert hwmon.read_chips() == []


class TestBrightnessSkill:
    def test_it_registers(self):
        from shani_chronoa.skills import discover_skills

        _tools, handlers = discover_skills()
        assert "set_brightness" in handlers

    def test_reading_reports_each_panel(self, tmp_path, monkeypatch):
        from shani_chronoa.skills import brightness

        panel = tmp_path / "intel_backlight"
        panel.mkdir()
        (panel / "brightness").write_text("9514")
        (panel / "max_brightness").write_text("96000")
        monkeypatch.setattr(brightness, "_BACKLIGHT", tmp_path)

        out = brightness.run({})
        assert "intel_backlight" in out and "10%" in out

    def test_a_denied_write_says_the_brightness_did_not_change(self, tmp_path, monkeypatch):
        """The failure this guards: a set that silently does nothing and then
        reports success, leaving the user believing their screen changed."""
        from shani_chronoa.skills import brightness

        panel = tmp_path / "intel_backlight"
        panel.mkdir()
        (panel / "brightness").write_text("9514")
        (panel / "max_brightness").write_text("96000")

        def _denied(self, data):
            raise PermissionError(13, "Permission denied")

        monkeypatch.setattr(type(panel / "brightness"), "write_text", _denied, raising=False)
        monkeypatch.setattr(brightness, "_BACKLIGHT", tmp_path)

        out = brightness.run({"level": 40})
        assert "permission denied" in out.lower()
        assert "NOT changed" in out, "a refused write must not read as success"

    @pytest.mark.parametrize("level", [-1, 101, 500, "bright"])
    def test_out_of_range_and_non_numeric_levels_are_refused(self, tmp_path, monkeypatch, level):
        from shani_chronoa.skills import brightness

        panel = tmp_path / "backlight"
        panel.mkdir()
        (panel / "brightness").write_text("100")
        (panel / "max_brightness").write_text("1000")
        monkeypatch.setattr(brightness, "_BACKLIGHT", tmp_path)

        out = brightness.run({"level": level})
        assert "between 0 and 100" in out or "not a whole number" in out

    def test_no_backlight_node_says_so_rather_than_claiming_success(self, tmp_path, monkeypatch):
        from shani_chronoa.skills import brightness

        monkeypatch.setattr(brightness, "_BACKLIGHT", tmp_path / "absent")
        out = brightness.run({"level": 50})
        assert "no backlight node" in out


class TestModelFitSense:
    """The configured model is picked by a fixed two-way tier.

    `HardwareProfile.get_model()` returns qwen3:4b or qwen3:1.7b and nothing
    else - it never asks what is installed, never checks the answer fits, and
    says nothing if the model it names is absent. On a 31GB machine it still
    selects the ~2.5GB model a 16GB machine gets.
    """

    def test_unreachable_ollama_is_unknown_not_an_empty_list(self, monkeypatch):
        """The failure this guards.

        `installed_models()` returning None means the question was not
        answered. Collapsing that into [] would report "this machine has no
        models", which is a different claim and is wrong precisely when
        Ollama is installed but not running.
        """
        from shani_chronoa.senses import modelfit

        assert modelfit.installed_models("http://127.0.0.1:1/") is None

    def test_a_running_ollama_with_no_models_is_a_real_empty_list(self, monkeypatch):
        import io
        import json as _json
        from shani_chronoa.senses import modelfit

        payload = _json.dumps({"models": []}).encode()

        class _Resp(io.BytesIO):
            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False

        monkeypatch.setattr(
            modelfit.urllib.request, "urlopen",
            lambda url, timeout=None: _Resp(payload),
        )
        assert modelfit.installed_models("http://x/") == []

    def test_real_reported_sizes_drive_the_fit_verdict(self):
        from shani_chronoa.senses import modelfit

        # 2.5 GB against a 1 GB budget must not pass; the same against 4 GB must.
        two_gb = 2 * 1024 ** 3
        assert modelfit._fits(two_gb, 1024) is False
        assert modelfit._fits(two_gb, 4096) is True
        assert modelfit._fits(None, 4096) is None, "unknown size must not be called a fit"

    def test_a_model_present_locally_is_reported_with_its_real_size(self, monkeypatch):
        import io
        import json as _json
        from shani_chronoa.senses import modelfit

        payload = _json.dumps({"models": [
            {"name": "qwen3:4b", "size": 2_600_000_000,
             "details": {"family": "qwen3", "parameter_size": "4.0B"}},
            {"name": "llama3:8b", "size": 5_200_000_000,
             "details": {"family": "llama", "parameter_size": "8.0B"}},
        ]}).encode()

        class _Resp(io.BytesIO):
            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False

        monkeypatch.setattr(
            modelfit.urllib.request, "urlopen",
            lambda url, timeout=None: _Resp(payload),
        )
        models = modelfit.installed_models("http://x/")
        assert [m["name"] for m in models] == ["qwen3:4b", "llama3:8b"]
        assert models[0]["size_bytes"] == 2_600_000_000
        assert models[0]["family"] == "qwen3"

    def test_a_configured_model_that_is_not_installed_is_flagged(
        self, monkeypatch, chronoa_config, gsettings_env
    ):
        import io
        import json as _json
        from shani_chronoa.senses import modelfit

        # The sense refuses before it inspects anything, so the consent
        # gate has to be opened for this to be about the warning at all.
        chronoa_config.set("modelfit-sense-enabled", "true")

        # Only llama3:8b is installed, but the tier will have chosen qwen3:4b.
        payload = _json.dumps({"models": [
            {"name": "llama3:8b", "size": 5_200_000_000, "details": {}},
        ]}).encode()

        class _Resp(io.BytesIO):
            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False

        monkeypatch.setattr(
            modelfit.urllib.request, "urlopen",
            lambda url, timeout=None: _Resp(payload),
        )
        out = modelfit._SENSE.run({})
        text = out.content if hasattr(out, "content") else str(out)
        assert "WARNING" in text and "not among the installed models" in text


class TestBackendAndSourceDetection:
    """A binary on PATH is not the same as an engine that can serve.

    vLLM and SGLang require a CUDA device. On a machine with only integrated
    graphics they are present-and-useless, and listing them as available is how
    a machine ends up "supporting vLLM" because a binary exists.
    """

    def test_a_gpu_only_engine_without_cuda_is_present_but_unusable(self, monkeypatch):
        from shani_chronoa.senses import modelfit

        monkeypatch.setattr(modelfit, "_has_cuda", lambda: False)
        monkeypatch.setattr(
            modelfit.shutil, "which",
            lambda b: "/usr/bin/vllm" if b == "vllm" else None,
        )
        backends = {b["backend"]: b for b in modelfit.backends()}
        assert backends["vllm"]["state"] == "present-unusable"
        assert "no CUDA" in backends["vllm"]["detail"]

    def test_the_same_engine_with_cuda_is_plainly_present(self, monkeypatch):
        from shani_chronoa.senses import modelfit

        monkeypatch.setattr(modelfit, "_has_cuda", lambda: True)
        monkeypatch.setattr(
            modelfit.shutil, "which",
            lambda b: "/usr/bin/vllm" if b == "vllm" else None,
        )
        backends = {b["backend"]: b for b in modelfit.backends()}
        assert backends["vllm"]["state"] == "present"

    def test_a_cpu_engine_needs_no_cuda(self, monkeypatch):
        from shani_chronoa.senses import modelfit

        monkeypatch.setattr(modelfit, "_has_cuda", lambda: False)
        monkeypatch.setattr(
            modelfit.shutil, "which",
            lambda b: "/usr/local/bin/ollama" if b == "ollama" else None,
        )
        backends = {b["backend"]: b for b in modelfit.backends()}
        assert backends["ollama"]["state"] == "present"

    def test_huggingface_is_a_source_not_a_backend(self):
        """The distinction the research write-ups blur.

        HF is where GGUF weights live; Ollama pulls from HF underneath. Listing
        it beside vLLM would imply Chronoa needs it to fetch models, which it
        does not.
        """
        from shani_chronoa.senses import modelfit

        # _BACKENDS is the (name, binary, needs_cuda) table; backends() is
        # what it produces. Index the table by position, not by key.
        backend_names = {entry[0] for entry in modelfit._BACKENDS}
        assert "huggingface" not in backend_names
        assert "huggingface-cli" in {s[0] for s in modelfit._SOURCES}
        # And the GPU-only ones are backends, not sources.
        assert {"vllm", "sglang"} <= backend_names

    def test_the_ollama_source_is_reported_from_its_store_directory(self, tmp_path, monkeypatch):
        from shani_chronoa.senses import modelfit

        monkeypatch.setenv("HOME", str(tmp_path))
        sources = {s["source"]: s for s in modelfit.model_sources()}
        assert sources["ollama"]["state"] == "absent"

        (tmp_path / ".ollama" / "models").mkdir(parents=True)
        sources = {s["source"]: s for s in modelfit.model_sources()}
        assert sources["ollama"]["state"] == "present"


class TestModelManager:
    def test_both_actions_register(self):
        from shani_chronoa.skills import discover_skills

        _tools, handlers = discover_skills()
        assert "recommend_model" in handlers
        assert "install_model" in handlers

    def test_install_refuses_to_pick_a_model_by_itself(self):
        """The property that makes this safe to expose to a model.

        A recommendation must not turn into gigabytes fetched on an
        assumption, so `install_model` with no name does nothing.
        """
        from shani_chronoa.skills import model_manager

        out = model_manager.install({})
        assert "does not choose one for you" in out

    def test_a_nonsense_model_name_is_refused_before_any_request(self):
        from shani_chronoa.skills import model_manager

        for bad in ("../etc/passwd", "Qwen3:8B", "a" * 300, ""):
            out = model_manager.install({"name": bad})
            assert "Nothing was installed" in out or "not a valid" in out or "No model name" in out

    def test_recommend_sizes_against_available_memory_not_total(self, monkeypatch):
        from shani_chronoa.senses import modelfit
        from shani_chronoa.skills import model_manager

        monkeypatch.setattr(
            modelfit, "_meminfo",
            lambda: {"total_mb": 32000, "available_mb": 1000},
        )
        monkeypatch.setattr(model_manager, "_meminfo", modelfit._meminfo, raising=False)
        out = model_manager.recommend({})
        assert "available" in out
        # A 1 GB budget must not offer a 9 GB model.
        assert "qwen3:14b" not in out

    def test_a_model_the_budget_cannot_hold_is_reported_as_fitting_nothing(self, monkeypatch):
        from shani_chronoa.senses import modelfit
        from shani_chronoa.skills import model_manager

        monkeypatch.setattr(
            modelfit, "_meminfo",
            lambda: {"total_mb": 2000, "available_mb": 100},
        )
        monkeypatch.setattr(model_manager, "_meminfo", modelfit._meminfo, raising=False)
        out = model_manager.recommend({})
        assert "No model in the catalogue fits" in out

    def test_recommend_says_its_sizes_are_approximate(self):
        from shani_chronoa.skills import model_manager

        out = model_manager.recommend({})
        assert "approximate" in out, "a hardcoded table must not look measured"


class TestTaskModelResolution:
    """The idea is llm-manager's; the shape is Chronoa's own.

    Arch's `llm-manager` keeps a flat task->model INI. Chronoa needs no second
    config file because it already has per-task pins in its own settings layer,
    each falling back to its own hardware tier. What was missing was the
    *lookup* - one place that answers "which model for this job" and says
    whether the answer came from the user or from the tier.
    """

    def test_the_three_real_jobs_resolve(self):
        from shani_chronoa import models

        assert models.tasks() == ["text", "transcribe", "vision"]
        for task in models.tasks():
            assert models.resolve(task), f"{task} resolved to nothing"
            assert models.describe(task), f"{task} has no stated purpose"

    def test_an_unknown_task_is_none_not_the_chat_model(self, monkeypatch):
        """A typo must say so, not quietly answer with the text model."""
        from shani_chronoa import models

        assert models.resolve("textt") is None
        out = models.explain("textt")
        assert "not a task Chronoa resolves" in out
        assert "text" in out  # it lists what is known

    def test_a_user_pin_beats_the_hardware_tier(self, monkeypatch, chronoa_config):
        from shani_chronoa import models

        chronoa_config.set("model", "qwen3:30b-a3b")
        assert models.resolve("text", config=chronoa_config) == "qwen3:30b-a3b"
        explain = models.explain("text", config=chronoa_config)
        assert "which wins" in explain, "the source of the answer must be named"

    def test_an_empty_pin_falls_through_to_the_tier(self, chronoa_config):
        from shani_chronoa import models

        chronoa_config.set("model", "")
        resolved = models.resolve("text", config=chronoa_config)
        assert resolved == "qwen3:4b" or resolved == "qwen3:1.7b"
        assert "hardware tier decides" in models.explain("text", config=chronoa_config)

    def test_whitespace_only_a_pin_is_not_a_pin(self, chronoa_config):
        """A settings field left as spaces is not a user preference."""
        from shani_chronoa import models

        chronoa_config.set("model", "   ")
        resolved = models.resolve("text", config=chronoa_config)
        assert resolved in ("qwen3:4b", "qwen3:1.7b")

    def test_transcribe_is_flagged_as_not_an_ollama_model(self, chronoa_config):
        """Whisper is not pulled with `ollama pull`, and saying so prevents a
        user chasing a model that was never going to be there."""
        from shani_chronoa import models

        assert "transcribe" in models.NON_OLLAMA_TASKS
        assert "not an Ollama model" in models.explain("transcribe", config=chronoa_config)

    def test_the_vision_pin_is_separate_from_the_text_pin(self, monkeypatch, chronoa_config):
        """They are different jobs with different requirements, and conflating
        them is the mistake the two separate settings exist to prevent."""
        from shani_chronoa import models

        chronoa_config.set("model", "qwen3:4b")
        chronoa_config.set("vision-model", "qwen3-vl:2b")
        assert models.resolve("text", config=chronoa_config) == "qwen3:4b"
        assert models.resolve("vision", config=chronoa_config) == "qwen3-vl:2b"
