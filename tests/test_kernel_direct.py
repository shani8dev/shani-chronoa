"""airplane_mode, charger_info, firmware_updates against fake sysfs trees and a stub fwupdmgr.

The hardware is faked at its kernel interface, never touched: tests must not
switch a real radio (the brightness incident is why that rule exists).
"""

import json
import stat
from pathlib import Path

import pytest

from shani_chronoa.skills import airplane_mode as air, charger_info as chg, firmware_updates as fw


def _w(p: Path, **files):
    p.mkdir(parents=True, exist_ok=True)
    for k, v in files.items():
        (p / k).write_text(f"{v}\n")


@pytest.fixture
def rf(tmp_path, monkeypatch):
    root = tmp_path / "rfkill"
    _w(root / "rfkill0", type="wlan", name="phy0", soft=0, hard=0)
    _w(root / "rfkill1", type="bluetooth", name="hci0", soft=1, hard=0)
    _w(root / "rfkill2", type="wwan", name="wwan0", soft=0, hard=1)
    monkeypatch.setattr(air, "RFKILL_DIR", root)
    return root


def test_airplane_status_is_free_and_names_a_hard_block(rf):
    out = air._run({})
    assert out.startswith("Airplane mode is off") and "Bluetooth (hci0): off" in out
    assert "blocked by a switch or the BIOS" in out


def test_switching_is_gated_and_verified(rf, tmp_path, monkeypatch):
    class Off:
        def get_bool(self, k, d=False):
            return False
    monkeypatch.setattr(air, "ChronoaConfig", Off)
    assert "radio-control-enabled" in air._run({"action": "on"})

    class On:
        def get_bool(self, k, d=False):
            return True
    monkeypatch.setattr(air, "ChronoaConfig", On)
    bindir = tmp_path / "bin"
    bindir.mkdir()
    stub = bindir / "rfkill"
    stub.write_text(f"#!/bin/sh\nfor f in {rf}/rfkill*/soft; do echo 1 > $f; done\n")
    stub.chmod(stub.stat().st_mode | stat.S_IXUSR)
    monkeypatch.setenv("PATH", f"{bindir}:/usr/bin:/bin")
    assert "now on (verified)" in air._run({"action": "on"})
    assert air._post_condition({"action": "on"})[0] is True
    assert air._post_condition({"action": "off"})[0] is False
    assert air._post_condition({"action": "status"}) is None


def test_charger_reports_a_ups_on_mains_as_plugged_in(tmp_path, monkeypatch):
    """The bug this covers: `plugged` was only set by a Mains/USB entry with
    online=1, so a UPS - which answers `online` exactly like Mains - left the
    flag false and the skill reported "Running on battery" on a machine sitting
    on mains. That is not an omission, it is the opposite of the truth."""
    ps = tmp_path / "ps"
    _w(ps / "ups0", type="UPS", online=1, capacity=100, status="OL")
    monkeypatch.setattr(chg, "POWER_SUPPLY_DIR", ps)
    monkeypatch.setattr(chg, "TYPEC_DIR", tmp_path / "no-typec")
    out = chg._run({})
    assert out.startswith("On mains through a UPS"), out
    assert "Running on battery" not in out
    assert "UPS battery 100%" in out


def test_charger_reports_a_ups_that_lost_mains_as_on_battery(tmp_path, monkeypatch):
    """The other direction, and the one that matters. `online=0` means utility
    power is out, so the machine is being carried by the UPS."""
    ps = tmp_path / "ps"
    _w(ps / "ups0", type="UPS", online=0, capacity=42, status="OB DISCHRG")
    monkeypatch.setattr(chg, "POWER_SUPPLY_DIR", ps)
    monkeypatch.setattr(chg, "TYPEC_DIR", tmp_path / "no-typec")
    out = chg._run({})
    assert "ON BATTERY" in out
    assert "the UPS is carrying the machine" in out
    assert "UPS battery 42%" in out


