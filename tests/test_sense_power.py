"""The `power` sense, against the traps in `/sys/class/power_supply`.

Each test here corresponds to a real reading on this machine that would
otherwise have produced a plausible wrong answer:

- `charge_full` and `charge_now` read back as **empty strings** on this
  laptop, because the embedded controller exposes no coulomb counter. Dividing
  them yields an exception or a zero, so the field family must be chosen by
  which one is populated.
- `energy_now=36130000` against `energy_full=36020000` is a **ratio of
  100.3%**. The percentage must clamp.
- `energy_full_design=45000000` against `energy_full=36020000` is **20.0%
  wear**. The wrong denominator reports a worn pack as new forever.
- The directory also holds `AC` (type `Mains`) and `ucsi-source-psy-*` (type
  `USB`), so counting every entry as a battery reports this laptop as having
  three.

The filesystem is faked per-test, so these hold on any machine.
"""

import pytest

from shani_chronoa.config import ChronoaConfig
from shani_chronoa.senses import power


def config_reason():
    """The real refusal reason for this sense, as the running schema sees it."""
    return ChronoaConfig().sense_allowed_reason("power")


def _entry(tmp_path, name, kind, **fields):
    """Write a fake power_supply entry; omit a field by passing None."""
    entry = tmp_path / name
    entry.mkdir(parents=True, exist_ok=True)
    (entry / "type").write_text(kind + "\n")
    for field, value in fields.items():
        if value is None:
            continue
        (entry / field).write_text(f"{value}\n")
    return entry


@pytest.fixture
def root(tmp_path, monkeypatch):
    _root = tmp_path / "power_supply"
    _root.mkdir()
    monkeypatch.setattr(power, "_GLOB", str(_root / "*"))
    monkeypatch.setattr(power, "_AC", "Mains")
    monkeypatch.setattr(power, "_BATTERY", "Battery")
    return _root


# The measured values from this machine.
THIS_LAPTOP = dict(
    energy_now=36130000,
    energy_full=36020000,
    energy_full_design=45000000,
    cycle_count=633,
    technology="Li-poly",
    status="Not charging",
    model_name="5B10X026",
    # Empty on this machine - the trap.
    charge_now=None,
    charge_full=None,
)


class TestTheRealReading:
    def test_it_reports_charge_wear_and_cycles(self, root):
        _entry(root, "BAT0", "Battery", **THIS_LAPTOP)
        (pack,) = power.read_packs()
        assert pack["percent"] == 100.0
        assert pack["wear_percent"] == 20.0
        assert pack["cycles"] == 633
        assert pack["design_wh"] == 45.0

    def test_the_clamp_is_explained_rather_than_silent(self, root):
        """Clamping 100.3% to 100% without saying so would look like a battery
        that is exactly full."""
        _entry(root, "BAT0", "Battery", **THIS_LAPTOP)
        (pack,) = power.read_packs()
        assert "above its last-full stamp" in pack["note"]


class TestTheEmptyCoulombCounter:
    def test_empty_charge_files_do_not_become_zero(self, tmp_path, monkeypatch):
        """`charge_now` exists as a file and reads as '' on this laptop. A
        parser that reads it first and divides gets a flat battery that is not
        flat at all."""
        root = tmp_path / "power_supply"
        _entry(
            root, "BAT0", "Battery",
            charge_now="", charge_full="",   # present but empty
            energy_now=20000000, energy_full=40000000,
        )
        monkeypatch.setattr(power, "_GLOB", str(root / "*"))
        (pack,) = power.read_packs()
        assert pack["percent"] == 50.0, "an empty charge file was read as a value"

    def test_an_empty_charge_file_with_no_energy_family_is_undetermined(self, root):
        """The case that actually reaches the empty-string branch: with no energy
        reading at all, the charge files *are* consulted, and on this laptop
        they read as ''. Reading '' as 0 would give a flat battery that is not
        flat, and 0/0 is a ZeroDivisionError."""
        _entry(root, "BAT0", "Battery",
               energy_now=None, energy_full=None,
               charge_now="", charge_full="",   # present but empty
               status="Discharging")
        (pack,) = power.read_packs()
        assert pack["percent"] is None
        assert "neither energy nor charge" in pack["reason"]

    def test_a_pack_with_neither_family_reports_undetermined(self, root):
        _entry(root, "BAT0", "Battery", energy_now=None, energy_full=None,
               charge_now=None, charge_full=None, status="Discharging")
        (pack,) = power.read_packs()
        assert pack["percent"] is None
        assert "neither energy nor charge" in pack["reason"]


