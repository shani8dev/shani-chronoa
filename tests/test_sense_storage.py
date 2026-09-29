"""The `storage` sense, and the triple-counting trap in `/sys/block`.

The trap is real and present on this machine: one physical NVMe drive, and
`/sys/block` listing **three** entries for it. Verified directly:

    dm-0     slaves=['nvme0n1p3']
    dm-1     slaves=['dm-0']
    nvme0n1  slaves=-

`dm-0` is a device-mapper target and `dm-1` is a mapper on a mapper, so an
implementation that lists every entry — which is what `lsblk -o NAME` invites —
reports "3 disks" on a machine with one drive.

`size` is in 512-byte sectors (`1000215216` for this 512GB drive), so the
sector-to-bytes factor is asserted rather than assumed.
"""

import shutil
import subprocess

import pytest

from shani_chronoa.senses import storage


def _block(root, name, *, slaves=(), sectors=1000215216, rotational="0",
           model=None, percentage_used=None, media_errors=None, scheduler="mq-deadline"):
    entry = root / name
    entry.mkdir(parents=True, exist_ok=True)
    (entry / "size").write_text(f"{sectors}\n")
    (entry / "queue").mkdir(exist_ok=True)
    (entry / "queue/rotational").write_text(rotational + "\n")
    (entry / "queue/scheduler").write_text(f"[{scheduler}]\n")
    if slaves:
        slave_dir = entry / "slaves"
        slave_dir.mkdir(exist_ok=True)
        for slave in slaves:
            (slave_dir / slave).mkdir(exist_ok=True)
    if model or percentage_used or media_errors is not None:
        device = entry / "device"
        device.mkdir(exist_ok=True)
        if model:
            (device / "model").write_text(model + "\n")
        if percentage_used is not None:
            (device / "percentage_used").write_text(f"{percentage_used}\n")
        if media_errors is not None:
            (device / "media_errors").write_text(f"{media_errors}\n")
    return entry


@pytest.fixture
def root(tmp_path, monkeypatch):
    block = tmp_path / "block"
    block.mkdir()
    monkeypatch.setattr(storage, "_BLOCK", block)
    return block


class TestTheTripleCountTrap:
    def test_one_drive_behind_two_mappers_is_reported_as_one(self, root):
        """The headline case, reproduced from this machine."""
        _block(root, "nvme0n1", model="KBG40ZNT512G TOSHIBA MEMORY")
        _block(root, "dm-0", slaves=("nvme0n1p3",))
        _block(root, "dm-1", slaves=("dm-0",))
        (disks,) = storage.read_disks()
        assert disks["name"] == "nvme0n1", (
            "a device-mapper layer was reported as a physical disk; this "
            "machine has one drive, not three"
        )
        assert len(storage.read_disks()) == 1

    def test_the_layers_are_reported_separately(self, root):
        _block(root, "nvme0n1", model="X")
        _block(root, "dm-0", slaves=("nvme0n1p3",))
        layers = storage.read_layered()
        assert [l["name"] for l in layers] == ["dm-0"]
        assert layers[0]["on"] == ["nvme0n1p3"]

    def test_a_partition_is_not_a_disk_either(self, root):
        _block(root, "nvme0n1", model="X")
        _block(root, "nvme0n1p1", slaves=("nvme0n1",))
        _block(root, "nvme0n1p2", slaves=("nvme0n1",))
        assert [d["name"] for d in storage.read_disks()] == ["nvme0n1"]

    def test_two_real_disks_are_both_reported(self, root):
        _block(root, "sda", model="Real Disk A")
        _block(root, "nvme0n1", model="Real Disk B")
        assert len(storage.read_disks()) == 2

    def test_pseudo_devices_are_never_disks(self, root):
        for name in ("loop0", "ram0", "zram0", "dm-3", "md0", "sr0", "fd0"):
            _block(root, name)
        assert storage.read_disks() == []


class TestSectorMaths:
    def test_size_is_512_byte_sectors(self, root):
        """1000215216 sectors is this machine's 512GB drive. Multiplying by
        1024 instead would double every capacity reported."""
        _block(root, "nvme0n1", sectors=1000215216)
        (disk,) = storage.read_disks()
        assert disk["bytes"] == 1000215216 * 512
        assert 500e9 < disk["bytes"] < 520e9, "not a plausible 512GB drive"

    def test_a_zero_size_device_is_still_listed_not_dropped(self, root):
        _block(root, "sr0", sectors=0)
        _block(root, "sda", sectors=0)
        (disk,) = storage.read_disks()
        assert disk["bytes"] == 0, "a readable zero size is data, not an error"


