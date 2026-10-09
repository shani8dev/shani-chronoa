"""Skill: describe the machine Chronoa is running on.

Deliberately built from `/proc`, `/etc/os-release` and `os.uname()` rather than
from shelling out to `uname`, `lsb_release` and `hostnamectl`. Those are three
separate binaries that can each be absent on a minimal or non-systemd image, so
a shell-based version needs three failure paths for one question and degrades
to a partial answer on exactly the stripped-down systems where knowing what you
are running on matters most. The kernel exposes all of it directly, so there is
nothing here that can be missing.

This overlaps the `senses` layer on purpose but is not a duplicate of it. A
sense is polled and deposited as a percept to sit in the assistant's context
whether or not anyone asked; this answers a direct question, on demand, and is
the skill that reaches an MCP client. Both being present is the point - the same
facts are available when asked for and available in advance.

Honesty rules: a field that cannot be read says so by name. An absent
`/proc/uptime` is "uptime unknown", not "0 seconds uptime"; a missing
`/etc/os-release` is "distribution unknown" rather than a guess at the host's
origin. A zero is reported as a zero, and nothing is rounded into a state the
reader cannot distinguish from a failure.
"""

from __future__ import annotations

import logging
import os
import socket
from pathlib import Path

from shani_chronoa.skills import Skill

logger = logging.getLogger(__name__)

_SCHEMA = {
    "type": "function",
    "function": {
        "name": "system_info",
        "description": (
            "Describe the machine Chronoa is running on: distribution, kernel, "
            "architecture, uptime, load, memory, CPU count, whether this is a "
            "desktop session or a container, and the hardware identity (make, "
            "model, board, BIOS version)."
        ),
        "parameters": {"type": "object", "properties": {}},
    },
}

_MEMINFO_KEYS = ("MemTotal", "MemAvailable")


def _read(path: str) -> str | None:
    try:
        return Path(path).read_text()
    except (OSError, ValueError):
        return None


def _distribution() -> str:
    raw = _read("/etc/os-release")
    if raw is None:
        return "distribution: unknown (/etc/os-release is not readable here)"
    fields = dict(
        line.split("=", 1)
        for line in raw.splitlines()
        if "=" in line and not line.startswith("#")
    )
    pretty = fields.get("PRETTY_NAME", "").strip().strip('"')
    if pretty:
        return f"distribution: {pretty}"
    name = fields.get("NAME", "").strip().strip('"')
    version = fields.get("VERSION", "").strip().strip('"')
    if name:
        return f"distribution: {name} {version}".rstrip()
    return "distribution: unknown (/etc/os-release has no NAME or PRETTY_NAME)"


def _uptime() -> str:
    raw = _read("/proc/uptime")
    if raw is None:
        return "uptime: unknown (/proc/uptime is not readable - not a Linux host?)"
    try:
        seconds = float(raw.split()[0])
    except (IndexError, ValueError):
        return "uptime: unknown (/proc/uptime was not readable as a number)"
    if seconds < 0:
        return "uptime: unknown (/proc/uptime reported a negative value)"
    days, rem = divmod(int(seconds), 86400)
    hours, rem = divmod(rem, 3600)
    minutes = rem // 60
    if days:
        return f"uptime: {days}d {hours}h {minutes}m"
    if hours:
        return f"uptime: {hours}h {minutes}m"
    return f"uptime: {minutes}m"


def _load() -> str:
    raw = _read("/proc/loadavg")
    if raw is None:
        return "load: unknown (/proc/loadavg is not readable)"
    parts = raw.split()
    if len(parts) < 3:
        return f"load: unknown (/proc/loadavg was malformed: {raw.strip()!r})"
    return f"load average: {', '.join(parts[:3])} (1, 5, 15 min)"


