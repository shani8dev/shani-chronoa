"""The `cooling` sense, and the difference between a stopped fan and no fan.

Measured on this machine: two hwmon fan channels, both 3300 RPM, on the
`thinkpad` chip. `liquidctl` is not installed.

The sense exists mostly for the case this machine does *not* currently
exercise — a liquid cooler, which does not appear in hwmon at all — and for
the distinction that a naive `if rpm > 0` filter destroys:

- a fan at 3300 RPM is spinning;
- a fan at **0 RPM is stopped** — its power is cut, or it has stalled. That is
  something a user needs to be told;
- **no fan channel exists** is a different claim again.

`hwmon` already reports a declared-but-unwired channel separately, and this
sense must not collapse that distinction back by dropping zero readings.
"""

import pytest

from shani_chronoa.senses import hwmon

LIQUIDCTL_JSON = """{
  "Corsair H100i": {
    "Firmware version": "1706000810",
    "Liquid temperature": {"celsius": 34.4, "unit": "celsius"},
    "FAN speed": {"rpm": 1044, "unit": "rpm"},
    "Pump speed": {"rpm": 2100, "unit": "rpm"},
    "Speed": "Default",
    "Mode": "Default"
  }
}
"""


_CHIP_INDEX = [0]


def _chip(root, driver, *, fans):
    """A `/sys/class/hwmon/hwmonN` directory whose `name` is the driver.

    The directory name and the chip name are different things, and confusing
    them is a fixture bug that hides a real one: the glob is `hwmon*`, so a
    directory named after the driver is simply never found.
    """
    index = _CHIP_INDEX[0]
    _CHIP_INDEX[0] += 1
    chip = root / f"hwmon{index}"
    chip.mkdir(parents=True, exist_ok=True)
    (chip / "name").write_text(driver + "\n")
    for channel, rpm in fans.items():
        (chip / f"fan{channel}_input").write_text(f"{rpm}\n")
    return chip


def _temp_only_chip(root, driver="acpitz", celsius=40):
    """A chip with a readable temperature and no fan channel at all.

    Needed to reach the "no fans and no coolers" branch: a chip with no fan is
    not enough on its own, because a chip that exposes nothing readable is
    turned away earlier as "no hwmon chip exposed a readable channel".
    """
    index = _CHIP_INDEX[0]
    _CHIP_INDEX[0] += 1
    chip = root / f"hwmon{index}"
    chip.mkdir(parents=True, exist_ok=True)
    (chip / "name").write_text(driver + "\n")
    (chip / "temp1_input").write_text(f"{celsius * 1000}\n")
    (chip / "temp1_label").write_text(f"{driver}\n")
    return chip


def _with_liquidctl(monkeypatch, stdout):
    monkeypatch.setattr(hwmon.shutil, "which",
                        lambda n: "/usr/bin/liquidctl" if n == "liquidctl" else None)

    class R:
        pass

    def fake_run(argv, **kwargs):
        r = R()
        r.stdout = stdout
        r.stderr = ""
        r.returncode = 0
        return r

    monkeypatch.setattr(hwmon.subprocess, "run", fake_run)


@pytest.fixture
def hwmon_root(tmp_path, monkeypatch):
    root = tmp_path / "hwmon"
    root.mkdir()
    monkeypatch.setattr(hwmon, "_HWMON_ROOT", root)
    monkeypatch.setattr(hwmon.shutil, "which", lambda n: None)
    return root


@pytest.fixture
def granted(monkeypatch):
    monkeypatch.setattr(hwmon.ChronoaConfig, "sense_allowed",
                        lambda self, s: True)


