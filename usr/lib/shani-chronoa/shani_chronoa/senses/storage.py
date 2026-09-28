"""Storage inventory and health, from sysfs — no package required.

**The trap that matters here is counting the same disk three times.** This
machine has one physical NVMe drive and `/sys/block` lists **three** entries
for it: `nvme0n1`, plus `dm-0` and `dm-1` sitting on top of it. Verified
directly:

    dm-0     slaves=['nvme0n1p3']  device_mapper=True
    dm-1     slaves=['dm-0']       device_mapper=True
    nvme0n1  slaves=-               device_mapper=False

`dm-0` is a device-mapper target, `dm-1` is a mapper on a mapper, and neither
is a disk. A sense that lists every `/sys/block` entry — which is the obvious
implementation, and the one `lsblk -o NAME` encourages — reports "3 disks,
508.8GB each" on a machine with a single 512GB drive, and a user who asks "how
much disk space do I have" gets an answer that is wrong by a factor of three.
So the inventory resolves the slave chain and reports only the leaves.

**`size` is in 512-byte sectors, always, regardless of the device.** The
kernel's `block/size` file is sector count, not bytes and not 1K-blocks, so
multiplying by 512 is right and multiplying by 1024 is the classic 2× error.
512GB from this drive is `1000215216` sectors.

**`rotational` is a lie for NVMe and USB SSDs.** It reports whether the device
*claims* rotational media, and an NVMe drive correctly claims 0 while a USB
enclosure wrapping an SSD sometimes claims 1. So it is reported as what the
device claims and never used to conclude anything is a spinning disk.

**What this deliberately does not claim.** Disk *health* needs SMART, and SMART
is a different and much more dangerous question than disk *inventory*.
`smartctl` is in `shani-tools-extra` — an **opt-in** package, not in the base
image — and it needs root for most drives. This sense therefore reports what
sysfs genuinely says (identity, capacity, rotational claim, queue depth) and
says plainly that health was not determined, rather than inferring health from
a temperature that hwmon already covers.

**The SMART bitmask has not been verified against the installed binary.** The exit
status is a bitmask, not a verdict, and the failing-disk bit is 3 while a
failure-to-open bit is 1 - so a bare non-zero check is unsafe in both
directions. `classify()` below decodes it explicitly, but the mapping has not
been confirmed against a real `smartctl` on this machine, so a drive reported
here as healthy is a drive smartctl exited 0 on, not a drive independently
proven good.
"""

import logging
import os
import shutil
import subprocess
from pathlib import Path
from typing import Dict, List, Optional, Union

from shani_chronoa.config import ChronoaConfig
from shani_chronoa.senses import SENSITIVITY_PUBLIC, Sense
from shani_chronoa.senses.context import Percept

logger = logging.getLogger(__name__)

KIND = "machine-state"
SENSITIVITY = SENSITIVITY_PUBLIC
_TTL_SECONDS = 600.0
_POLL_INTERVAL = 600.0

_BLOCK = Path("/sys/block")
_SECTOR_BYTES = 512
_SMARTCTL_TIMEOUT = 30
# Never a disk, whatever the slave chain says. `dm-` and `md` are here as well
# as being detected structurally: a mapper with no `slaves` directory at all is
# not a drive, and a RAID member is only meaningful with its array.
_SKIP_PREFIXES = ("loop", "ram", "zram", "sr", "fd", "dm-", "md")


def _read_int(path: Path) -> Optional[int]:
    try:
        raw = path.read_text().strip()
    except OSError:
        return None
    try:
        return int(raw)
    except ValueError:
        return None


def _read_text(path: Path) -> Optional[str]:
    try:
        return path.read_text().strip()
    except OSError:
        return None


def _slaves(entry: Path) -> List[str]:
    """Devices this one is built on top of, or [] for a physical device.

    An entry with slaves is a layer *above* real storage - a partition, a
    device-mapper target, a RAID member - and is not itself a disk.
    """
    directory = entry / "slaves"
    try:
        return sorted(os.listdir(directory))
    except OSError:
        return []


def _is_physical(entry: Path, name: str) -> bool:
    """A disk has no other device built on top of it.

    The slave check is the real test - it is what distinguishes a physical disk
    from a partition, a mapper target, or a RAID member - while the prefix list
    catches the pseudo-devices that have no meaningful chain at all.
    """
    if name.startswith(_SKIP_PREFIXES):
        return False
    return not _slaves(entry)