def _memory() -> str:
    raw = _read("/proc/meminfo")
    if raw is None:
        return "memory: unknown (/proc/meminfo is not readable)"
    values: dict[str, int] = {}
    for line in raw.splitlines():
        key, _, rest = line.partition(":")
        if key.strip() not in _MEMINFO_KEYS:
            continue
        digits = rest.strip().split()
        if digits and digits[0].isdigit():
            values[key.strip()] = int(digits[0]) // 1024
    if "MemTotal" not in values:
        return "memory: unknown (/proc/meminfo had no MemTotal)"
    total = values["MemTotal"]
    avail = values.get("MemAvailable")
    if avail is None:
        return f"memory: {total} MiB total, available unknown (no MemAvailable field)"
    return f"memory: {avail} MiB available of {total} MiB"


def _cpus() -> str:
    count = os.cpu_count()
    if not count:
        return "cpus: unknown (os.cpu_count() reported nothing)"
    return f"cpus: {count} logical"


def _session() -> str:
    kind = os.environ.get("XDG_SESSION_TYPE", "").strip()
    desktop = os.environ.get("XDG_CURRENT_DESKTOP", "").strip()
    if not kind:
        return (
            "session: XDG_SESSION_TYPE is not set, so this is not a login "
            "session I can identify (or it was started outside one)"
        )
    return f"session: {kind}" + (f" on {desktop}" if desktop else "")


def _identity() -> str:
    """Make, model, board and BIOS, from `/sys/class/dmi/id` without root.

    **Why the sysfs tree and not `dmidecode`.** Measured on this box:
    `dmidecode -s bios-version` prints *"Permission denied"* and
    *"Can't read memory from /dev/mem"* while **exiting 0** - so the tool
    most people reach for is unusable without root and silent about it. The
    same facts are world-readable in `/sys/class/dmi/id` (`bios_version`,
    `board_name`, `sys_vendor`, ...), which is the pattern this package
    already follows for processes and ports: read the kernel's own file
    rather than a binary that may be missing, restricted, or both.

    **Only the fields the kernel makes readable.** `product_serial` and
    friends are root-only by design and are never read, because a skill
    reporting `Permission denied` for half a table reads as a broken answer
    rather than as a permission boundary. A missing file is reported as
    unknown, never as blank.
    """
    fields = (("vendor", "sys_vendor"), ("model", "product_name"),
              ("board", "board_name"), ("bios", "bios_version"),
              ("bios date", "bios_date"))
    parts = []
    for label, name in fields:
        raw = _read(f"/sys/class/dmi/id/{name}")
        value = (raw or "").strip()
        # Some DMI strings end in whitespace by design, and a value that is
        # only whitespace is not a value.
        parts.append(f"{label}: {value}" if value and value.strip()
                     else f"{label}: unknown")
    return ("machine: " + ", ".join(parts)
            if any(not p.endswith("unknown") for p in parts)
            else "machine: unknown (no readable /sys/class/dmi/id - a container?)")


def _virtualisation() -> str:
    if Path("/run/systemd/container").exists():
        return f"virtualisation: container ({Path('/run/systemd/container').read_text().strip()})"
    if Path("/.dockerenv").exists():
        return "virtualisation: container (docker)"
    if Path("/proc/1/cgroup").exists():
        raw = _read("/proc/1/cgroup") or ""
        for marker in ("lxc", "libpod", "kubepods", "containerd"):
            if marker in raw:
                return f"virtualisation: container ({marker})"
    product = _read("/sys/class/dmi/id/product_name")
    if product and product.strip():
        return f"virtualisation: hardware (DMI product name: {product.strip()})"
    return "virtualisation: unknown (no container marker, no readable DMI product)"


def _run(_arguments: dict) -> str:
    uname = os.uname()
    return "\n".join([
        f"hostname: {socket.gethostname()}",
        f"kernel: {uname.release} ({uname.version})",
        f"architecture: {uname.machine}",
        _distribution(),
        _identity(),
        _uptime(),
        _load(),
        _memory(),
        _cpus(),
        _session(),
        _virtualisation(),
    ])


SKILLS = [Skill(name="system_info", schema=_SCHEMA, run=_run)]
