"""Sense: when did this machine last boot, how often does it, and did it shut down cleanly?

`cpu` already reports uptime from `/proc/uptime` and this sense deliberately does
not repeat it. Uptime answers "how long has it been up", which is a live
counter; what nothing else reports is the *history* behind it: when this boot
started in wall-clock terms, how many boots this machine has had, and whether
each one ended in an orderly shutdown or the process died and the kernel had to
recover. A machine that reboots uncleanly, repeatedly, is a machine with a real
fault, and the only evidence is the gap pattern in the login records - which is
invisible from uptime alone, because uptime is perfectly happy to reset.

Three sources, each with its own honest failure:

- **`/proc/sys/kernel/random/boot_id`** is a fresh random UUID per boot. It is
  the only exact identity of "this boot" and it is unreadable in a container or
  on a locked-down `/proc`. Unreadable is UNKNOWN, and deliberately *not*
  replaced by a tick count or a timestamp derived from uptime: a bare number
  with no meaning attached is the "plausible-looking wrong answer" this project
  has already shipped in other forms.
- **The `btime` line of `/proc/stat`** is the boot instant as a Unix timestamp.
  Its real job here is converting `/proc/<pid>/stat`'s start-time field - which
  is a count of clock ticks since boot, meaningless on its own - into a wall
  time. Without `btime` that conversion is impossible and the sense says so.
- **The login records** (`/var/log/wtmp`, via `last -b`) give the boot count and
  the shutdown records that establish cleanliness.

**No readable login database is UNKNOWN, never "never rebooted."** A machine
with `wtmp` disabled, a container without `/var/log`, or a `last` binary from a
different package family produces no output. Reading that as "no boots
recorded" is the same class of error as the `fuser` one: an absent record
becomes a positive claim, and "this machine has never rebooted" is a claim
somebody will act on.

**Cleanliness is derived from records, and derived conservatively.** A shutdown
record after every boot means every boot was orderly. A boot with no shutdown
record before the next one means the machine lost power or the kernel panicked.
The report counts the *unmatched* boots and says what the absence means, rather
than summarising "mostly clean" - a percentage would hide the one bad shutdown
that is the entire reason to look.
"""

from __future__ import annotations

import logging
import os
import shutil
import subprocess
import time
from pathlib import Path
from typing import List, Optional, Union

from shani_chronoa.config import ChronoaConfig
from shani_chronoa.senses import SENSITIVITY_PUBLIC, Sense
from shani_chronoa.senses.context import Percept

logger = logging.getLogger(__name__)

KIND = "machine-state"
SENSITIVITY = SENSITIVITY_PUBLIC
_TTL_SECONDS = 900.0
_POLL_INTERVAL = 900.0
_TIMEOUT = 15

_PROC = Path("/proc")
_BOOT_ID = _PROC / "sys/kernel/random/boot_id"
_STAT = _PROC / "stat"
_WTMP = Path("/var/log/wtmp")

#: Start-time is field 22 of `/proc/<pid>/stat`; the comm field is field 2, so
#: after splitting on the closing parenthesis the remaining list starts at field
#: 3 and the index is 22 - 3.
_START_TIME_INDEX = 19

_MAX_RECORDS = 10


def _read_text(path: Path) -> Optional[str]:
    try:
        return path.read_text(errors="replace")
    except OSError:
        return None


def _clock_ticks() -> Optional[int]:
    """`SC_CLK_TCK`, which is what `/proc/<pid>/stat`'s start-time counts in."""
    try:
        ticks = os.sysconf("SC_CLK_TCK")
    except (AttributeError, ValueError, OSError):
        return None
    return int(ticks) if isinstance(ticks, int) and ticks > 0 else None


def read_boot_id() -> Optional[str]:
    """This boot's identifier, or None when it could not be read.

    None is not a fallback point for anything else in this file. A boot
    identity that cannot be read is not replaced by a tick count or by
    subtracting uptime from the current time and calling the result the boot
    time - those are different claims with different error bars.
    """
    raw = _read_text(_BOOT_ID)
    if not raw:
        return None
    value = raw.strip()
    return value if len(value) >= 8 else None