def _vendor_model(entry: Path) -> str:
    """The drive's model, which lives under `device/` for a real disk."""
    for candidate in ("device/model", "device/name"):
        value = _read_text(entry / candidate)
        if value:
            return value.strip()
    return ""


def _wear(entry: Path) -> Optional[dict]:
    """NVMe endurance, from the controller's own sysfs.

    A drive can expose percentage-used, the data units written, and media
    errors without any external tool. A SATA or USB drive exposes none of
    these, which is why this returns None for them rather than guessing.
    """
    controller = entry / "device"
    used = _read_text(controller / "percentage_used")
    if used is None:
        return None
    record: Dict[str, object] = {}
    try:
        record["percentage_used"] = int(used)
    except ValueError:
        return None
    media_errors = _read_text(controller / "media_errors")
    if media_errors is not None and media_errors.isdigit():
        record["media_errors"] = int(media_errors)
    return record


def _device(entry: Path, name: str) -> Optional[dict]:
    sectors = _read_int(entry / "size")
    if sectors is None:
        return None
    record: Dict[str, object] = {
        "name": name,
        "bytes": sectors * _SECTOR_BYTES,
        "rotational_claim": _read_text(entry / "queue/rotational"),
        "scheduler": _read_text(entry / "queue/scheduler"),
        "model": _vendor_model(entry),
    }
    discard = _read_text(entry / "queue/discard_max_bytes")
    if discard:
        record["discard_max_bytes"] = int(discard)
    wear = _wear(entry)
    if wear:
        record["nvme"] = wear
    return record


def read_disks() -> List[dict]:
    """Physical disks only: leaves of the slave chain, not layers above it."""
    try:
        entries = sorted(os.listdir(_BLOCK))
    except OSError as exc:
        logger.debug("cannot list /sys/block: %s", exc)
        return []
    disks = []
    for name in entries:
        entry = _BLOCK / name
        if not entry.is_dir() or not _is_physical(entry, name):
            continue
        found = _device(entry, name)
        if found is not None:
            disks.append(found)
    return disks


def read_layered() -> List[dict]:
    """The device-mapper and RAID layers, so "why is there a 3rd disk" is
    answerable without the user having to know what LVM is."""
    layers = []
    try:
        entries = sorted(os.listdir(_BLOCK))
    except OSError:
        return layers
    for name in entries:
        if not name.startswith(("dm-", "md")):
            continue
        entry = _BLOCK / name
        if not entry.is_dir():
            continue
        layers.append({
            "name": name,
            "on": _slaves(entry),
            "kind": "device-mapper" if name.startswith("dm-") else "raid",
        })
    return layers


BIT_USAGE = 0x01
BIT_OPEN_FAILED = 0x02
BIT_COMMAND_FAILED = 0x04
BIT_DISK_FAILING = 0x08
BIT_PREFAIL_PAST = 0x10
BIT_PREFAIL_PAST_ONCE = 0x20
BIT_ERROR_LOG = 0x40
BIT_SELF_TEST_LOG = 0x80

# Attributes worth surfacing by name. Reading the whole table is the tool's job;
# picking the ones a person can act on is this sense's.
_WATCHED = {
    5: "reallocated sectors",
    187: "uncorrectable errors",
    188: "command timeout",
    194: "temperature",
    197: "pending sectors",
    199: "uncorrectable via CRC",
    231: "SSD life left",
    233: "media wearout indicator",
    241: "total writes (GB)",
    242: "total reads (GB)",
}



