"""Sense: what is listening on this machine's ports, and who owns it.

Built from `/proc/net/tcp` and `/proc/net/tcp6` cross-referenced against
`/proc/<pid>/fd`, not from one subprocess. That is not fastidiousness - it is
the direct consequence of how this project has already got this question wrong.

**The `fuser` incident, and why this file is shaped the way it is.** The `capture`
sense (formerly `contention`) asked whether anything was holding the microphone
or the camera, and it answered with `fuser`. On a machine without `psmisc`
installed, `fuser` is not there; the resulting `OSError` was swallowed and the
sense reported **every device as free**. It was green on the development box,
which had psmisc, and wrong on the distribution ShaniOS actually ships to. A
missing tool was silently converted into "nothing is using this", which is the
single most dangerous direction for an answer to be wrong in: the user is told
their microphone is available, and it is not.

So the socket table is read from `/proc`, which needs no package, and the two
failure directions are both made explicit:

- **`/proc/net/tcp` unreadable is UNKNOWN, never "nothing is listening."** The
  exact `fuser` failure, without the dependency. A machine whose `/proc` is not
  mounted gets an honest "I could not enumerate sockets".
- **An unreadable `/proc/<pid>/fd` is "owner unknown", not "no owner".** Another
  user's process, or `hidepid=2` on the mount, produces `EACCES` on the fd
  directory while `/proc/net/tcp` stays perfectly readable. Reporting those
  sockets as unowned would say that nothing holds port 22 when sshd does. The
  per-socket owner is therefore a genuine tri-state: a named process, unknown,
  or a process that has already exited.

Both tables are needed and either may be absent: a kernel with IPv6 disabled has
no `/proc/net/tcp6` at all, and a kernel with IPv6 but no `tcp6` module loaded
has a missing one. `/proc/net/tcp6` alone being unreadable is reported as a
caveat on IPv6 coverage rather than as a total UNKNOWN, because the IPv4 table
was still read - but it is never silently dropped.
"""

from __future__ import annotations

import errno
import logging
import os
import socket
from pathlib import Path
from typing import Dict, List, Optional, Union

from shani_chronoa.config import ChronoaConfig
from shani_chronoa.senses import SENSITIVITY_PERSONAL, Sense
from shani_chronoa.senses.context import Percept
from shani_chronoa import sysfs

logger = logging.getLogger(__name__)

KIND = "machine-state"
# Open ports and the programs behind them say what this machine is running and,
# for a port bound to a loopback address, which services the user talks to.
# That is a fact about the machine, but not a fact about hardware.
SENSITIVITY = SENSITIVITY_PERSONAL
_TTL_SECONDS = 60.0
_POLL_INTERVAL = 60.0

_PROC = Path("/proc")
_NET_TCP = _PROC / "net/tcp"
_NET_TCP6 = _PROC / "net/tcp6"

#: `st` column value for TCP_LISTEN. A socket in any other state is a
#: connection, not a service, and listing every established connection would
#: turn "what is listening" into a log of who this machine is talking to.
_TCP_LISTEN = "0A"

#: A machine with more than this many listening sockets is not a machine whose
#: port list fits in a context percept, so the overflow is counted rather than
#: listed.
_MAX_LISTED = 40

#: Tri-state owners.
OWNER_NAMED = "named"
OWNER_UNKNOWN = "unknown"
OWNER_GONE = "no-live-process"


def _decode_ipv4(hex_address: str) -> str:
    """`0100007F` -> `127.0.0.1` (the field is little-endian per 32-bit word)."""
    if len(hex_address) != 8:
        return hex_address
    try:
        octets = [int(hex_address[i:i + 2], 16) for i in range(0, 8, 2)]
    except ValueError:
        return hex_address
    return ".".join(str(o) for o in reversed(octets))