def read_btime() -> Optional[int]:
    """The boot instant as a Unix timestamp, from the `btime` line of /proc/stat."""
    text = _read_text(_STAT)
    if not text:
        return None
    for line in text.splitlines():
        if line.startswith("btime "):
            raw = line.split()[1] if len(line.split()) > 1 else ""
            return int(raw) if raw.isdigit() else None
    return None


def read_process_started(pid: str, btime: Optional[int],
                         ticks: Optional[int]) -> Optional[float]:
    """Wall-clock time a process started, or None when it cannot be derived.

    Needs both `btime` and the tick rate: the kernel's field counts ticks since
    boot, and ticks mean nothing without knowing what a tick is.
    """
    if btime is None or ticks is None:
        return None
    text = _read_text(_PROC / pid / "stat")
    if not text:
        return None
    tail = text.rsplit(")", 1)
    if len(tail) != 2:
        return None
    fields = tail[1].split()
    if len(fields) <= _START_TIME_INDEX:
        return None
    raw = fields[_START_TIME_INDEX]
    if not raw.isdigit():
        return None
    return btime + int(raw) / ticks


def read_boot_records() -> Optional[dict]:
    """Boot and shutdown records, or None when no database could be consulted.

    None covers three separate situations that all look identical from here: no
    wtmp, no `last`, and a `last` that failed. All three are "not asked".
    """
    if not _WTMP.is_file():
        return None
    if shutil.which("last") is None:
        return None

    boots = _last_records(["-b", "-F"], "reboot")
    if boots is None:
        return None
    shutdowns = _last_records(["-x", "-F"], "shutdown")
    return {
        "boots": boots,
        # A `last` that could not report shutdowns is not the same as one that
        # reported none, so absence stays None rather than becoming [].
        "shutdowns": shutdowns,
    }


def _last_records(flags: List[str], needle: str) -> Optional[List[float]]:
    """Epoch timestamps of `last` records matching `needle`, oldest first.

    `-F` is what forces the full ISO timestamp; without it `last` prints a
    locale-dependent abbreviated form that cannot be parsed reliably, and a
    parser that guesses at that format is how a boot record silently becomes the
    wrong year.
    """
    try:
        proc = subprocess.run(
            ["last", *flags, "-n", "40", needle],
            capture_output=True, text=True, timeout=_TIMEOUT, check=False)
    except (subprocess.TimeoutExpired, OSError) as exc:
        logger.debug("last failed: %s", exc)
        return None
    if proc.returncode != 0:
        return None

    stamps: List[float] = []
    for line in (proc.stdout or "").splitlines():
        if needle not in line:
            continue
        stamp = _parse_last_timestamp(line)
        if stamp is not None:
            stamps.append(stamp)
    stamps.sort()
    return stamps


def _parse_last_timestamp(line: str) -> Optional[float]:
    """The leading `YYYY-MM-DD HH:MM:SS` of a `last -F` line, as an epoch."""
    parts = line.split()
    if len(parts) < 2 or not parts[0].count("-") == 2:
        return None
    try:
        return time.mktime(time.strptime(f"{parts[0]} {parts[1]}",
                                         "%Y-%m-%d %H:%M:%S"))
    except (ValueError, OverflowError):
        return None


def _unmatched_boots(boots: List[float], shutdowns: Optional[List[float]]) -> int:
    """Boots with no shutdown record after them, i.e. not an orderly stop.

    Only the newest boot is excluded, because it is the one currently running
    and has not ended yet. A shutdown that happened *before* the oldest
    recorded boot is not this function's business, and does not need to be in
    the log: the question is only whether each completed boot was followed by a
    stop record before the next boot started.
    """
    if shutdowns is None:
        return 0
    ordered = sorted(boots)
    unmatched = 0
    for index, started in enumerate(ordered):
        if index == len(ordered) - 1:
            continue
        if not any(s > started for s in shutdowns):
            unmatched += 1
    return unmatched


def _fmt(stamp: float) -> str:
    return time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(stamp))