class TestStoppedIsNotAbsent:
    def test_a_zero_rpm_fan_is_reported_as_stopped(self, hwmon_root, monkeypatch, granted):
        """The whole point. A `if rpm > 0` filter would drop this and the user
        would never learn their fan stopped."""
        _chip(hwmon_root, "hwmon0", fans={1: 0})
        percept = hwmon._run({})
        assert percept.metadata["stopped_fans"] == 1
        assert percept.metadata["fans"] == 1, "a stopped fan was not counted"
        assert "stopped, 0 RPM" in percept.content

    def test_a_stopped_fan_explains_itself(self, hwmon_root, monkeypatch, granted):
        _chip(hwmon_root, "hwmon0", fans={1: 0})
        content = hwmon._run({}).content
        assert "not the same as no fan being fitted" in content
        assert "declared-but-unwired" in content, (
            "the sense must defer to hwmon's own unwired-channel rule rather "
            "than restate it wrongly"
        )

    def test_a_spinning_fan_is_not_labelled_stopped(self, hwmon_root, monkeypatch, granted):
        """The real reading on this machine."""
        _chip(hwmon_root, "thinkpad", fans={1: 3300, 2: 3300})
        percept = hwmon._run({})
        assert percept.metadata["stopped_fans"] == 0
        assert percept.metadata["fans"] == 2
        assert "3300 RPM" in percept.content
        assert "stopped" not in percept.content

    def test_stopped_and_spinning_are_distinguished(self, hwmon_root, monkeypatch, granted):
        _chip(hwmon_root, "hwmon0", fans={1: 0, 2: 2100})
        content = hwmon._run({}).content
        assert "stopped, 0 RPM" in content
        assert "2100 RPM" in content
        assert hwmon._run({}).metadata["stopped_fans"] == 1

    def test_no_fan_channel_is_neither_stopped_nor_spinning(self, hwmon_root, monkeypatch, granted):
        content = hwmon._run({}).content
        assert "0 RPM" not in content, "a machine with no fan channel is not a stopped fan"
        assert hwmon._run({}).metadata["fans"] == 0


class TestMultipleChips:
    def test_fans_are_gathered_from_every_chip(self, hwmon_root, monkeypatch, granted):
        _chip(hwmon_root, "hwmon0", fans={1: 1000})
        _chip(hwmon_root, "hwmon1", fans={1: 2000})
        assert hwmon._run({}).metadata["fans"] == 2

    def test_a_chip_with_no_fans_contributes_nothing(self, hwmon_root, monkeypatch, granted):
        _chip(hwmon_root, "hwmon0", fans={})
        _chip(hwmon_root, "hwmon1", fans={1: 1200})
        assert hwmon._run({}).metadata["fans"] == 1

    def test_a_named_fan_label_is_preferred(self, hwmon_root, monkeypatch, granted):
        chip = _chip(hwmon_root, "hwmon0", fans={1: 1500})
        (chip / "fan1_label").write_text("CPU fan\n")
        assert "CPU fan" in hwmon._run({}).content

    def test_non_fan_attributes_are_ignored(self, hwmon_root, monkeypatch, granted):
        chip = _chip(hwmon_root, "hwmon0", fans={1: 1500})
        (chip / "temp1_input").write_text("42000\n")
        (chip / "in0_input").write_text("1200\n")
        assert hwmon._run({}).metadata["fans"] == 1


class TestLiquidCooler:
    def test_a_cooler_is_read_from_its_own_json(self, hwmon_root, monkeypatch, granted):
        _chip(hwmon_root, "hwmon0", fans={1: 1000})
        _with_liquidctl(monkeypatch, LIQUIDCTL_JSON)
        percept = hwmon._run({})
        assert percept.metadata["coolers"] == 1
        assert "coolant 34.4C" in percept.content
        assert "pump 2100 RPM" in percept.content

    def test_a_cooler_absent_from_hwmon_is_still_reported(self, hwmon_root, monkeypatch, granted):
        """The case that motivates the sense: an AIO's radiator and pump
        usually do not appear in hwmon at all."""
        _with_liquidctl(monkeypatch, LIQUIDCTL_JSON)
        content = hwmon._run({}).content
        assert "no fan channel in hwmon" in content
        assert hwmon._run({}).metadata["coolers"] == 1

    def test_empty_liquidctl_output_is_no_cooler_not_a_crash(self, hwmon_root, monkeypatch, granted):
        _chip(hwmon_root, "hwmon0", fans={1: 1000})
        _with_liquidctl(monkeypatch, "")
        assert hwmon._run({}).metadata["coolers"] == 0

    def test_malformed_json_is_no_cooler(self, hwmon_root, monkeypatch, granted):
        _chip(hwmon_root, "hwmon0", fans={1: 1000})
        _with_liquidctl(monkeypatch, "{not json")
        assert hwmon._run({}).metadata["coolers"] == 0

    def test_a_device_without_a_temperature_is_not_a_cooler(self, hwmon_root, monkeypatch, granted):
        _chip(hwmon_root, "hwmon0", fans={1: 1000})
        _with_liquidctl(monkeypatch, '{"Some Device": {"Speed": "Default"}}')
        assert hwmon._run({}).metadata["coolers"] == 0


