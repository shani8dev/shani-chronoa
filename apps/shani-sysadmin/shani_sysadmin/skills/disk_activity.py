"""Skill: why is my disk busy?

`disk_usage` answers *how full* the disk is, the `storage` sense answers *what
disks exist*, and nothing answered **how hard the disk is working** — which is
the question behind "why is the machine slow" far more often than either. A
disk at 95% utilisation with 40 ms await is the whole answer, and it is a
number no other skill reads.

`iostat -x` from `sysstat` reads it, unprivileged, and it is one of the tools
`shani-tools-extra` ships by design — so the binary is on every image, which is
exactly the case where a skill is worth having.

**Measured on this machine**, so the parse is against real output:

    Device             r/s     rkB/s   awi ms   w/s     wkB/s   ...
    nvme0n1            0.00      0.00     0.00    0.00      0.00  ...

and the columns that answer the question are `r/s` and `w/s` (is anything being
read or written at all) and `%util` (is the disk saturated — the difference
between "busy" and "slow").

**Loop devices are filtered out.** A loop-mounted squashfs or an AppImage shows
as a block device doing nothing useful to report; `lsblk` was the wrong answer
for the same reason in the `storage` sense, and the matrix sweep that shipped
`lsblk`-driven skills learned it by watching the rows.

Two samples, not one: `iostat` reports averages *since boot* on its first
sample, which on a machine that has been up for weeks is a history rather than
a measurement. The second sample, one second apart, is the now.
"""

from __future__ import annotations

import shutil
import subprocess

from shani_chronoa import sense_reading
from shani_chronoa.config import ChronoaConfig
from shani_chronoa.skills import Skill

_TIMEOUT = 30

#: Devices that are not disks. A loop device is a file mounted as a block
#: device; `ram` is RAM. Neither has a queue that can be busy in the sense
#: anyone asking this question means.
_NOT_DISKS = ("loop", "ram", "zram", "dm-", "md")


def _consent(config: ChronoaConfig) -> "tuple[bool, str]":
    """Follows the storage sense's switch: the same subject that sense reports,
    so it wears the same consent (see `sense_reading.py`)."""
    if config.sense_allowed("storage"):
        return True, ""
    return False, sense_reading.refusal(config, "storage")


def _is_disk(name: str) -> bool:
    return bool(name) and not any(name.startswith(p) for p in _NOT_DISKS)


def _sample() -> "tuple[list, str]":
    """`iostat -x 1 2`'s second report: (rows, reason).

    The first report is since-boot; the second, one interval later, is the
    present. Both are taken because the distinction is invisible in the output
    itself — a table of zeros is either an idle disk or a machine that has
    never done anything, and only the second sample tells them apart.
    """
    try:
        proc = subprocess.run(["iostat", "-x", "1", "2"], capture_output=True,
                              text=True, timeout=_TIMEOUT, check=False)
    except subprocess.TimeoutExpired:
        return [], f"iostat did not answer within {_TIMEOUT}s"
    except OSError as exc:
        return [], str(exc)
    if proc.returncode != 0:
        detail = (proc.stderr or "").strip().splitlines()
        return [], detail[-1] if detail else f"exit {proc.returncode}"

    # Reports are separated by a blank line; the header names the columns and
    # repeats once per report. The LAST report is the now.
    reports, current = [], []
    for line in (proc.stdout or "").splitlines():
        if not line.strip():
            if current:
                reports.append(current)
                current = []
            continue
        current.append(line)
    if current:
        reports.append(current)
    if not reports:
        return [], "iostat printed nothing"

    header, *rows = reports[-1]
    columns = header.split()
    parsed = []
    for row in rows:
        cells = row.split()
        if len(cells) < len(columns) or not _is_disk(cells[0]):
            continue
        parsed.append(dict(zip(columns, cells)))
    return parsed, ""


def _run(_arguments: dict) -> str:
    if shutil.which("iostat") is None:
        return ("Disk activity is UNKNOWN: iostat (the sysstat package) is not "
                "installed. shani-tools-extra ships it, so an image without it "
                "is an image that has dropped something it installs by design.")
    config = ChronoaConfig()
    allowed, why = _consent(config)
    if not allowed:
        return why

    rows, problem = _sample()
    if problem:
        return f"Disk activity is UNKNOWN: iostat said {problem!r}. Nothing was guessed."
    if not rows:
        return ("No block devices doing work were found - which on a machine "
                "with a disk means the reports were all loop or RAM devices, "
                "not that the disk is idle.")

    lines = [f"{len(rows)} disk(s) working (per second, over the last second):"]
    busy = []
    for row in rows:
        util = row.get("%util", "?")
        try:
            util_value = float(util)
        except ValueError:
            util_value = None
        # **`r_await` and `w_await`, not a single `await`.** Measured: sysstat
        # reports read and write wait separately, and a parser looking for
        # `await` finds nothing and prints a question mark beside every disk -
        # which reads as a broken probe rather than as a field that is not
        # there. Read wait is the one that shows up as "opening a file is slow";
        # write wait is the one that shows up as "the save dialog hung".
        r_await = row.get("r_await", "?")
        w_await = row.get("w_await", "?")
        lines.append(f"  {row.get('Device', '?'):<16} "
                     f"read {row.get('r/s', '?')}/s (wait {r_await} ms), "
                     f"written {row.get('w/s', '?')}/s (wait {w_await} ms), "
                     f"{util}% used")
        if util_value is not None and util_value >= 80.0:
            busy.append(row.get("Device", "?"))
    if busy:
        lines.append("")
        lines.append(f"Busy right now: {', '.join(busy)} at 80% or more "
                     "utilisation - a queue that deep is where 'slow' comes from.")
    return "\n".join(lines)


SCHEMA = {
    "type": "function",
    "function": {
        "name": "disk_activity",
        "description": (
            "How hard this machine's disks are working right now: reads and "
            "writes per second, average wait, and how much of each disk's time "
            "is spent busy. Answers 'why is my computer slow', 'why is the disk "
            "grinding', 'is something writing all the time'. Read-only; nothing "
            "is started or stopped."
        ),
        "parameters": {"type": "object", "properties": {}},
    },
}

SKILLS = [Skill(name="disk_activity", schema=SCHEMA, run=_run)]