class TestOverfullAndUnderfull:
    def test_a_large_excess_is_reported_as_a_fault_not_clamped_away(self, root):
        """Above 102% is beyond calibration slop; clamping silently would hide a
        genuinely odd reading."""
        _entry(root, "BAT0", "Battery", energy_now=50000000, energy_full=40000000)
        (pack,) = power.read_packs()
        assert pack["percent"] == 100.0
        assert "beyond normal calibration slop" in pack["note"]

    def test_a_zero_reading_is_not_an_empty_battery(self, root):
        """A pack that reports 0/0 has a gauge that has not settled, which is
        not the same as a flat one."""
        _entry(root, "BAT0", "Battery", energy_now=0, energy_full=0)
        (pack,) = power.read_packs()
        assert pack["percent"] is None
        assert "has not settled" in pack["reason"]


class TestNonBatteryEntriesAreNotCounted:
    def test_a_mains_adapter_is_not_a_battery(self, root):
        _entry(root, "AC", "Mains", online="1")
        assert power.read_packs() == []
        assert power.read_ac() == [{"name": "AC", "online": "1"}]

    def test_a_usb_c_port_is_not_a_battery(self, root):
        """This machine exposes `ucsi-source-psy-USBC000:001`, type USB. Without
        the type check this laptop reports three batteries."""
        _entry(root, "ucsi-source-psy-USBC000:001", "USB", online="1")
        assert power.read_packs() == []

    def test_a_machine_with_no_battery_reports_none_not_zero(self, root):
        _entry(root, "AC", "Mains", online="0")
        assert power.read_packs() == []
        assert power.read_ac()[0]["online"] == "0"


class TestChargeFamilyFallback:
    def test_it_uses_the_charge_family_when_energy_is_unusable(self, root):
        _entry(root, "BAT0", "Battery", energy_now=None, energy_full=None,
               charge_now=1500000, charge_full=3000000)
        (pack,) = power.read_packs()
        assert pack["percent"] == 50.0
        assert pack["unit"] == "Ah", "the charge family is Ah, not Wh"

    def test_a_degraded_pack_reports_real_wear_not_zero(self, root):
        """`energy_full` below `energy_full_design` is the whole point of the
        wear figure: a pack holding 20Wh of a 45Wh design is 55.6% worn, and
        reading it as new - by using design capacity as the current one - is the
        failure this blocks."""
        _entry(root, "BAT0", "Battery", energy_now=20000000, energy_full=20000000,
               energy_full_design=45000000)
        (pack,) = power.read_packs()
        assert pack["wear_percent"] == 55.6
        assert pack["full_wh"] == 20.0
        assert pack["design_wh"] == 45.0
        assert "wear_percent" not in pack.get("note", ""), (
            "a real wear figure must not also be labelled undetermined"
        )

    def test_a_settled_gauge_keeps_the_design_capacity(self, root):
        """A 0/0 reading means the gauge has not settled, not that the pack is
        empty - and the design capacity is readable regardless."""
        _entry(root, "BAT0", "Battery", energy_now=0, energy_full=0,
               energy_full_design=45000000)
        (pack,) = power.read_packs()
        assert pack["percent"] is None
        assert pack["design_wh"] == 45.0

    def test_a_pack_whose_gauge_never_settles_says_so(self, root):
        """energy_now present, energy_full absent, and no charge family: there is
        no denominator of any kind, so the percentage is undetermined."""
        _entry(root, "BAT0", "Battery", energy_now=20000000, energy_full=None,
               charge_now=None, charge_full=None)
        (pack,) = power.read_packs()
        assert pack["percent"] is None
        assert "neither energy nor charge" in pack["reason"]


class TestEndToEnd:
    def test_it_produces_a_percept_with_metadata(self, root, monkeypatch):
        _entry(root, "BAT0", "Battery", **THIS_LAPTOP)
        _entry(root, "AC", "Mains", online="1")
        monkeypatch.setattr(power.ChronoaConfig, "sense_allowed",
                            lambda self, s: True)
        percept = power._run({})
        assert percept.sense == "power"
        assert percept.metadata["packs"] == 1
        assert percept.metadata["worst_wear"] == 20.0
        assert "633 charge cycles" in percept.content

    def test_a_desktop_says_so(self, root, monkeypatch):
        _entry(root, "AC", "Mains", online="1")
        monkeypatch.setattr(power.ChronoaConfig, "sense_allowed",
                            lambda self, s: True)
        percept = power._run({})
        assert "no battery" in percept.content
        assert percept.metadata["packs"] == 0

    def test_it_refuses_when_consent_is_off(self, root, monkeypatch):
        _entry(root, "BAT0", "Battery", **THIS_LAPTOP)
        monkeypatch.setattr(power.ChronoaConfig, "sense_allowed",
                            lambda self, s: False)
        result = power._run({})
        assert isinstance(result, str), "a denied sense must not emit a percept"
        # `sense_allowed_reason` is the real reason, and on this machine it is
        # the "turned off" string - which is what a user needs to see to know
        # which switch to flip.
        assert config_reason() in result
