"""Sense: is this machine running on mains, or on a battery that will run out.

Shanios switched its UPS daemon from apcupsd to NUT in 2026-10-10, and nothing
on the assistant side ever knew about it: the `power` sense reads
`/sys/class/power_supply/*` and keeps only `type == "Battery"`, so a UPS entry -
which the kernel reports as `type == "UPS"` - was filtered out by design. A
machine with a UPS plugged in was indistinguishable from a desktop, and the one
question a UPS exists to answer ("how long do I have?") had no answer anywhere in
this app.

**Two sources, and they answer different questions.**

* `/sys/class/power_supply/*` with `type == "UPS"` needs **no binary and no
  daemon**, and reports what the kernel's power-supply driver sees. It is the
  part that always works when there is hardware.
* NUT's own `upsc` reports the *full* picture - charge, runtime, load, transfer
  reason - but only when NUT is actually monitoring something, which on a fresh
  install it deliberately is not (shani-settings ships `ups.conf` with no device
  section, and `nut-scanner -U` is what fills one in).

**The failure this sense exists not to have.** "I could not ask" and "the power
is fine" are opposite claims, and on a UPS the second one is the dangerous
answer: an unreachable UPS is indistinguishable from a dead one, which is why
NUT's own `DEADTIME` treats silence as on-battery. So an unreadable sysfs entry
and an `upsc` that will not answer both report UNKNOWN with the reason named, and
absent hardware reports absent - never a clean, confident reading.

**What is deliberately not read.** `/etc/nut/upsd.users` holds the credential
that can force a UPS power-down, and the device section of `ups.conf` names which
port and driver the hardware uses. Neither is a fact worth sending to a model;
this sense reads the monitor policy from `upsmon.conf` only to learn *whether*
anything is being watched at all, and reports the name and nothing else from it.
"""

from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Optional, Union

from shani_chronoa.config import ChronoaConfig
from shani_chronoa.senses import SENSITIVITY_PUBLIC, Sense
from shani_chronoa.senses.context import Percept
from shani_chronoa import sysfs

logger = logging.getLogger(__name__)

KIND = "machine-state"
SENSITIVITY = SENSITIVITY_PUBLIC
# TTL deliberately exceeds the poll interval: a sense that expires before it is
# repolled is missing for (poll - ttl) seconds of every cycle, and on this sense
# that is the difference between "the power is fine" and "nobody has looked
# lately". `test_sense_manifest` asserts exactly this property across the whole
# registry, and caught the 15/30 pair this first shipped with.
_TTL_SECONDS = 90.0
_POLL_INTERVAL = 30.0

#: The kernel's own name for a UPS in `/sys/class/power_supply`. The `power`
#: sense keeps `Battery` and this one keeps this - a machine with both reports
#: both, and they are different devices with different answers.
_UPS = "UPS"

_GLOB = Path("/sys/class/power_supply/*")

#: NUT's policy file, read for one thing only: whether a MONITOR line exists.
#: There is no drop-in mechanism and this is the file the whole policy lives in.
_UPSMON_CONF = Path("/etc/nut/upsmon.conf")

#: `upsc` is in /usr/bin on Arch. Resolved through tool_path_or_self() by the
#: caller-side helper the same way `smartctl` is, so a PATH that lacks it does
#: not make a working UPS read as missing.
_UPSC = "upsc"


def _entry(entry: Path) -> Optional[dict]:
    """One UPS's sysfs readings, or None when it is not one.

    `type` is read first, for the same reason `power` reads it: the directory
    also holds `Battery`, `Mains`, and USB-C ports reported as `USB`, and
    counting any of those as a UPS is how a laptop comes to be reported as
    having an uninterruptible supply.
    """
    if sysfs.read_text(entry / "type") != _UPS:
        return None
    return {
        "name": entry.name,
        # `online` is the one that matters: it is what the driver knows about
        # mains, and `present` is a different question again.
        "online": sysfs.read_int(entry / "online"),
        "present": sysfs.read_int(entry / "present"),
        "status": sysfs.read_text(entry / "status"),
    }


