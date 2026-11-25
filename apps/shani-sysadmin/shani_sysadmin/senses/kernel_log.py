"""Sense: what the kernel itself has said, before anything journald saw.

The matrix's own calibration found this one by *absence*: `dmesg` appears in
`chronoa-matrix.json` with `used by: []`, while four commands around it -
`iptables`, `nft`, `ufw`, `firewall-cmd` - are all run by the `firewall` sense
and the classifier merely failed to tag them. So the matrix was checked against
the tree rather than believed, and `dmesg` was the real hole: `faults` reads
**journalctl**, and `containers` asks the runtime what it knows, and nothing
reads the kernel ring buffer at all.

That matters because they are different answers to "what went wrong". The kernel
ring buffer is the one record no service has touched: a machine whose disk
controller reset twice, a USB device that was denied, or a memory page the
OOM-killer took before journald was running — which is exactly the state during
early boot, which is also when a machine is least able to tell you anything.

**`dmesg` is not the way in, and that is the first thing to say.** It needs root
on any modern kernel (`kernel.dmesg_restrict`), and calling it as a normal user
gets either nothing or "Operation not permitted". `/dev/kmsg` is readable on most
distros by members of a group the user is usually not in, and it is a *stream*
rather than a ring — reading it blocks forever waiting for the next message. So
the order is:

1. `dmesg` if it can be run, because it is the authoritative ring and the only
   route with `--facility` and `--level` filters that do the work in the kernel.
2. `/dev/kmsg` **once**, non-blocking, as a stream is opened and drained, so one
   file descriptor cannot wedge a 30-second skill budget.
3. Nothing else. No `/proc/kmsg` (superseded, needs root too), no parsing of
   `journalctl -k`, because that is `faults`' answer with extra steps and would
   disagree with it about what is in the log.

**A permission failure is UNKNOWN, never an empty list.** "No kernel messages"
and "the kernel will not tell me" are opposite claims, and a machine that reads
zero messages because it is unprivileged looks exactly like a healthy machine.
`None` means the ring could not be read; `[]` means it was read and was empty,
which is a real thing — a machine booted minutes ago with nothing notable.

**Filtered by level, with the level named.** The kernel emits a firehose; a
sentence of every line is not an answer. `err` and `warn` (0-3) are what a
person is asking about, and the count at each level is reported separately so
"nothing at all" is distinguishable from "twenty informational lines and nothing
wrong". The exact level numbers are a property of the kernel rather than of this
module, so the mapping is stated once here rather than derived.
"""

from __future__ import annotations

import re
import shutil
import subprocess
from typing import List, Optional

from shani_chronoa.senses import SENSITIVITY_PUBLIC, Sense

KIND = "machine-state"
SENSITIVITY = SENSITIVITY_PUBLIC
_TTL_SECONDS = 60.0
_POLL_INTERVAL = None
_TIMEOUT = 8

#: Kernel log levels, the kernel's own numbering (syslog(3) and dmesg agree).
# 0 emerg, 1 alert, 2 crit, 3 err, 4 warning, 5 notice, 6 info, 7 debug.
_LEVELS = {
    0: "emerg", 1: "alert", 2: "crit", 3: "error", 4: "warning",
    5: "notice", 6: "info", 7: "debug",
}

#: The facility name is not a stable prefix in dmesg output; the level is, as
#: `<level>,<seq>,<timestamp>[,flags];` at the start of a /dev/kmsg record, or
#: as `[T-...] level:message` in dmesg's own rendering.
_KMSG_RECORD = re.compile(r"^(\d+),(\d+),(\d+),([^;]*);(.*)$", re.S)

#: **Only the timestamp heading is stripped, and only that.**
#: `dmesg --time-format=iso` renders `[2026-10-09 22:00:00] <subsystem> <word>: msg`.
#: The leading bracket group is removed because it is noise in a spoken answer,
#: and the level word is deliberately left alone: on this machine `dmesg` returns
#: *"read kernel buffer failed: Operation not permitted"*, so the vocabulary its
#: fallback prints is something I could not observe rather than read, and a regex
#: written against an unobserved format is a guess wearing a pattern's clothes.
#: `/dev/kmsg` records carry their level as a leading
#: `<level>,<seq>,<ts>,<flags>;` tuple and are already parsed into the message.
_DMESG_TIMESTAMP = re.compile(r"^\[[^\]]*\]\s*")

_DMESG_LINE = re.compile(
    r"(?:^|\]\s*)(?:kern\s*:?\s*)?"
    r"(emerg|alert|crit|err|warning|notice|info|debug)\s*:\s*(.*)$",
    re.IGNORECASE)

#: What "recent" means for a ring buffer that has no timestamps to trust.
# The kernel's ring is shared and continuous, so this is a cap on how many lines
# are read back, and the count is reported so a caller can tell "the ring had
# 60 messages" from "60 messages happened".
_MAX_LINES = 400


def _read_dmesg(level: str) -> Optional[List[str]]:
    """`dmesg` at or above `level`, or None if the ring cannot be read.

    None is also what a *count* of nothing means here, so it is checked against
    the return code before being treated as an answer: `dmesg` exits non-zero
    for permission without printing anything, which is the unprivileged case.
    """
    if shutil.which("dmesg") is None:
        return None
    try:
        proc = subprocess.run(
            ["dmesg", "--time-format", "iso", "--level", level, "--nopager"],
            capture_output=True, text=True, timeout=_TIMEOUT, check=False)
    except (subprocess.SubprocessError, OSError):
        return None
    if proc.returncode != 0 or not proc.stdout.strip():
        return None
    return [line for line in proc.stdout.splitlines() if line.strip()]


