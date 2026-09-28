"""The `smart` sense, and the exit-status bitmask that decides its safety.

`smartctl`'s exit status is a bitmask, not a verdict, and the two bits that
matter most point in opposite directions to the naive reading:

    bit 1 (2)   the drive could not be opened
    bit 3 (8)   the disk is FAILING

So `if returncode != 0: report failing` reports every *unreadable* drive as
failing, and `if returncode == 0: report healthy` reports every unreadable
drive as healthy. The second is the dangerous direction. Both tests below exist
because either mistake is silent.
"""

import pytest

from shani_chronoa.senses import smart


# The shape of `smartctl -A` on a real NVMe, column-for-column.
REAL_OUTPUT = """smartctl 7.4 2023-08-01 r5530 [x86_64-linux] (local build)
Copyright (C) 2002-20, Bruce Allen, Christian Franke, www.smartmontools.org

=== START OF INFORMATION SECTION ===
Device Model:     KBG40ZNT512G TOSHIBA MEMORY
Firmware Version: 0109AELA
User Capacity:    512,110,190,592 bytes [512 GB]

=== START OF READ SMART DATA SECTION ===
SMART overall-health self-assessment test result: PASSED

SMART Attributes Data Structure revision number: 16
Vendor Specific SMART Attributes with Thresholds:
ID# ATTRIBUTE_NAME          FLAG     VALUE WORST THRESH TYPE      UPDATED  WHEN_FAILED RAW_VALUE
  1 Critical Warning                 100       100       1    Pre-fail  Always       -       0
  5 Reallocated_Sector_Ct         0x0033   100       100      10    Pre-fail  Always       -       0
  9 Power_On_Hours              0x0032   099       099       0    Old_age   Always       -       16138
194 Temperature_Celsius         0x0022   059       045       0    Old_age   Always       -       37
199 UDMA_CRC_Error_Count        0x0032   100       100       0    Old_age   Always       -       0
231 SSD_Life_Left               0x0032   093       093       0    Old_age   Always       -       7
241 Total_LBAs_Written          0x0032   099       099       0    Old_age   Always       -       42137
"""

FIRING_OUTPUT = """Device Model:     Samsung SSD 990 PRO 2TB

SMART overall-health self-assessment test result: FAILED!

ID# ATTRIBUTE_NAME          FLAG     VALUE WORST THRESH TYPE      UPDATED  WHEN_FAILED RAW_VALUE
  5 Reallocated_Sector_Ct         0x0033   010       010      10    Pre-fail  Always   FAILING_NOW   4096
197 Pending_Sector_Count        0x0032   088       088       0    Old_age   Always       -       256
"""

PERMISSION_OUTPUT = """smartctl: device open failed: Permission denied
"""


def _smartctl(monkeypatch, result):
    monkeypatch.setattr(smart.shutil, "which",
                        lambda n: "/usr/bin/smartctl" if n == "smartctl" else None)

    class R:
        pass

    def fake_run(argv, **kwargs):
        r = R()
        r.stdout = result["stdout"]
        r.stderr = result.get("stderr", "")
        r.returncode = result["rc"]
        return r

    monkeypatch.setattr(smart.subprocess, "run", fake_run)
    monkeypatch.setattr(smart, "_read_disks", lambda: ["/dev/nvme0n1"])


@pytest.fixture
def granted(monkeypatch):
    monkeypatch.setattr(smart.ChronoaConfig, "sense_allowed",
                        lambda self, s: True)


class TestTheBitmask:
    def test_zero_means_healthy(self):
        assert smart.classify(0) == "healthy"

    def test_the_failing_bit_means_failing(self):
        assert "failing" in smart.classify(0x08)

    def test_the_failing_bit_wins_even_when_combined(self):
        """A drive that cannot be opened AND reports failing must be reported
        as failing, because the weaker claim is the safe one to omit."""
        assert "failing" in smart.classify(0x08 | 0x02)

    def test_a_drive_that_cannot_be_opened_is_not_healthy(self):
        """The dangerous direction. This is the whole reason `classify` exists."""
        state = smart.classify(0x02)
        assert state != "healthy"
        assert "not-determined" in state

    def test_an_unreadable_drive_is_not_also_reported_as_failing(self):
        """Otherwise every unprivileged read reports a failing disk."""
        state = smart.classify(0x02)
        assert not state.startswith("failing")

    def test_an_unreadable_drive_is_never_a_healthy_string(self):
        for status in range(1, 256):
            if status & 0x08:
                continue
            assert smart.classify(status) != "healthy", (
                f"status {status} classified as healthy without the failing bit"
            )

    def test_the_bitmask_constants_are_documented_as_unverified(self):
        """`smartctl` is not installed here, so these values come from the
        smartmontools documentation and have not been checked against a real
        binary. Recorded rather than implied to be verified."""
        assert smart.BIT_DISK_FAILING == 0x08
        assert smart.BIT_OPEN_FAILED == 0x02
        source = smart.__doc__ or ""
        assert "not been verified against the installed binary" in source