def _decode_ipv6(hex_address: str) -> str:
    """The 32-hex-digit field, as a canonical IPv6 address.

    The kernel writes four 32-bit words, each in host byte order, so the bytes
    within each group of eight are reversed before the address is real. An
    IPv4-mapped address comes back as `::ffff:a.b.c.d` and is unwrapped, because
    printing the mapped form for a dual-stack listener is noise.
    """
    if len(hex_address) != 32:
        return hex_address
    raw = bytearray()
    for index in range(0, 32, 8):
        try:
            raw += bytes.fromhex(hex_address[index:index + 8])[::-1]
        except ValueError:
            return hex_address
    try:
        if bytes(raw[:12]) == b"\x00" * 10 + b"\xff\xff":
            return socket.inet_ntop(socket.AF_INET, bytes(raw[12:]))
        return socket.inet_ntop(socket.AF_INET6, bytes(raw))
    except (OSError, ValueError):
        return hex_address


def read_socket_table(path: Path, ipv6: bool = False) -> Optional[List[dict]]:
    """Every LISTEN socket in one `/proc/net/tcp*` table, or None if unreadable.

    None is a real answer and it is the important one: it means the kernel would
    not tell us what is listening, which is not the same as an empty table.
    """
    if not path.is_file():
        return None
    text = sysfs.read_raw(path)
    if text is None:
        return None

    rows: List[dict] = []
    for line in text.splitlines()[1:]:  # row 0 is the column header
        fields = line.split()
        # sl local rem st tx:rx tr:tm->when retrnsmt uid timeout inode ...
        if len(fields) < 10:
            continue
        if fields[3] != _TCP_LISTEN:
            continue
        local = fields[1].rpartition(":")
        if not local[0] or not local[2]:
            continue
        try:
            port = int(local[2], 16)
        except ValueError:
            continue
        inode = fields[9]
        if not inode.isdigit():
            continue
        address = _decode_ipv6(local[0]) if ipv6 else _decode_ipv4(local[0])
        rows.append({
            "address": address,
            "port": port,
            "inode": inode,
            "uid": fields[7] if fields[7].isdigit() else None,
        })
    return rows


def _process_name(pid: str) -> str:
    return (sysfs.read_raw(_PROC / pid / "comm") or "?").strip() or "?"


def map_socket_inodes(wanted: set) -> tuple:
    """Which PIDs hold which socket inodes, and how many could not be checked.

    Returns `(owners, unreadable_pids)`. `unreadable_pids` is the load-bearing
    half: a process whose `/proc/<pid>/fd` this account cannot read is not
    evidence of anything, and without its count a socket owned by another user
    would report as unowned - the `fuser` failure again, one level down.
    """
    owners: Dict[str, List[str]] = {}
    unreadable = 0
    if not wanted:
        return owners, unreadable

    try:
        pids = [entry.name for entry in os.scandir(_PROC) if entry.name.isdigit()]
    except OSError:
        return owners, len(wanted)

    for pid in pids:
        fd_dir = _PROC / pid / "fd"
        try:
            fds = list(os.scandir(fd_dir))
        except FileNotFoundError:
            continue  # exited between the scan and now; not an error
        except NotADirectoryError:
            continue
        except OSError as exc:
            if exc.errno in (errno.EACCES, errno.EPERM):
                unreadable += 1
            continue
        for entry in fds:
            try:
                target = os.readlink(entry.path)
            except OSError:
                continue
            if not target.startswith("socket:["):
                continue
            inode = target[8:-1]
            if inode in wanted:
                owners.setdefault(inode, []).append(pid)
    return owners, unreadable


