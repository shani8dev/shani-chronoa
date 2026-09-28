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