class TestAttributeParsing:
    def test_it_reads_the_real_table(self, monkeypatch, granted):
        _smartctl(monkeypatch, {"stdout": REAL_OUTPUT, "rc": 0})
        (drive,) = smart.read_health()
        assert drive["model"] == "KBG40ZNT512G TOSHIBA MEMORY"
        names = {a["name"]: a["raw"] for a in drive["attributes"]}
        assert names["SSD life left"] == "7"
        assert names["temperature"] == "37"
        assert names["reallocated sectors"] == "0"

    def test_it_ignores_attributes_nobody_can_act_on(self, monkeypatch, granted):
        """Power_On_Hours is in the table and is not reported: a user cannot do
        anything with it, and a wall of every attribute is how a health
        summary becomes unreadable."""
        _smartctl(monkeypatch, {"stdout": REAL_OUTPUT, "rc": 0})
        (drive,) = smart.read_health()
        assert all(a["name"] != "power on hours" for a in drive["attributes"])
        assert all(a["name"] != "critical warning" for a in drive["attributes"])

    def test_a_failing_attribute_is_surfaced_with_its_threshold(self, monkeypatch, granted):
        _smartctl(monkeypatch, {"stdout": FIRING_OUTPUT, "rc": 0x08})
        (drive,) = smart.read_health()
        assert "failing" in drive["state"]
        reallocated = [a for a in drive["attributes"] if a["id"] == 5][0]
        assert reallocated["raw"] == "4096"
        assert reallocated["value"] == 10
        assert reallocated["threshold"] == 10

    def test_a_garbage_table_yields_no_attributes_not_a_crash(self, monkeypatch, granted):
        _smartctl(monkeypatch, {"stdout": "not a table at all\n1 2 3\n", "rc": 0})
        (drive,) = smart.read_health()
        assert "attributes" not in drive

    def test_the_parser_ignores_a_table_with_the_wrong_column_count(self):
        assert smart._parse_attributes(
            "ID# NAME FLAG VALUE WORST THRESH TYPE\n5 Reallocated 0x0033 100\n"
        ) == []


class TestDegradation:
    def test_a_missing_smartctl_says_so_and_names_the_package(self, monkeypatch, granted):
        monkeypatch.setattr(smart.shutil, "which", lambda n: None)
        percept = smart._run({})
        assert "smartctl is not installed" in percept.content
        assert "smartmontools" in percept.content
        assert percept.metadata["smartctl_present"] is False
        assert "not determined" in percept.content, (
            "a missing tool must not read as a healthy disk"
        )
        # The word "healthy" must not appear at all: an absent tool says
        # nothing about the drives, and a summary that merely leads with a
        # caveat still reads as reassurance to anyone skimming.
        assert "healthy" not in percept.content, (
            f"a missing smartctl produced reassurance: {percept.content!r}"
        )

    def test_a_permission_failure_is_quoted_not_summarised_away(self, monkeypatch, granted):
        _smartctl(monkeypatch, {"stdout": "", "stderr": PERMISSION_OUTPUT, "rc": 2})
        content = smart._run({}).content
        assert "Permission denied" in content
        assert "not the same as healthy" in content

    def test_no_identifiable_drive_is_not_no_bad_drives(self, monkeypatch, granted):
        _smartctl(monkeypatch, {"stdout": REAL_OUTPUT, "rc": 0})
        monkeypatch.setattr(smart, "_read_disks", lambda: [])
        percept = smart._run({})
        assert "not determined" in percept.content
        assert percept.metadata["drives"] == 0

    def test_it_reuses_the_storage_senses_disk_rule(self, monkeypatch, granted):
        """A second /sys/block implementation is a second chance to run
        smartctl against a device-mapper layer that has no SMART data."""
        import shani_chronoa.senses.storage as storage_module
        monkeypatch.setattr(storage_module, "_BLOCK", __import__("pathlib").Path("/nonexistent"))
        assert smart._read_disks() == []


class TestEndToEnd:
    def test_a_healthy_drive_says_so_with_its_wear(self, monkeypatch, granted):
        _smartctl(monkeypatch, {"stdout": REAL_OUTPUT, "rc": 0})
        percept = smart._run({})
        assert percept.metadata["healthy"] == 1
        assert "SSD life left: 7" in percept.content
        assert "1 drive(s): 1 healthy" in percept.content

    def test_a_failing_drive_says_to_back_up_now(self, monkeypatch, granted):
        _smartctl(monkeypatch, {"stdout": FIRING_OUTPUT, "rc": 0x08})
        percept = smart._run({})
        assert percept.metadata["failing"] == 1
        assert "backed up" in percept.content

    def test_it_refuses_when_consent_is_off(self, monkeypatch):
        monkeypatch.setattr(smart.ChronoaConfig, "sense_allowed",
                            lambda self, s: False)
        assert isinstance(smart._run({}), str)