def read_listeners() -> Optional[dict]:
    """The listening sockets, or None when neither socket table could be read.

    None must never be rendered as an empty list. The three fields of the
    returned dict are:

    - `sockets` - the listeners found, with an owner that is a name, `unknown`
      or `no-live-process`
    - `unreadable_processes` - how many PIDs' fds could not be inspected, which
      is what makes an `unknown` owner honest rather than a guess
    - `ipv6_read` - False when `/proc/net/tcp6` was missing, so incomplete IPv6
      coverage is stated rather than assumed
    """
    v4 = read_socket_table(_NET_TCP, ipv6=False)
    v6 = read_socket_table(_NET_TCP6, ipv6=True)
    if v4 is None and v6 is None:
        return None

    sockets = list(v4 or []) + list(v6 or [])
    owners, unreadable = map_socket_inodes({s["inode"] for s in sockets})
    for record in sockets:
        pids = owners.get(record["inode"], [])
        if pids:
            record["owner"] = OWNER_NAMED
            record["process"] = ", ".join(
                f"{_process_name(pid)} (pid {pid})" for pid in sorted(pids, key=int)
            )
        elif unreadable:
            # Not "unowned". Something this account cannot see may hold it.
            record["owner"] = OWNER_UNKNOWN
            record["process"] = None
        else:
            # Every process on the machine was inspectable and none holds it.
            record["owner"] = OWNER_GONE
            record["process"] = None
    sockets.sort(key=lambda record: (record["port"], record["address"]))
    return {
        "sockets": sockets,
        "unreadable_processes": unreadable,
        "ipv6_read": v6 is not None,
    }


def _run(arguments: dict) -> Union[str, Percept]:
    config = ChronoaConfig()
    if not config.sense_allowed("listeners"):
        return f"Not enumerating listening ports: {config.sense_allowed_reason('listeners')}."

    result = read_listeners()
    if result is None:
        return (
            "Listening ports are UNKNOWN - cannot enumerate sockets. Neither "
            "/proc/net/tcp nor /proc/net/tcp6 could be read, so this is NOT a "
            "report that nothing is listening. A missing /proc, a locked-down "
            "one, or a kernel with no proc networking at all all look like this, "
            "and a real sshd on port 22 would be invisible in that answer."
        )

    sockets = result["sockets"]
    if not sockets:
        return (
            "Nothing is listening. Both socket tables were read successfully "
            "and neither holds a socket in the LISTEN state, so this is a "
            "positive finding about this machine rather than an empty scan."
        )

    lines = [f"{len(sockets)} socket(s) in the LISTEN state:"]
    for record in sockets[:_MAX_LISTED]:
        lines.append(
            f"  {record['address']}:{record['port']} - "
            + (record["process"] or f"owner {record['owner']}")
        )
    if len(sockets) > _MAX_LISTED:
        lines.append(f"  and {len(sockets) - _MAX_LISTED} more")

    unknown = sum(1 for s in sockets if s["owner"] == OWNER_UNKNOWN)
    if result["unreadable_processes"]:
        lines.append(
            f"  {result['unreadable_processes']} process(es) had an unreadable "
            f"/proc/<pid>/fd, so {unknown} port(s) are reported as 'owner "
            f"unknown' - another user's process, or a hidepid mount. That is "
            f"not a claim that nothing holds them."
        )
    if not result["ipv6_read"]:
        lines.append(
            "  IPv6 coverage is incomplete: /proc/net/tcp6 was not readable, so "
            "an IPv6-only listener would not appear above."
        )

    return _SENSE.to_percept(
        "\n".join(lines),
        source="proc-net-tcp",
        metadata={
            "enumerated": True,
            "listening_count": len(sockets),
            "owner_unknown_count": unknown,
            "unreadable_processes": result["unreadable_processes"],
            "ipv6_read": result["ipv6_read"],
        },
    )


_SCHEMA = {
    "type": "function",
    "function": {
        "name": "listeners",
        "description": (
            "List the TCP sockets in the LISTEN state, with the process holding "
            "each, read from /proc/net/tcp and /proc/net/tcp6 cross-referenced "
            "against /proc/<pid>/fd rather than from a helper binary. Reports "
            "UNKNOWN - never 'nothing is listening' - when the socket table "
            "cannot be read, and reports a port whose owning process belongs to "
            "another user as 'owner unknown' rather than as unowned."
        ),
        "parameters": {"type": "object", "properties": {}},
    },
}


_SENSE = Sense(
    name="listeners",
    kind=KIND,
    ttl_seconds=_TTL_SECONDS,
    sensitivity=SENSITIVITY,
    schema=_SCHEMA,
    run=_run,
    poll_interval=_POLL_INTERVAL,
)

SENSES = [_SENSE]