class TestDegradation:
    def test_no_hwmon_and_no_liquidctl_is_undetermined_both_ways(self, hwmon_root, monkeypatch, granted):
        """Two separate absences: the kernel exposing nothing, and the tool not
        being installed. Collapsing them claims a machine has no fan."""
        percept = hwmon._run({})
        content = percept.content
        assert percept.metadata["fans"] == 0
        assert percept.metadata["liquidctl_present"] is False
        # The two absences are different in kind and must be named apart: the
        # kernel reporting no fan, versus the tool not being installed.
        assert "fans are undetermined" in content
        assert "coolant is undetermined" in content
        assert "Neither means this machine has no cooling" in content

    def test_hwmom_without_liquidctl_still_reports_the_fans(self, hwmon_root, monkeypatch, granted):
        """The default on a real desktop, since liquidctl ships in
        shani-tools-extra."""
        _chip(hwmon_root, "thinkpad", fans={1: 3300})
        percept = hwmon._run({})
        assert percept.metadata["fans"] == 1
        assert percept.metadata["liquidctl_present"] is False
        assert "1 fan channel(s)" in percept.content

    def test_an_unreadable_hwmon_is_not_claimed_to_be_passively_cooled(self, hwmon_root, monkeypatch, granted):
        _with_liquidctl(monkeypatch, LIQUIDCTL_JSON)
        monkeypatch.setattr(hwmon, "_HWMON_ROOT", hwmon_root / "gone")
        content = hwmon._run({}).content
        assert "not determined" in content or "no fan channel in hwmon" in content
        assert hwmon._run({}).metadata["coolers"] == 1, (
            "an unreadable hwmon must not suppress a cooler the tool did report"
        )

    def test_it_refuses_when_consent_is_off(self, monkeypatch):
        monkeypatch.setattr(hwmon.ChronoaConfig, "sense_allowed",
                            lambda self, s: False)
        assert isinstance(hwmon._run({}), str)


def _chip_with_pwm(root, driver="thinkpad", pwm=255, enable=2, fans=None):
    """A chip exposing fan PWM control, the way a laptop's EC bridge does."""
    chip = _chip(root, driver, fans=fans or {1: 3300})
    (chip / "pwm1").write_text(f"{pwm}\n")
    (chip / "pwm1_enable").write_text(f"{enable}\n")
    return chip