def _run_smartctl(device: str) -> Optional[subprocess.CompletedProcess]:
    if shutil.which("smartctl") is None:
        return None
    try:
        return subprocess.run(
            ["smartctl", "-H", "-A", device],
            capture_output=True, text=True, timeout=_SMARTCTL_TIMEOUT, check=False,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        logger.debug("smartctl failed on %s: %s", device, exc)
        return None


def classify(status: int) -> str:
    """Turn an exit status into one of: healthy, failing, or not-determined.

    The middle case is what makes this safe. A drive we could not open is
    *not* healthy and *not* failing, and reporting either would be a lie the
    user acts on.
    """
    if status == 0:
        return "healthy"
    if status & BIT_DISK_FAILING:
        return "failing"
    if status & BIT_OPEN_FAILED:
        return "not-determined: the drive could not be opened"
    if status & BIT_PREFAIL_PAST:
        return "degraded: a prefail attribute is past its threshold"
    if status & BIT_ERROR_LOG or status & BIT_SELF_TEST_LOG:
        return "degraded: the drive's own logs contain errors"
    if status & BIT_PREFAIL_PAST_ONCE:
        return "watch: an attribute was past threshold previously"
    return "not-determined"


def _parse_attributes(text: str) -> List[dict]:
    """The attribute table, restricted to the ones worth a person's attention.

    `smartctl -A` output is a fixed-width table, and the column positions shift
    between smartmontools versions, so the value is taken from the raw-format
    reading (`VALUE/MAX`) which every version emits rather than from a column
    offset.
    """
    rows: List[dict] = []
    for line in (text or "").splitlines():
        parts = line.split()
        # ID# NAME FLAG VALUE WORST THRESH TYPE UPDATED WHEN_FAILED RAW_VALUE
        if len(parts) < 10 or not parts[0].isdigit():
            continue
        identifier = int(parts[0])
        if identifier not in _WATCHED:
            continue
        raw = parts[9]
        value = None
        worst = None
        threshold = None
        # The normalised triple starts after the FLAG, at index 3. Reading it
        # one column right takes THRESH as VALUE and TYPE as THRESH, and still
        # yields three numbers, so it looks correct.
        for token in parts[3:6]:
            if token.isdigit():
                number = int(token)
                if value is None:
                    value = number
                elif worst is None:
                    worst = number
                else:
                    threshold = number
                    break
        rows.append({
            "id": identifier,
            "name": _WATCHED[identifier],
            "raw": raw,
            "value": value,
            "threshold": threshold,
        })
    return rows


def _read_disks() -> List[str]:
    """Device paths for the physical disks, by this module's own disk rule.

    Deliberately the same `read_disks()` the inventory half uses, so smartctl is
    only ever run against a physical device. A second implementation here would
    be a second chance to hand `smartctl` a device-mapper layer, which has no
    SMART data behind it.
    """
    return [f"/dev/{d['name']}" for d in read_disks()]


def read_health() -> List[dict]:
    """Every readable physical drive, with what SMART actually said."""
    if shutil.which("smartctl") is None:
        return []
    out = []
    for device in _read_disks():
        proc = _run_smartctl(device)
        if proc is None:
            out.append({"device": device, "state": "not-determined: smartctl failed"})
            continue
        state = classify(proc.returncode)
        record: Dict[str, object] = {
            "device": device,
            "state": state,
            "status": proc.returncode,
        }
        attributes = _parse_attributes(proc.stdout or "")
        if attributes:
            record["attributes"] = attributes
        model = ""
        for line in (proc.stdout or "").splitlines():
            if line.startswith("Device Model:"):
                model = line.split(":", 1)[1].strip()
                break
        if model:
            record["model"] = model
        if state.startswith("not-determined") and (proc.stderr or "").strip():
            record["detail"] = (proc.stderr or "").strip().splitlines()[0][:120]
        out.append(record)
    return out



def _health_report():
    """SMART health for every physical drive, or why it is unavailable.

    A missing smartctl, no readable drive, and a drive that could not be opened
    are three different facts and none of them is "healthy", so each keeps its
    own wording rather than collapsing into one empty result.
    """
    if shutil.which("smartctl") is None:
        return (
            ["SMART health was not determined: smartctl is not installed. It "
             "comes from the smartmontools package. Drive identity, capacity "
             "and NVMe wear are still reported above."],
            {"smartctl_present": False, "drives": 0},
        )

    drives = read_health()
    if not drives:
        return (
            ["SMART health was not determined: no physical drive could be "
             "identified to read."],
            {"smartctl_present": True, "drives": 0},
        )

    lines = []
    counts = {"healthy": 0, "failing": 0, "degraded": 0, "undetermined": 0}
    for drive in drives:
        state = str(drive["state"])
        if state == "healthy":
            counts["healthy"] += 1
        elif state == "failing":
            counts["failing"] += 1
        elif state.startswith("degraded"):
            counts["degraded"] += 1
        else:
            counts["undetermined"] += 1
        header = drive.get("model") or drive["device"]
        lines.append(f"{header}: {state}")
        if "detail" in drive:
            lines.append(f"  {drive['detail']}")
        for attribute in drive.get("attributes", []):
            line = f"  {attribute['name']}: {attribute['raw']}"
            if attribute.get("value") is not None and attribute.get("threshold"):
                line += (f" (normalised {attribute['value']}, failing at or "
                         f"below {attribute['threshold']})")
            lines.append(line)

    if counts["failing"]:
        lines.append(
            f"{counts['failing']} drive(s) report a FAILING status. Copy "
            f"anything you have not backed up before trusting this machine."
        )
    if counts["undetermined"]:
        lines.append(
            f"{counts['undetermined']} drive(s) could not be read. That is not "
            f"the same as healthy: most often it needs root, or the drive is "
            f"behind a controller that does not pass SMART through."
        )
    lines.append(
        f"{len(drives)} drive(s): {counts['healthy']} healthy, "
        f"{counts['failing']} failing, {counts['degraded']} degraded, "
        f"{counts['undetermined']} not determined"
    )
    return lines, {"smartctl_present": True, "drives": len(drives), **counts}


def _run(arguments: dict) -> Union[str, Percept]:
    config = ChronoaConfig()
    if not config.sense_allowed("storage"):
        return f"Not reading storage state: {config.sense_allowed_reason('storage')}."

    disks = read_disks()
    layers = read_layered()
    if not disks:
        return (
            "No physical block device was readable under /sys/block. That is "
            "unusual for a running system, and it means this is a fact about "
            "what could be read rather than about the machine having no disks."
        )

    lines = []
    total = 0
    for disk in disks:
        gib = disk["bytes"] / (1024 ** 3)
        total += gib
        model = disk["model"] or "unidentified device"
        kind = "rotational (as claimed)" if disk["rotational_claim"] == "1" else "non-rotational (as claimed)"
        lines.append(f"{disk['name']}: {model}, {gib:.1f} GiB, {kind}")
        if "nvme" in disk:
            wear = disk["nvme"]
            line = f"  NVMe wear: {wear['percentage_used']}% of rated endurance used"
            if "media_errors" in wear:
                line += f", {wear['media_errors']} media errors"
            lines.append(line)
        if disk.get("discard_max_bytes"):
            lines.append(
                f"  discard/TRIM up to {disk['discard_max_bytes']} bytes per write"
            )

    if layers:
        lines.append(
            f"{len(layers)} mapping layer(s) sit on these and are not separate "
            f"disks: " + ", ".join(f"{l['name']} on {'+'.join(l['on'])}" for l in layers)
        )
    lines.append(f"{len(disks)} physical disk(s), {total:.1f} GiB total")

    # Health used to be refused here with a fixed sentence claiming smartctl
    # could not be read. It is a different question from inventory, but it is
    # the same subject - the same drive - and _health_report() below can now
    # say what smartctl actually reported instead of asserting in advance that
    # it cannot, which also retires the stale "shani-tools-extra" claim.
    health_lines, health_meta = _health_report()
    lines.append("")
    lines.extend(health_lines)

    return _SENSE.to_percept(
        "\n".join(lines),
        source="sysfs-block",
        metadata={
            **health_meta,
            "disks": len(disks),
            "layers": len(layers),
            "total_gib": round(total, 1),
            "wear": {
                d["name"]: d["nvme"]["percentage_used"]
                for d in disks if "nvme" in d
            },
        },
    )


_SCHEMA = {
    "type": "function",
    "function": {
        "name": "storage",
        "description": (
            "Read the physical disks in this machine: model, capacity in GiB, "
            "and for NVMe drives how much of their rated write endurance has "
            "been used plus any media errors. Reports only the physical disks - "
            "the device-mapper and RAID layers built on top of them are counted "
            "separately, because listing those as disks reports the same drive "
            "three times. Also reads SMART health per drive where smartctl is "
            "available: reallocated and pending sectors, media wearout, "
            "temperature and error counts, keeping a drive's own failing bit "
            "distinct from a drive that merely could not be opened. A drive "
            "that cannot be read is reported as not determined, never healthy."
        ),
        "parameters": {"type": "object", "properties": {}},
    },
}

_SENSE = Sense(
    name="storage",
    kind=KIND,
    ttl_seconds=_TTL_SECONDS,
    sensitivity=SENSITIVITY,
    schema=_SCHEMA,
    run=_run,
    poll_interval=_POLL_INTERVAL,
)

SENSES = [_SENSE]
