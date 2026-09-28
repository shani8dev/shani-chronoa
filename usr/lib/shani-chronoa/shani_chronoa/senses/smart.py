"""SMART disk health — the sense that has to be right about a failing disk.

This is the one sense in the package where a wrong answer is expensive: a
drive that reports "healthy" while failing is worse than no answer, because the
user stops backing up. Everything below is therefore arranged so that **any**
difficulty degrades to "not determined" and never to "fine".

**`smartctl`'s exit status is a bitmask, not a verdict.** This is the trap
that decides whether a sense like this is safe. The status is a byte where
several bits are set independently, and the two that matter most are
*opposite* in meaning to the naive reading:

- bit 0 (1) — command line parsing failed
- bit 1 (2) — device could not be opened, or identification failed
- bit 2 (4) — some SMART command failed, or a checksum error
- **bit 3 (8) — the disk is FAILING**
- bit 4 (16) — a prefail attribute is at or past its threshold
- bit 5 (32) — an attribute was past threshold in the past
- bit 6 (64) — the device error log contains errors
- bit 7 (128) — the self-test log contains errors

So `returncode == 0` means *no* problems, `returncode & 8` is a failing disk,
and `returncode & 2` means *we could not read it at all* — which is emphatically
not the same as "healthy". A parser written as `if result.returncode != 0:
report failing` reports every unreadable drive as a failing drive, which is
both wrong and maximally alarming. A parser written as `if result.returncode == 0:
report healthy` reports every unreadable drive as healthy, which is the
dangerous direction. Hence: **an unreadable drive is never healthy.**

❓ **The exact bit values are recorded from the smartmontools documentation and
have not been verified against the installed binary** — `smartctl` is not
present on this machine. `test_the_bitmask_constants` documents that
unverified status rather than pretending otherwise, and the sense treats any
non-zero exit as "not determined" unless the failing bit is set, which is the
safe direction for both cases.

**Whether root is needed varies by drive and by bus, and is not knowable in
advance.** NVMe over a native controller often answers unprivileged; SATA
usually needs root. The sense reports what it actually got, and says nothing
about health when it got nothing.
"""

import logging
import shutil
import subprocess
from typing import Dict, List, Optional, Union

from shani_chronoa.config import ChronoaConfig
from shani_chronoa.senses import SENSITIVITY_PUBLIC, Sense
from shani_chronoa.senses.context import Percept

logger = logging.getLogger(__name__)

KIND = "machine-state"
SENSITIVITY = SENSITIVITY_PUBLIC
_TTL_SECONDS = 900.0
_POLL_INTERVAL = 900.0

_TIMEOUT = 30

# smartctl exit-status bits. See the module docstring: the failing-disk bit is
# 3, and a failure-to-open bit is 1, so a bare non-zero check is unsafe in
# both directions.
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


def _read_disks() -> List[str]:
    """Physical block devices, via the sibling `storage` sense's own rule.

    Reusing it rather than re-listing `/sys/block` is the point: a second
    implementation is a second chance to count a device-mapper layer as a disk
    and then run `smartctl` against a mapper that has no SMART data.
    """
    from shani_chronoa.senses import storage
    return [f"/dev/{d['name']}" for d in storage.read_disks()]


def _run_smartctl(device: str) -> Optional[subprocess.CompletedProcess]:
    if shutil.which("smartctl") is None:
        return None
    try:
        return subprocess.run(
            ["smartctl", "-H", "-A", device],
            capture_output=True, text=True, timeout=_TIMEOUT, check=False,
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


def _run(arguments: dict) -> Union[str, Percept]:
    config = ChronoaConfig()
    if not config.sense_allowed("smart"):
        return f"Not reading disk health: {config.sense_allowed_reason('smart')}."

    if shutil.which("smartctl") is None:
        return _SENSE.to_percept(
            "SMART health was not determined: smartctl is not installed. It "
            "comes from the smartmontools package. Temperature and drive "
            "identity are still available - the storage sense covers those.",
            source="smartctl-missing",
            metadata={"smartctl_present": False, "drives": 0},
        )

    drives = read_health()
    if not drives:
        return _SENSE.to_percept(
            "SMART health was not determined: no physical drive could be "
            "identified to read. Inventory is the storage sense's job and it "
            "reports separately.",
            source="smartctl-no-drives",
            metadata={"smartctl_present": True, "drives": 0},
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

    return _SENSE.to_percept(
        "\n".join(lines),
        source="smartctl",
        metadata={"smartctl_present": True, "drives": len(drives), **counts},
    )


_SCHEMA = {
    "type": "function",
    "function": {
        "name": "smart",
        "description": (
            "Read SMART health for every physical drive: whether the disk "
            "reports itself healthy, failing or degraded, plus the attributes "
            "worth a person's attention - reallocated sectors, pending "
            "sectors, uncorrectable errors, media wearout, SSD life left, and "
            "total data written. A drive that could not be read is reported as "
            "not determined, never as healthy, because smartctl's exit status "
            "is a bitmask where a failure to open the drive is a different bit "
            "from a failing disk. Requires the smartmontools package, and root "
            "on most SATA drives."
        ),
        "parameters": {"type": "object", "properties": {}},
    },
}

_SENSE = Sense(
    name="smart",
    kind=KIND,
    ttl_seconds=_TTL_SECONDS,
    sensitivity=SENSITIVITY,
    schema=_SCHEMA,
    run=_run,
    poll_interval=_POLL_INTERVAL,
)

SENSES = [_SENSE]