def _run(arguments: dict) -> Union[str, Percept]:
    config = ChronoaConfig()
    if not config.sense_allowed("boots"):
        return f"Not reading boot history: {config.sense_allowed_reason('boots')}."

    boot_id = read_boot_id()
    btime = read_btime()
    ticks = _clock_ticks()
    init_started = read_process_started("1", btime, ticks)

    if boot_id is None and btime is None:
        return (
            "Boot history is UNKNOWN: /proc/sys/kernel/random/boot_id and the "
            "btime line of /proc/stat could both not be read, so this boot has "
            "no identity and no start time. That is not the same as a machine "
            "that has never rebooted, and no tick count is substituted for the "
            "missing boot identity."
        )

    lines = []
    if boot_id:
        lines.append(f"this boot's id: {boot_id}")
    if btime:
        age = int(max(0.0, time.time() - btime))
        lines.append(f"booted at: {_fmt(btime)} ({age}s ago)")
    if init_started is not None:
        lines.append(
            f"  the init process started at {_fmt(init_started)} - the start-time "
            f"field of /proc/1/stat converted with btime and SC_CLK_TCK, which is "
            f"how a tick counter becomes a wall clock"
        )
    else:
        lines.append(
            "  when the init process started: UNKNOWN - converting "
            "/proc/<pid>/stat's tick counter needs btime and SC_CLK_TCK, and at "
            "least one of them was unavailable"
        )

    records = read_boot_records()
    if records is None:
        lines.append(
            "boot history: UNKNOWN - boot history unavailable. /var/log/wtmp is "
            "absent or unreadable, or no `last` binary is installed, so no boot "
            "was asked for. This is NOT a machine that has never rebooted."
        )
        return _SENSE.to_percept(
            "\n".join(lines),
            source="proc",
            metadata={
                "boot_id_known": boot_id is not None,
                "boot_time_known": btime is not None,
                "boot_count": None,
                "unclean_boots": None,
            },
        )

    boots = records["boots"]
    shutdowns = records["shutdowns"]
    lines.append(f"recorded boots: {len(boots)}")
    for stamp in boots[-_MAX_RECORDS:]:
        lines.append(f"  {_fmt(stamp)}")
    if len(boots) > _MAX_RECORDS:
        lines.append(f"  and {len(boots) - _MAX_RECORDS} more")

    if shutdowns is None:
        lines.append(
            "clean shutdowns: UNKNOWN - `last -x` could not report shutdown "
            "records, so no verdict on whether this machine shuts down cleanly "
            "is offered"
        )
    else:
        unmatched = _unmatched_boots(boots, shutdowns)
        lines.append(f"shutdown records: {len(shutdowns)}")
        if unmatched == 0:
            lines.append(
                "  every recorded boot is followed by a shutdown record, so "
                "every one of them ended in an orderly stop"
            )
        else:
            lines.append(
                f"  {unmatched} boot(s) have NO shutdown record before the next "
                f"one started. The machine lost power, was reset, or the kernel "
                f"panicked on those occasions - that gap is the whole reason to "
                f"look, so it is counted rather than averaged into a percentage."
            )

    return _SENSE.to_percept(
        "\n".join(lines),
        source="proc+wtmp",
        metadata={
            "boot_id_known": boot_id is not None,
            "boot_time_known": btime is not None,
            "boot_count": len(boots),
            "shutdown_count": None if shutdowns is None else len(shutdowns),
            "unclean_boots": (
                None if shutdowns is None
                else _unmatched_boots(boots, shutdowns)
            ),
        },
    )


_SCHEMA = {
    "type": "function",
    "function": {
        "name": "boots",
        "description": (
            "Report this boot's identity and start time, how many boots the "
            "login database records, and how many of those boots ended without "
            "a shutdown record - which is how a machine that is losing power or "
            "panicking shows up. Uses boot_id, the btime line of /proc/stat, and "
            "the wtmp boot and shutdown records. Does NOT report uptime, which "
            "the processor sense already owns. Reports UNKNOWN - never 'never "
            "rebooted' - when the login database cannot be read."
        ),
        "parameters": {"type": "object", "properties": {}},
    },
}


_SENSE = Sense(
    name="boots",
    kind=KIND,
    ttl_seconds=_TTL_SECONDS,
    sensitivity=SENSITIVITY,
    schema=_SCHEMA,
    run=_run,
    poll_interval=_POLL_INTERVAL,
)

SENSES = [_SENSE]
