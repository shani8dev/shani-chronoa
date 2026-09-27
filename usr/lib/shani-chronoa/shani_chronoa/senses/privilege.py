"""Privilege sense: which processes hold a capability they should not need.

Every process exposes its effective capability set as a hex `CapEff` in
`/proc/<pid>/status`. This reads those, looks for a small set of capabilities
that are genuinely dangerous in ordinary operation, and reports the holders.

**This matches on a capability bit, never on a program name.** That is the
whole design and it is the difference between a diagnostic and a blacklist. A
name list would be trivially evaded and would also be wrong: `curl` is fine,
`curl` running as root is not, and the same binary is both depending on who
launched it. `CAP_NET_RAW` on a process is the fact worth knowing. Naming
software instead would report a program as dangerous because of what it is,
which is not the question.

**"Unmanaged" is the discriminator, not "unknown".** A first version of this
excluded a hand-written list of system daemons, and run against a real desktop
that produced forty lines of expected hits - `gdm3`, `cron`, `catatonit`,
`switcheroo-control`, `fusermount3`, `systemd-udevd` - and buried the one line
that mattered. A reader who sees forty known-good entries every hour stops
reading the output, which is worse than having no sense at all, and the
interesting signal is exactly the thing a name list would have had to
eventually be extended with.

So the question asked is not "is this program suspicious" but **"is this
binary owned by a distribution package?"** Everything the distro installed is
part of the operating system and is expected to hold what it holds;
software that no package owns did not arrive with the OS, and is the thing
worth a second look. That is a fact about provenance, not about identity, so
it needs no vendor list and does not go stale when a package is renamed.

Read-only: this never signals, never writes, and never changes a capability.
"""
import logging
import os
import shutil
import subprocess
from pathlib import Path
from typing import Optional, Union

from shani_chronoa.config import ChronoaConfig
from shani_chronoa.senses import SENSITIVITY_PERSONAL, Sense
from shani_chronoa.senses.context import Percept

logger = logging.getLogger(__name__)

KIND = "machine-state"
SENSITIVITY = SENSITIVITY_PERSONAL
_TTL_SECONDS = 300.0
_POLL_INTERVAL = 300.0

_PROC = Path("/proc")

# Bit number -> (name, why it matters). Deliberately small: a list long enough
# to read as a catalogue stops being a signal.
WATCHED = {
    13: ("CAP_NET_RAW", "can read raw packets and spoof addresses"),
    16: ("CAP_SYS_MODULE", "can load kernel modules"),
    17: ("CAP_SYS_RAWIO", "can use raw I/O ports"),
    18: ("CAP_SYS_CHROOT", "can chroot"),
    21: ("CAP_SYS_ADMIN", "has the broad administrative capability"),
}

# Daemons whose entire purpose is to hold these bits. Excluding them is what
# keeps the report readable; without it, a normal desktop produces dozens of
# expected hits.
LEGITIMATE = frozenset({
    "systemd", "systemd-resolve", "systemd-resolved", "systemd-networkd",
    "NetworkManager", "wpa_supplicant", "dhcpcd", "dhclient", "dnsmasq",
    "unbound", "avahi-daemon", "containerd", "dockerd", "firewalld",
    "ModemManager", "udisksd", "unattended-upgr", "snapd", "cupsd", "chronyd",
})

_SCHEMA = {
    "type": "function",
    "function": {
        "name": "privilege",
        "description": (
            "Report processes holding a Linux capability that is dangerous in "
            "ordinary operation - raw packet access, module loading, raw I/O, "
            "chroot, or the broad administrative capability - excluding system "
            "daemons that legitimately require them. Matches on the capability "
            "bit rather than on program name, so the same binary is judged by "
            "how it was launched, not what it is. Read-only."
        ),
        "parameters": {"type": "object", "properties": {}},
    },
}


def _proc_field(pid: str, field: str) -> Optional[str]:
    try:
        for line in (Path(_PROC) / pid / "status").read_text().splitlines():
            if line.startswith(f"{field}:"):
                return line.split(":", 1)[1].strip()
    except (OSError, ValueError):
        return None
    return None


def _cmdline(pid: str) -> str:
    try:
        return (Path(_PROC) / pid / "cmdline").read_bytes().replace(b"\0", b" ").strip().decode(
            "utf-8", "replace"
        )
    except OSError:
        return ""


def _exe_path(pid: str, cmdline: str) -> str:
    """The real executable path for a process.

    The first command-line token is only a hint: a bare `sh`, `su` or
    `catatonit` is a name on $PATH, not a path, and a package query about a
    name answers nothing. `/proc/<pid>/exe` is the resolved path, and the
    command line is the fallback when the link is unreadable.
    """
    try:
        resolved = os.readlink(Path(_PROC) / pid / "exe")
        if resolved.startswith("/"):
            return resolved
    except OSError:
        pass
    token = cmdline.split()[0] if cmdline.split() else ""
    return token if token.startswith("/") else ""


# Which tool answers "which package owns this file", per distro. Chronoa
# ships on Arch-based ShaniOS, so a Debian-only implementation is not a
# portability detail - on the actual target `dpkg-query` does not exist, the
# lookup returns nothing, and every process on the machine is then reported as
# unmanaged third-party software. That is not a degraded mode, it is the
# sense actively crying wolf on its own distribution.
_OWNERSHIP_TOOLS = (
    ("dpkg-query", ["-S", "--"]),          # Debian, Ubuntu
    ("pacman", ["-Qo"]),                   # Arch, ShaniOS
    ("rpm", ["-qf"]),                      # Fedora
    ("zypper", ["what-provides"]),         # openSUSE
)