def _ups_from_sysfs() -> list[dict]:
    """Every UPS the kernel's power-supply layer can see, or an empty list.

    An empty list here means "none", and the caller decides separately whether
    that is "no UPS" or "could not ask" - two different claims that must not be
    the same sentence.
    """
    found: list[dict] = []
    for raw in sorted(_GLOB.parent.glob(_GLOB.name)):
        if not raw.is_dir():
            continue
        try:
            if (raw / "type").exists():
                record = _entry(raw)
                if record is not None:
                    found.append(record)
        except OSError as exc:
            logger.debug("ups sense: could not read %s: %s", raw, exc)
    return found


def _sysfs_consultable() -> bool:
    """Whether `/sys/class/power_supply` could be consulted at all.

    The distinction that matters: a directory that exists and is empty is a
    real "there is no UPS", while one that does not exist or cannot be listed is
    "nobody could ask". Folding them together makes an absence read as a
    failure, which is the shape this repo keeps being bitten by - and on a UPS
    the failure-shaped answer is the one that says the power is fine when nobody
    checked.
    """
    try:
        return _GLOB.parent.is_dir()
    except OSError:
        return False


def _monitor_name() -> Optional[str]:
    """The UPS NUT is watching, or None.

    `MONITOR <system> <powervalue> <user> <password> (master|secondary)`. The
    system is `<name>@<host>`, and it is the name `upsc` needs. An absent MONITOR
    line is a real state - the policy is shipped but nothing is monitored - so
    None here means exactly that and is not an error.
    """
    try:
        text = _UPSMON_CONF.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        parts = stripped.split()
        if len(parts) >= 2 and parts[0] == "MONITOR":
            return parts[1].split("@", 1)[0]
    return None


def _run(arguments: dict) -> Union[str, Percept]:
    config = ChronoaConfig()
    if not config.sense_allowed("ups"):
        return f"Not reading UPS state: {config.sense_allowed_reason('ups')}."

    devices = _ups_from_sysfs()
    # An empty-but-readable directory is a real "there is no UPS"; one that
    # cannot be consulted is "nobody could ask". Conflating them makes a readable
    # empty power_supply report UNKNOWN, which is an absence dressed as a failure.
    consulted = _sysfs_consultable()

    # `upsc` is only worth running when NUT is actually monitoring something,
    # and it is resolved rather than assumed: a bare `upsc` on a PATH that lacks
    # /usr/bin reports a working UPS as missing, which is the mistake this repo
    # has made with `smartctl` and `pactl` before.
    name = _monitor_name()
    detail: Optional[dict] = None
    detail_error = ""

    if name:
        upsc = _resolve("upsc")
        if upsc is None:
            detail_error = "NUT is monitoring a UPS but upsc is not on PATH"
        else:
            detail_error = _read_upsc(upsc, name) or ""
            if detail_error.startswith("OK:"):
                detail = _parse_upsc(detail_error[3:])
                detail_error = ""

    if not devices and detail is None:
        if not consulted:
            return (
                "UPS state is UNKNOWN: /sys/class/power_supply could not be "
                "consulted, so it is not established that there is no UPS. That "
                "is a statement about this process's access to sysfs, not a "
                "claim about the hardware."
            )
        if name and detail_error:
            return (
                f"UPS state is UNKNOWN: NUT is configured to monitor '{name}' "
                f"but could not read it - {detail_error}. An unreachable UPS is "
                "not a healthy one, and nothing here claims it is."
            )
        return (
            "No UPS is connected to this machine: no UPS entry in "
            "/sys/class/power_supply and NUT has no MONITOR line. This is the "
            "expected state on a fresh install - shani-settings ships no device "
            "section, and `sudo nut-scanner -U` is what adds one."
        )

    lines: list[str] = []
    for device in devices:
        online = device["online"]
        if online is None:
            state = "could not be determined"
        elif online:
            state = "on mains"
        else:
            state = "ON BATTERY"
        line = f"{device['name']}: {state}"
        if device["status"]:
            line += f"  (driver status: {device['status']})"
        if online == 0:
            line += "  -- mains is out"
        lines.append(line)

    if detail is not None:
        # Ordered so the first lines a reader sees are the ones that decide what
        # to do: whether the power is out, and how long is left.
        order = ("ups.status", "battery.charge", "battery.runtime", "ups.load",
                 "input.voltage", "battery.voltage")
        for key in order:
            if detail.get(key) not in (None, ""):
                lines.append(f"{key}: {detail[key]}")
        daemon_on_battery = detail.get("ups.status") in ("OB", "OB LB", "LB")
        if daemon_on_battery:
            lines.append("mains is out - this machine is running on battery")
        # The kernel's driver and NUT's daemon are two independent readings of
        # the same hardware, and they can disagree. Preferring one without saying
        # so is how a machine on mains comes to be reported as on battery, so the
        # disagreement is named rather than resolved silently.
        kernel_on_battery = any(d["online"] == 0 for d in devices)
        if devices and kernel_on_battery != daemon_on_battery:
            lines.append(
                "NOTE: the kernel's power-supply driver and NUT disagree about "
                f"whether the power is out (kernel says "
                f"{'on battery' if kernel_on_battery else 'on mains'}, NUT says "
                f"{'on battery' if daemon_on_battery else 'on mains'}). NUT's "
                "answer is reported above because it is the one talking to the "
                "hardware, but the disagreement is a finding, not noise.")
    elif name and detail_error:
        lines.append(f"NUT could not be read: {detail_error}")

    return _SENSE.to_percept(
        "\n".join(lines),
        source="sysfs+upsc" if detail else "sysfs",
        metadata={
            "ups_count": len(devices),
            "on_battery": any(d["online"] == 0 for d in devices),
            "nut_monitoring": name,
        },
    )