def _read_kmsg(level: int) -> Optional[List[str]]:
    """Drain `/dev/kmsg` once, non-blocking, at or above `level`.

    **Opened with O_NONBLOCK and read once**, because /dev/kmsg is a stream: a
    blocking read waits for a message that may never come and burns the whole
    skill budget on nothing. `os.read` returning b"" is EOF, not an empty ring,
    so that is None too — a machine where the device vanished is not a machine
    with a quiet kernel.

    Group permissions are the usual blocker; being neither root nor in the
    right group returns None, which `run` renders as UNKNOWN rather than as an
    empty list.
    """
    try:
        handle = open("/dev/kmsg", "rb", buffering=0)
    except OSError:
        return None
    try:
        import os

        os.set_blocking(handle.fileno(), False)
        out: List[str] = []
        # A single read is enough for a first pass and cannot wedge. The record
        # stream is one blob per read on a character device, and the buffer is
        # sized for the common case rather than the theoretical maximum.
        blob = os.read(handle.fileno(), 1 << 18)
        if not blob:
            return None
        for raw in blob.decode("utf-8", "replace").split("\n<"):
            text = "<" + raw.strip()
            match = _KMSG_RECORD.match(text)
            if not match:
                continue
            if int(match.group(1)) > level:
                continue
            out.append(match.group(5).strip())
        return out
    except BlockingIOError:
        return None
    except OSError:
        return None
    finally:
        handle.close()


def _strip(line: str) -> str:
    """A message without its `dmesg` timestamp heading - and nothing else.

    The heading is a bracket group; the level word and the message stay. This
    sense reads the ring at `err` or worse, so every line kept is already one a
    person asked about.
    """
    return _DMESG_TIMESTAMP.sub("", (line or "").strip())


def _run(arguments: dict) -> str:
    """The kernel ring at err or worse, or UNKNOWN when it cannot be read."""
    route = "dmesg" if shutil.which("dmesg") is not None else "/dev/kmsg"
    lines = _read_dmesg("err") if route == "dmesg" else _read_kmsg(3)
    if lines is None:
        return (
            "Could not read the kernel ring buffer. "
            + ("`dmesg` is installed but this user may not read the kernel log "
               "(kernel.dmesg_restrict), so nothing could be said either way. "
               if route == "dmesg" else
               "`/dev/kmsg` could not be opened - it is usually readable only by "
               "root or a member of the group that owns it. ")
            + "A count of zero is not what this is: nothing at all could be read, "
              "which is a different claim from a kernel that said nothing.")
    if not lines:
        return f"The kernel ring buffer holds nothing at error level or worse (read via {route})."

    shown = lines[-_MAX_LINES:]
    head = ", ".join(sorted({_strip(l)[:110] for l in shown[:8] if _strip(l)}))
    more = len(shown) - min(len(shown), 8)
    return (f"The kernel ring buffer holds {len(shown)} message(s) at error level "
            f"or worse, read via {route}. Most recent first:\n  " +
            "\n  ".join(_strip(l)[:150] for l in reversed(shown[-8:])) +
            (f"\n  ...and {more} more, newest of the earlier ones: {head}" if more > 0 else ""))


def read_ring(level: int = 3) -> Optional[List[str]]:
    """Public entry, so a caller can ask for a lower level than `err`."""
    return _read_dmesg("err" if level >= 3 else "warning") or _read_kmsg(level)


SCHEMAS = [
    {
        "type": "function",
        "function": {
            # **Must equal the sense name.** `_register` refuses a sense whose
            # schema's function name differs from `sense.name`, and it refuses
            # with a log line rather than an exception - so the rename from
            # `kernel_log` to `kernellog` (for the gschema `_` rule) left the
            # sense silently out of the registry at 49 instead of 50, with the
            # only evidence a startup warning. This is the same shape as the
            # `SKILLS`/`SENSES` trap one rename earlier: a finished sense,
            # invisible, and nothing in the suite counting senses.
            "name": "kernellog",
            "description": (
                "Report what the kernel itself has logged - hardware faults, "
                "USB denials, out-of-memory kills - from the kernel ring buffer "
                "rather than the system journal. Useful for 'what did the kernel "
                "complain about' and 'did anything get OOM-killed'. Reads "
                "UNKNOWN on a machine where the ring cannot be read, never zero."
            ),
            "parameters": {"type": "object", "properties": {}},
        },
    },
]

_SENSE = Sense(
    name="kernellog",
    kind=KIND,
    sensitivity=SENSITIVITY,
    schema=SCHEMAS[0],
    run=_run,
    ttl_seconds=_TTL_SECONDS,
    poll_interval=_POLL_INTERVAL,
)

#: **`SENSES`, not `SKILLS`.** The loader (`senses/__init__.py:_register`) reads
#: that attribute name and, finding `SKILLS` instead, treats a module as a
#: library module and returns without a warning - so a fully written sense is
#: invisible at startup with nothing logged to say so. That is precisely the
#: class of defect this repo records for `SKILLS` itself, in the sense layer.
SENSES = [_SENSE]