def test_a_peripheral_battery_still_does_not_read_as_the_machines(tmp_path, monkeypatch):
    """The scope guard, held for the new branch too - the levels are averaged
    across machine-scoped batteries only, and a mouse at 5% must not drag a UPS
    at 90% down."""
    ps = tmp_path / "ps"
    _w(ps / "hidpp_battery_0", type="Battery", capacity=5, status="Discharging",
       scope="Device")
    _w(ps / "ups0", type="UPS", online=1, capacity=90, status="OL")
    monkeypatch.setattr(chg, "POWER_SUPPLY_DIR", ps)
    monkeypatch.setattr(chg, "TYPEC_DIR", tmp_path / "no-typec")
    out = chg._run({})
    assert "UPS battery 90%" in out
    # Matched case-insensitively AND on either spelling, because the reply is
    # sentence-capitalised (`[:1].upper()`) - asserting the lowercase form only
    # matched nothing at all, which is a test that cannot fail. Confirmed by
    # running it: with the scope guard removed the reply reads
    # "Battery 5% (discharging); on mains through a UPS; ...".
    assert "5%" not in out.replace("UPS battery 90%", ""), out


def test_a_machine_with_only_a_ups_is_not_reported_as_no_power_supplies(tmp_path, monkeypatch):
    """The empty-answer sentence names a desktop on mains with no battery. A
    machine with a UPS is not that, so that sentence must not be shown."""
    ps = tmp_path / "ps"
    _w(ps / "ups0", type="UPS", online=1, capacity=100, status="OL")
    monkeypatch.setattr(chg, "POWER_SUPPLY_DIR", ps)
    monkeypatch.setattr(chg, "TYPEC_DIR", tmp_path / "no-typec")
    out = chg._run({})
    assert "reports no power supplies" not in out


def test_charger_reports_pd_wattage_only_when_offered(tmp_path, monkeypatch):
    ps, tc = tmp_path / "ps", tmp_path / "typec"
    _w(ps / "ucsi", type="USB", online=1, usb_type="C [PD] PD_PPS")
    _w(ps / "BAT0", type="Battery", capacity=55, status="Charging", power_now=25000000)
    pd = tmp_path / "pd1"
    _w(pd / "source-capabilities" / "1:fixed_supply", voltage="5000mV", maximum_current="3000mA")
    _w(pd / "source-capabilities" / "2:fixed_supply", voltage="20000mV", maximum_current="3250mA")
    (tc / "port0-partner").mkdir(parents=True)
    (tc / "port0-partner" / "usb_power_delivery").symlink_to(pd)
    monkeypatch.setattr(chg, "POWER_SUPPLY_DIR", ps)
    monkeypatch.setattr(chg, "TYPEC_DIR", tc)
    out = chg._run({})
    assert "USB-C (PD)" in out and "up to 65 W" in out and "battery 55% (charging, 25.0 W)" in out
    assert out.startswith("Plugged in through USB-C (PD)")
    for d in (pd / "source-capabilities").iterdir():
        for f in d.iterdir():
            f.unlink()
        d.rmdir()
    assert "does not report its wattage" in chg._run({}), "no offers is not 0 W"


def test_firmware_updates_parse_and_say_when_none(tmp_path, monkeypatch):
    bindir = tmp_path / "bin"
    bindir.mkdir()
    stub = bindir / "fwupdmgr"
    out = tmp_path / "out.json"
    out.write_text(json.dumps({"Devices": [{"Name": "SSD", "Version": "1.0",
                                            "Releases": [{"Version": "1.2", "Urgency": "high", "Summary": "Fixes TRIM"}]}]}))
    stub.write_text(f"#!/bin/sh\ncat {out}\n")
    stub.chmod(stub.stat().st_mode | stat.S_IXUSR)
    monkeypatch.setenv("PATH", f"{bindir}:/usr/bin:/bin")
    assert "SSD: 1.0 -> 1.2 (high urgency) - Fixes TRIM" in fw._run({})
    out.write_text(json.dumps({"Devices": []}))
    assert "No firmware updates" in fw._run({})
    out.write_text("No updatable devices")
    assert "could not list updates" in fw._run({})