def _ownership_command(paths: "list[str]") -> "Optional[tuple]":
    """An argv prefix that answers ownership for these paths, or None."""
    for tool, prefix in _OWNERSHIP_TOOLS:
        if shutil.which(tool) is not None:
            return [tool, *prefix, *paths]
    return None


def _package_owned(paths: "list[str]") -> "Optional[dict]":
    """Map each path to whether a distribution package owns it.

    One batched call rather than a call per process: this runs on an
    unattended poll and a dozen subprocesses a minute is a cost the sense
    should not impose.

    Returns None when no ownership tool is available, which the caller must
    treat as "provenance undeterminable" - never as "unmanaged". Those are
    opposite claims and only one of them is safe to guess.
    """
    unique = sorted({p for p in paths if p.startswith("/")})
    if not unique:
        return {}
    argv = _ownership_command(unique)
    if argv is None:
        return None
    try:
        proc = subprocess.run(
            argv, capture_output=True, text=True, timeout=15, check=False,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        logger.debug("ownership lookup failed: %s", exc)
        return None
    owned: dict = {}
    for line in (proc.stdout or "").splitlines():
        # `dpkg-query -S` prints "<package>: <path>"; `pacman -Qo` prints
        # "<path> is owned by <package>". Package names cannot contain ": ",
        # so the leading separator is the first one, and the path is either
        # the text before it or the first whitespace-delimited field.
        package, separator, path = line.partition(": ")
        candidate = path if separator and path in unique else line.split()[0] if line.split() else ""
        if candidate in unique:
            owned[candidate] = True
    return {p: owned.get(p, False) for p in unique}


def read_holders() -> list:
    """Processes holding a watched capability, split by package provenance."""
    try:
        pids = sorted(entry.name for entry in _PROC.iterdir() if entry.name.isdigit())
    except OSError:
        return []
    candidates = []
    for pid in pids:
        if int(pid) <= 2:
            continue
        cmdline = _cmdline(pid)
        if not cmdline:
            # Kernel threads have an empty cmdline by design, and cannot hold
            # a userspace capability set worth reporting here.
            continue
        name = _proc_field(pid, "Name") or cmdline.split()[0]
        if name in LEGITIMATE:
            continue
        raw = _proc_field(pid, "CapEff")
        if not raw:
            continue
        try:
            effective = int(raw, 16)
        except ValueError:
            continue
        held = [(cap, why) for bit, (cap, why) in WATCHED.items() if effective & (1 << bit)]
        if held:
            candidates.append({
                "pid": int(pid), "name": name, "cmdline": cmdline,
                "exe": _exe_path(pid, cmdline), "held": held,
            })

    owned = _package_owned([c["exe"] for c in candidates])
    for c in candidates:
        if owned is None:
            c["provenance"] = "unknown"
        elif not c["exe"]:
            # No resolvable path at all. Treated as unknown rather than
            # unmanaged: a process whose exe link cannot be read is not
            # evidence of anything, and defaulting it to "unmanaged" would
            # accuse the distribution's own daemons whenever the link is
            # unreadable, which is the direction that teaches a reader to
            # ignore the output.
            c["provenance"] = "unknown"
        elif owned.get(c["exe"]) is None:
            c["provenance"] = "unknown"
        else:
            c["provenance"] = "managed" if owned[c["exe"]] else "unmanaged"
    return candidates


def _run(arguments: dict) -> Union[str, Percept]:
    config = ChronoaConfig()
    if not config.sense_allowed("privilege"):
        return f"Not reading process privileges: {config.sense_allowed_reason('privilege')}."

    holders = read_holders()
    unmanaged = [h for h in holders if h["provenance"] == "unmanaged"]
    unknown = [h for h in holders if h["provenance"] == "unknown"]
    if not holders:
        return "No non-system process holds a dangerous capability on this machine."

    def _line(h: dict) -> str:
        caps = ", ".join(cap for cap, _ in h["held"])
        return f"pid {h['pid']} {h['name']} ({h['exe'] or 'path unavailable'}) holds {caps}"

    lines = []
    if unmanaged:
        lines.append(
            f"{len(unmanaged)} of {len(holders)} holder(s) are not owned by any "
            "distribution package:"
        )
        for h in unmanaged:
            lines.append(f"  {_line(h)}")
            for cap, why in h["held"]:
                lines.append(f"      {cap}: {why}")
    else:
        lines.append("No unmanaged software holds a dangerous capability.")
    if unknown:
        lines.append(
            f"{len(unknown)} holder(s) could not be attributed because their "
            "executable path is unreadable - provenance unknown, not presumed:"
        )
        for h in unknown:
            lines.append(f"  {_line(h)}")
    managed = len(holders) - len(unmanaged) - len(unknown)
    if managed:
        lines.append(
            f"{managed} further holder(s) are distribution packages and are "
            "expected to hold what they hold."
        )
    return _SENSE.to_percept(
        "\n".join(lines),
        source="proc-capabilities",
        metadata={"holders": len(holders), "unmanaged": len(unmanaged), "unknown": len(unknown)},
    )


_SENSE = Sense(
    name="privilege",
    kind=KIND,
    ttl_seconds=_TTL_SECONDS,
    sensitivity=SENSITIVITY,
    schema=_SCHEMA,
    run=_run,
    poll_interval=_POLL_INTERVAL,
)

SENSES = [_SENSE]