class TestPwmIsReadNeverWritten:
    """Fan control is readable even though Chronoa must never write it.

    `pwm1` answers a question nobody can answer any other way - is this
    machine's firmware driving the fans, or has something taken manual control?
    - and reading it costs nothing. Writing it is a different proposition
    entirely: `fancontrol` is a standing policy rather than a bounded action,
    Arch's own man page for it warns about burning the CPU, and its config
    refers to hwmon indices that are assigned at boot and are not stable across
    reboots. So the read half is in the sense and the write half is not
    anywhere in the codebase.
    """

    def test_the_duty_cycle_is_reported_as_a_percentage(self, hwmon_root, granted):
        _chip_with_pwm(hwmon_root, pwm=128, enable=1)
        content = hwmon._run({}).content
        assert "pwm1" in content
        assert "50.2%" in content, (
            "128 of 255 is 50.196% and was not reported as a duty cycle")

    def test_the_enable_value_is_reported_raw(self, hwmon_root, granted):
        """The 0/1/2 reading is a de-facto convention, not a standard: the
        kernel's own driver docs have dell-smm-hwmon calling 1 'BIOS control
        disabled' and g762 calling 2 'closed-loop mode'. So the number is
        reported and the interpretation is marked as the convention it is."""
        _chip_with_pwm(hwmon_root, enable=2)
        content = hwmon._run({}).content
        assert "enable 2" in content
        assert "driver-specific" in content

    def test_a_full_duty_cycle_is_not_reported_as_a_stalled_fan(self, hwmon_root, granted):
        _chip_with_pwm(hwmon_root, pwm=255, enable=2, fans={1: 3300})
        content = hwmon._run({}).content
        assert "100%" in content
        assert "3300 RPM" in content
        assert "stopped" not in content

    def test_a_zero_duty_cycle_with_the_fan_still_spinning_is_flagged(
        self, hwmon_root, granted
    ):
        """The two disagree, and both are reported. A commanded stop that the
        fan ignores is a real fault; reporting either half alone would hide it.
        """
        _chip_with_pwm(hwmon_root, pwm=0, enable=1, fans={1: 3300})
        content = hwmon._run({}).content
        assert "0%" in content
        assert "3300 RPM" in content
        assert "still spinning" in content or "disagree" in content

    def test_a_chip_with_no_pwm_nodes_says_nothing_about_control(self, hwmon_root, granted):
        _chip(hwmon_root, "bat0", fans={})
        content = hwmon._run({}).content
        assert "pwm" not in content.lower(), (
            "a chip with no PWM node was given an opinion about fan control"
        )

    def test_the_sense_never_writes_to_a_pwm_node(self, hwmon_root, granted):
        import inspect
        source = inspect.getsource(hwmon)
        for forbidden in ('"w"', "'w'", r"open\(.*pwm", "write_text"):
            assert not __import__("re").search(forbidden, source), (
                f"the hwmon sense opened a pwm node for writing: {forbidden}"
            )

    def test_pwm_presence_is_in_the_metadata(self, hwmon_root, granted):
        _chip_with_pwm(hwmon_root)
        metadata = hwmon._run({}).metadata
        assert metadata["pwm_channels"] == 1


class TestLiquidctlAnsweredButNamedNoCooler:
    """The distinction the merge flattened, and the case it lost.

    Every other liquidctl test here supplies a fan, so the "no fans and no
    coolers" branch is never reached. That is why losing it went unnoticed: the
    branch was already unreachable from the rest of this file. A machine with
    `liquidctl` installed and no AIO - a passively cooled laptop, or cooling
    driven by firmware the kernel does not expose - lands exactly there, and
    was being told its coolant was "undetermined - the tool is absent" about a
    tool that was present and had just answered.
    """

    def test_liquidctl_present_and_no_cooler_is_not_reported_as_absent(
            self, hwmon_root, monkeypatch, granted):
        _temp_only_chip(hwmon_root)                   # a readable channel, no fan
        _with_liquidctl(monkeypatch, "")              # installed, names nothing
        percept = hwmon._run({})
        assert percept.metadata["liquidctl_present"] is True
        assert percept.metadata["coolers"] == 0
        assert "the tool is absent" not in percept.content
        assert "passively cooled" in percept.content
        assert "firmware" in percept.content
        assert percept.source == "hwmon+liquidctl", (
            "a percept that consulted liquidctl must say so in its source")

    def test_liquidctl_really_absent_still_says_so(self, hwmon_root, granted):
        """The other half: the two cases must stay distinguishable, or
        restoring one is indistinguishable from collapsing both."""
        _temp_only_chip(hwmon_root)
        percept = hwmon._run({})
        assert percept.metadata["liquidctl_present"] is False
        assert "the tool is absent" in percept.content
        assert "passively cooled" not in percept.content