class TestRotationalIsAClaim:
    def test_it_is_reported_as_what_the_device_claims(self, root):
        _block(root, "sda", rotational="1", model="Spinning")
        (disk,) = storage.read_disks()
        assert disk["rotational_claim"] == "1"

    def test_an_sd_card_claiming_rotational_is_not_reclassified(self, root):
        """USB enclosures around SSDs sometimes claim rotational. Nothing here
        may conclude a device is spinning from that claim."""
        _block(root, "mmcblk0", rotational="1", model="Lies About It")
        (disk,) = storage.read_disks()
        assert disk["rotational_claim"] == "1"
        assert "rotational" not in {k for k in disk if k == "is_ssd"}


class TestNvmeWear:
    def test_wear_is_read_when_the_kernel_exposes_it(self, root):
        _block(root, "nvme0n1", model="X", percentage_used=7, media_errors=0)
        (disk,) = storage.read_disks()
        assert disk["nvme"]["percentage_used"] == 7
        assert disk["nvme"]["media_errors"] == 0

    def test_absent_wear_is_omitted_not_zeroed(self, root):
        """This machine's NVMe exposes no `percentage_used` at all, so 0 would
        be a fabricated 'no wear yet'."""
        _block(root, "nvme0n1", model="X")
        (disk,) = storage.read_disks()
        assert "nvme" not in disk

    def test_a_non_numeric_wear_field_is_refused(self, root):
        _block(root, "nvme0n1", model="X", percentage_used="unknown")
        (disk,) = storage.read_disks()
        assert "nvme" not in disk


class TestEndToEnd:
    def test_it_states_that_health_was_not_determined(self, root, monkeypatch):
        """SMART needs an opt-in package and root. The sense says so rather
        than letting silence imply a healthy disk.

        `shutil.which` is stubbed rather than left to the host. This test used
        to pass only because the dev box had no `smartctl` installed - it was
        green for an environmental reason, not because it verified the
        absent-tool path, and it went red the moment smartctl appeared. A test
        that depends on a package being missing is not testing its subject.
        """
        real_which = shutil.which
        monkeypatch.setattr(storage.shutil, "which",
                            lambda name: None if name == "smartctl" else real_which(name))
        _block(root, "nvme0n1", model="KBG40ZNT512G TOSHIBA MEMORY")
        _block(root, "dm-0", slaves=("nvme0n1p3",))
        monkeypatch.setattr(storage.ChronoaConfig, "sense_allowed",
                            lambda self, s: True)
        percept = storage._run({})
        assert percept.metadata["disks"] == 1
        assert percept.metadata["layers"] == 1
        assert percept.metadata["smartctl_present"] is False
        assert "SMART health was not determined" in percept.content
        assert "not separate disks" in percept.content

    def test_an_unreadable_drive_is_reported_as_unreadable_not_healthy(
            self, root, monkeypatch):
        """The same invariant once the tool *is* installed.

        With `smartctl` present but the drive not openable, the sense says so
        with a reason instead of falling back to the tool-absent wording, and
        critically does not report the drive as healthy. This path is reachable
        on any ordinary desktop account, so unlike the 0x00 and 0x08 paths in
        `classify()` it needs no root to exercise.
        """
        _block(root, "nvme0n1", model="KBG40X026")
        _block(root, "dm-0", slaves=("nvme0n1p3",))
        monkeypatch.setattr(storage.ChronoaConfig, "sense_allowed",
                            lambda self, s: True)
        monkeypatch.setattr(
            storage, "_run_smartctl",
            lambda device: subprocess.CompletedProcess(
                args=["smartctl", "-H", "-A", device], returncode=2,
                stdout="",
                stderr="Smartctl open device: /dev/nvme0n1 failed: "
                       "Permission denied"))
        percept = storage._run({})
        assert percept.metadata["smartctl_present"] is True
        assert percept.metadata["healthy"] == 0
        assert percept.metadata["failing"] == 0
        assert percept.metadata["undetermined"] == 1
        lines = percept.content.splitlines()
        drive_lines = [l for l in lines if l.startswith("/dev/")]
        assert drive_lines, "the unreadable drive is not named at all"
        assert "not-determined" in drive_lines[0]
        assert "healthy" not in drive_lines[0]
        assert "Permission denied" in percept.content, (
            "the tool's own reason is discarded, so the user is told a drive is "
            "unreadable without being told why"
        )
        assert "not the same as healthy" in percept.content

    def test_it_refuses_when_consent_is_off(self, monkeypatch):
        monkeypatch.setattr(storage.ChronoaConfig, "sense_allowed",
                            lambda self, s: False)
        result = storage._run({})
        assert isinstance(result, str), "a denied sense must not emit a percept"