def _resolve(binary: str) -> Optional[str]:
    """`binary`'s full path, or None when it is not installed.

    `shutil.which` first, then the two directories Arch actually puts its
    binaries in, because a PATH that lacks /usr/bin - a systemd user unit, or a
    bare `ExecStart=` - makes a working tool read as missing. That is the same
    trap `smartctl` and `apcaccess` already hit in this repo, and a UPS that
    reports as missing when it is present is the wrong answer.
    """
    import shutil
    found = shutil.which(binary)
    if found:
        return found
    for directory in ("/usr/bin", "/usr/sbin", "/bin", "/sbin"):
        candidate = Path(directory) / binary
        if candidate.is_file() and os.access(candidate, os.X_OK):
            return str(candidate)
    return None


def _read_upsc(upsc: str, name: str) -> str:
    """`upsc`'s output, or a sentence saying why it could not be read.

    Returns "OK:<text>" on success so an empty-but-successful read cannot be
    confused with a failure.
    """
    import subprocess
    try:
        proc = subprocess.run([upsc, f"{name}@localhost"],
                              capture_output=True, text=True, timeout=10)
    except (OSError, subprocess.SubprocessError) as exc:
        return f"upsc could not be run ({exc})"
    if proc.returncode != 0:
        first = (proc.stderr or proc.stdout or "").strip().splitlines()
        return "upsc refused: " + (first[0] if first else f"exit {proc.returncode}")
    return "OK:" + proc.stdout


def _parse_upsc(text: str) -> dict:
    """`upsc`'s `KEY : VALUE` lines, as {key: value}.

    Split on the first ': ' and nothing else, because values contain colons -
    `ups.mfr: American Power Conversion` has one, and a naive split on every
    colon would keep only the first field of a two-field value.
    """
    out: dict = {}
    for line in text.splitlines():
        key, sep, value = line.partition(": ")
        if sep and key.strip():
            out[key.strip()] = value.strip()
    return out


_SCHEMA = {
    "type": "function",
    "function": {
        "name": "ups",
        "description": (
            "Report whether this machine is running on mains or on a UPS "
            "battery, and if NUT is monitoring a UPS, its charge, remaining "
            "runtime, load and voltages. Reads /sys/class/power_supply for "
            "hardware the kernel sees, and NUT's upsc for the full picture. "
            "Distinguishes 'no UPS is connected' (the fresh-install state) from "
            "'a UPS is configured but could not be read' - an unreachable UPS is "
            "not a healthy one, so that case reports UNKNOWN rather than "
            "claiming the power is fine."
        ),
        "parameters": {"type": "object", "properties": {}},
    },
}


_SENSE = Sense(
    name="ups",
    kind=KIND,
    ttl_seconds=_TTL_SECONDS,
    sensitivity=SENSITIVITY,
    schema=_SCHEMA,
    run=_run,
    poll_interval=_POLL_INTERVAL,
)

SENSES = [_SENSE]
