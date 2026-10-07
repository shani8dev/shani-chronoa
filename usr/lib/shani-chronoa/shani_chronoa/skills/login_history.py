"""Skill: who logged in to this machine recently, and when?

Login history is other people's presence, not this machine's hardware. That is
the reason the `sessions` sense is the one machine-state sense that defaults
**off**, and this skill uses the same switch rather than minting a new one:
"who else has been on this computer" is the same agreement whether it is
volunteered in the background or asked for directly. A second key would let a
fresh install ship one open and the other shut.

Source: `last` (util-linux) reading `/var/log/wtmp`. When `last` is absent the
file is read directly - the record layout is fixed on Linux (384 bytes, glibc
`struct utmp`) - so a missing binary does not become a missing answer.

On Shanios `/var/log` is its own persistent btrfs subvolume, so the history
survives reboots and slot switches even though the rest of `/var` is volatile.

Honesty rules: an unreadable or absent wtmp is UNKNOWN, never "nobody logged
in"; a session still open is said to be still open.
"""

from __future__ import annotations

import datetime as _dt
import shutil
import struct
import subprocess
from pathlib import Path

from shani_chronoa.config import ChronoaConfig
from shani_chronoa.skills import Skill

_SENSE = "sessions"
_WTMP = Path("/var/log/wtmp")
_TIMEOUT = 15
_DEFAULT = 15
_MAX = 50
_RECORD = struct.Struct("<hxxi32s4s32s256shhiii16s20s")  # glibc struct utmp, x86_64
_USER_PROCESS, _DEAD_PROCESS, _BOOT_TIME = 7, 8, 2

SCHEMA = {
    "type": "function",
    "function": {
        "name": "login_history",
        "description": (
            "Recent logins to this machine: which user, from which terminal or "
            "remote address, when they started and how long they lasted, and "
            "recent reboots. Use for 'who logged in', 'has anyone used my "
            "computer'. Requires the 'sessions-sense-enabled' consent key, "
            "because it reports other people's presence. Read-only."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "limit": {"type": "integer",
                          "description": f"How many entries to show (default {_DEFAULT})."},
            },
        },
    },
}


def _consent(config: ChronoaConfig) -> "tuple[bool, str]":
    if config.sense_allowed(_SENSE):
        return True, ""
    return False, (f"reading login history is turned off ({config.sense_allowed_reason(_SENSE)}; "
                   f"enable 'sessions-sense-enabled' in Settings). It reports other "
                   f"people's presence on this machine, so it is off unless you choose it.")


def _limit(arguments: dict) -> int:
    try:
        n = int(arguments.get("limit") or _DEFAULT)
    except (TypeError, ValueError):
        n = _DEFAULT
    return max(1, min(n, _MAX))


def _text(raw: bytes) -> str:
    return raw.split(b"\0", 1)[0].decode("utf-8", "replace")


def parse_wtmp(data: bytes) -> "list[dict]":
    """Login and boot records, newest first, with a login paired to its logout."""
    records = []
    for off in range(0, len(data) - _RECORD.size + 1, _RECORD.size):
        f = _RECORD.unpack_from(data, off)
        records.append({"type": f[0], "line": _text(f[2]), "user": _text(f[4]),
                        "host": _text(f[5]), "time": f[9]})
    out, open_lines = [], {}
    for rec in records:
        if rec["type"] == _USER_PROCESS and rec["user"]:
            entry = {"user": rec["user"], "line": rec["line"], "host": rec["host"],
                     "start": rec["time"], "end": None}
            out.append(entry)
            open_lines[rec["line"]] = entry
        elif rec["type"] == _DEAD_PROCESS and rec["line"] in open_lines:
            open_lines.pop(rec["line"])["end"] = rec["time"]
        elif rec["type"] == _BOOT_TIME:
            out.append({"user": "reboot", "line": "system boot", "host": "", "start": rec["time"], "end": None})
            for entry in open_lines.values():
                entry["end"] = entry["end"] or "crash"
            open_lines.clear()
    return list(reversed(out))


def _when(ts) -> str:
    return _dt.datetime.fromtimestamp(ts).strftime("%a %d %b %H:%M")


def describe(entries: "list[dict]") -> "list[str]":
    lines = []
    for e in entries:
        if e["user"] == "reboot":
            lines.append(f"  {'(reboot)':<12} {'':<10} {_when(e['start'])}")
            continue
        if e["end"] is None:
            span = "still logged in"
        elif e["end"] == "crash":
            span = "ended by a reboot or crash"
        else:
            span = f"for {_dt.timedelta(seconds=max(0, e['end'] - e['start']))}"
        where = f" from {e['host']}" if e["host"] else ""
        lines.append(f"  {e['user']:<12} {e['line']:<10} {_when(e['start'])}  {span}{where}")
    return lines


def _from_last(limit: int) -> "str | None":
    try:
        proc = subprocess.run(["last", "-F", "-w", "-n", str(limit)], capture_output=True,
                              text=True, timeout=_TIMEOUT, check=False)
    except (subprocess.TimeoutExpired, OSError):
        return None
    if proc.returncode != 0:
        return None
    rows = [l.rstrip() for l in proc.stdout.splitlines() if l.strip()]
    return "\n".join(["Recent logins (newest first, from `last`):", *[f"  {r}" for r in rows]])


def _run(arguments: dict) -> str:
    allowed, reason = _consent(ChronoaConfig())
    if not allowed:
        return f"Refusing to read login history: {reason}"
    limit = _limit(arguments)
    if shutil.which("last") is not None:
        text = _from_last(limit)
        if text:
            return text
    try:
        data = _WTMP.read_bytes()
    except FileNotFoundError:
        return (f"{_WTMP} does not exist, so login history is UNKNOWN - this "
                f"system is not recording logins there. That is not the same as nobody logging in.")
    except OSError as exc:
        return f"{_WTMP} could not be read ({exc}), so login history is UNKNOWN."
    entries = parse_wtmp(data)[:limit]
    if not entries:
        return f"{_WTMP} holds no login records."
    return "\n".join([f"Recent logins (newest first, read from {_WTMP}):", *describe(entries)])


SKILLS = [Skill(name="login_history", schema=SCHEMA, run=_run)]
