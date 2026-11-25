"""Skill: which program is using a port, and what this machine is connected to.

"What's on port 3000?" is the question a developer asks most often about their
own machine, and nothing in this package could answer it. `list_processes`
lists processes and `list_services` lists units; neither has a port, so the
link between "a thing is running" and "it is holding that port" had to be made
by the user from two lists they have to correlate themselves.

**Read from `/proc`, not by running `ss` or `lsof`.** The capability matrix flags
both as unused read-only commands and both are on the image today, so calling
one would have been the obvious wiring. `/proc/net/tcp` is the same data the
kernel already publishes, it needs no binary that a minimal image might lack,
and it does something the binaries cannot do for an unprivileged user:
`ss -p` and `lsof` both need permission to read the target process's `fd`
directory, so a socket belonging to root or to another user comes back
unattributed. Here the inode in `/proc/net/tcp` is matched against
`/proc/<pid>/fd/*` directly, and what cannot be attributed is reported as
unattributed instead of being quietly dropped.

Honesty rules:

- **This is one network namespace.** `/proc/net/tcp` shows the namespace's
  sockets, so a process in a container or a VM is not in here. A reply that
  says a port is free is scoped to this namespace and says so.
- **A socket with no visible owner is still listed.** Another user's process,
  or a kernel one, has an inode here and no `fd` we may read. Dropping those
  would make "nothing is on this port" true for the rows we could attribute and
  false for the machine.
- **A state this module does not know is shown as its raw hex**, never guessed
  into a friendlier wrong name.
- **An unreadable `/proc/net/tcp` is reported as unreadable**, not as "no ports
  in use" - the failure mode that matters most here is the confident empty
  answer.
"""

from __future__ import annotations

import logging
import os
import re
from pathlib import Path
from typing import Dict, List, NamedTuple, Optional, Tuple

from shani_chronoa.skills import Skill

logger = logging.getLogger(__name__)

_PROC = Path("/proc")

#: `st` from /proc/net/*, which is a 2-digit hex TCP state number.
_STATES = {
    1: "ESTABLISHED", 2: "SYN_SENT", 3: "SYN_RECV", 4: "FIN_WAIT1",
    5: "FIN_WAIT2", 6: "TIME_WAIT", 7: "CLOSE", 8: "CLOSE_WAIT",
    9: "LAST_ACK", 10: "LISTEN", 11: "CLOSING", 12: "NEW_SYN_RECV",
}

_KINDS = (("tcp6", "tcp"), ("udp6", "udp"), ("tcp", "tcp"), ("udp", "udp"))

#: Enough to answer "is my service up" without a wall of kernel bookkeeping.
_MAX_ROWS = 60

#: `/proc/net/tcp` uses hex; `0100007F` is 127.0.0.1 read as four little-endian
#: bytes. Unspecified is reported as such rather than as `0.0.0.0`, which is a
#: routable-looking address that names no interface.
_UNSPECIFIED = ("00000000", "0" * 32, "00000000000000000000000000000000")


class Socket(NamedTuple):
    kind: str
    local: str
    port: int
    peer: str
    peer_port: int
    state: str
    uid: int
    inode: int


class Owner(NamedTuple):
    pid: int
    name: str
    command: str


SCHEMA = {
    "type": "function",
    "function": {
        "name": "port_owner",
        "description": (
            "Find which program is using a network port, or list what this "
            "machine is listening on and connected to. Read-only."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "port": {
                    "type": "integer",
                    "description": "The port to look up, e.g. 3000. Omit to list everything.",
                },
                "process": {
                    "type": "string",
                    "description": "Only sockets owned by a process whose name or command matches this.",
                },
                "kind": {
                    "type": "string",
                    "description": "One of: tcp, udp, any. Defaults to any.",
                },
                "listening": {
                    "type": "boolean",
                    "description": "Only listening sockets. Use when asking what is accepting connections.",
                },
                "limit": {
                    "type": "integer",
                    "description": f"Maximum rows to list (default and ceiling: {_MAX_ROWS}).",
                },
            },
        },
    },
}


def _decode_address(raw: str, ipv6: bool) -> str:
    """`0100007F` -> `127.0.0.1`; a 32-hex IPv6 word quartet -> its address."""
    try:
        if ipv6:
            if len(raw) != 32:
                return f"0x{raw}"
            # Four 32-bit words, each little-endian, each already reversed.
            words = [raw[i:i + 8] for i in range(0, 32, 8)]
            data = b"".join(
                bytes.fromhex(word)[::-1] for word in words
            )
            packed = data.hex()
            if packed in _UNSPECIFIED:
                return "unspecified"
            # Compress the longest run of zero groups, as an address is written.
            try:
                import ipaddress
                return str(ipaddress.IPv6Address(data))
            except ValueError:
                return f"0x{packed}"
        if len(raw) != 8:
            return f"0x{raw}"
        data = bytes.fromhex(raw)[::-1]
        dotted = ".".join(str(byte) for byte in data)
        if dotted in ("0.0.0.0",):
            return "unspecified"
        return dotted
    except ValueError:
        return f"0x{raw}"


def _read_table(name: str, ipv6: bool, kind: str) -> Tuple[List[Socket], Optional[str]]:
    """Parse one `/proc/net/*` file. Returns `(sockets, problem)`."""
    path = _PROC / "net" / name
    try:
        text = path.read_text()
    except FileNotFoundError:
        return [], f"{path} is not present in this kernel's procfs."
    except PermissionError:
        return [], f"Not permitted to read {path}."
    except OSError as exc:
        return [], f"Could not read {path}: {exc}"

    rows: List[Socket] = []
    # The first line is the column header; every later line is one socket.
    for raw in text.splitlines()[1:]:
        parts = raw.split()
        # `sl` carries a trailing colon and its own line number, so the field
        # count is the only reliable length check.
        if len(parts) < 10:
            continue
        local, _, local_port = parts[1].rpartition(":")
        peer, _, peer_port = parts[2].rpartition(":")
        try:
            row = Socket(
                kind=kind,
                local=_decode_address(local, ipv6),
                port=int(local_port, 16),
                peer=_decode_address(peer, ipv6),
                peer_port=int(peer_port, 16),
                state=_state(parts[3], kind),
                uid=int(parts[7]),
                inode=int(parts[9]),
            )
        except ValueError:
            # A row we cannot parse is skipped rather than guessed at; the count
            # of rows read is still what the reply is based on.
            continue
        rows.append(row)
    return rows, None


def _state(raw: str, kind: str) -> str:
    """Decode `st`, keeping anything unknown visible instead of inventing it."""
    try:
        number = int(raw, 16)
    except ValueError:
        return f"0x{raw}"
    if kind == "udp":
        # UDP's st is 1 for established (connected) and 7 for unconnected, which
        # are not TCP states at all; naming them LISTEN/ESTABLISHED would be a
        # confident wrong answer.
        return "CONNECTED" if number == 1 else ("UNCONNECTED" if number == 7 else f"0x{raw}")
    return _STATES.get(number, f"0x{raw}")


def _read_all() -> Tuple[List[Socket], List[str]]:
    sockets: List[Socket] = []
    problems: List[str] = []
    for name, kind in _KINDS:
        rows, problem = _read_table(name, name.endswith("6"), kind)
        if problem:
            problems.append(problem)
            continue
        sockets.extend(rows)
    return sockets, problems


def _describe(pid: str) -> Owner:
    """Name and command for a pid, degrading honestly.

    `comm` is truncated to 15 characters by the kernel and a process may have
    changed its name since it started, so the command line is what carries the
    detail. A process that exited between the socket table being read and this
    call simply has no owner recorded - which is why every read here is
    individually guarded rather than wrapped in one try.
    """
    name, command = "?", ""
    try:
        name = (_PROC / pid / "comm").read_text().strip() or "?"
    except OSError:
        pass
    try:
        raw = (_PROC / pid / "cmdline").read_bytes()
        command = raw.replace(b"\0", b" ").decode("utf-8", "replace").strip()
    except OSError:
        pass
    return Owner(int(pid), name, command or f"[{name}]")


def _attributes(sockets: List[Socket]) -> Dict[int, Owner]:
    """Map socket inode -> owner, by one walk of `/proc/<pid>/fd`.

    An inode that is in `/proc/net/*` but appears in no readable `fd` is
    another user's socket or a kernel one, and is left out of the mapping on
    purpose: the caller reports those as "owner not visible" rather than
    dropping them. `readlink` on another user's fd raises `PermissionError`,
    which is the expected case here and not an error worth logging.
    """
    wanted = {row.inode for row in sockets if row.inode}
    owners: Dict[int, Owner] = {}
    if not wanted:
        return owners
    try:
        pids = os.listdir(_PROC)
    except OSError as exc:
        logger.warning("could not list /proc: %s", exc)
        return owners
    for pid in pids:
        if not pid.isdigit():
            continue
        fd_dir = _PROC / pid / "fd"
        try:
            handles = os.listdir(fd_dir)
        except OSError:
            continue
        for handle in handles:
            try:
                link = os.readlink(fd_dir / handle)
            except OSError:
                continue
            if not link.startswith("socket:["):
                continue
            try:
                inode = int(link[8:-1])
            except ValueError:
                continue
            if inode in wanted and inode not in owners:
                owners[inode] = _describe(pid)
    return owners


#: A Chromium renderer or a Java agent's command line runs to several thousand
#: characters. One browser's utility process holds a dozen sockets, so printing
#: them in full turns a one-line answer into a page of repeated flags - and the
#: part that identifies the program is at the front.
_MAX_COMMAND_CHARS = 140


def _short_command(command: str) -> str:
    if len(command) <= _MAX_COMMAND_CHARS:
        return command
    return command[:_MAX_COMMAND_CHARS] + f"... (+{len(command) - _MAX_COMMAND_CHARS} more characters)"


def _row_text(row: Socket, owner: Optional[Owner]) -> str:
    if row.state == "LISTEN":
        where = f"listening on {row.local}:{row.port}"
    elif row.kind == "udp":
        peer = "unconnected" if row.peer == "unspecified" else f"peer {row.peer}:{row.peer_port}"
        where = f"udp {row.local}:{row.port} ({row.state}, {peer})"
    else:
        where = f"{row.local}:{row.port} -> {row.peer}:{row.peer_port} ({row.state})"
    if owner is None:
        # inode 0 is not "somebody else's": a socket in TIME_WAIT, CLOSE_WAIT
        # or LAST_ACK has no owning process *at all* - the kernel is holding the
        # dead connection until the timer expires - and saying "another user"
        # would send the reader looking for a process that never existed.
        tail = ("no process: the kernel is holding this connection"
                if row.inode == 0 else
                "owner not visible (another user's process, or a kernel socket)")
        return f"  {row.port:>5}/{row.kind}  {where} - {tail}"
    return (f"  {row.port:>5}/{row.kind}  {where} - pid {owner.pid} "
            f"{owner.name}: {_short_command(owner.command)}")


def _port(value: object) -> Tuple[Optional[int], Optional[str]]:
    """Validate a caller-supplied port. Returns `(port, problem)`."""
    if value is None:
        return None, None
    if isinstance(value, bool) or not isinstance(value, (int, str)):
        return None, f"A port must be a number, not {value!r}."
    text = str(value).strip()
    if not text.lstrip("-").isdigit():
        return None, f"{value!r} is not a port number."
    number = int(text)
    if not 1 <= number <= 65535:
        return None, f"{number} is not a port number: ports run from 1 to 65535."
    return number, None


def _run(arguments: dict) -> str:
    port, problem = _port(arguments.get("port"))
    if problem:
        return problem

    kind = (arguments.get("kind") or "any").strip().lower()
    if kind not in ("tcp", "udp", "any"):
        return f"Kind must be tcp, udp or any, not {kind!r}."

    listening = arguments.get("listening")
    if listening is not None and not isinstance(listening, bool):
        return f"'listening' must be true or false, not {listening!r}."

    limit = arguments.get("limit")
    if limit is None:
        limit = _MAX_ROWS
    elif isinstance(limit, bool) or not isinstance(limit, int) or limit < 1:
        return f"'limit' must be a positive whole number, not {limit!r}."
    limit = min(limit, _MAX_ROWS)

    sockets, problems = _read_all()
    if not sockets and problems:
        return (
            "Could not read this machine's sockets: "
            + "; ".join(problems)
            + " Nothing is reported, which is not the same as nothing listening."
        )

    if kind != "any":
        sockets = [row for row in sockets if row.kind == kind]
    if listening:
        sockets = [row for row in sockets if row.state == "LISTEN"]

    needle = (arguments.get("process") or "").strip()
    owners = _attributes(sockets)
    if needle:
        pattern = re.compile(re.escape(needle), re.IGNORECASE)
        keep = []
        for row in sockets:
            owner = owners.get(row.inode)
            if owner and (pattern.search(owner.name) or pattern.search(owner.command)):
                keep.append(row)
        sockets = keep

    if port is not None:
        sockets = [row for row in sockets if row.port == port or row.peer_port == port]

    if not sockets:
        wanted = f"port {port}" if port is not None else "any matching socket"
        return (
            f"Nothing found for {wanted} in this network namespace. That covers "
            f"only this namespace's sockets - a process in a container or a VM "
            f"would not appear here."
            + (" The tables read cleanly, so this is a real absence rather than "
               "a failed read." if not problems else
               " Some tables could not be read (" + "; ".join(problems) +
               "), so this is not a clean absence.")
        )

    # Listening sockets first, then by port: the reason someone asked is
    # usually at the top, not wherever the kernel happened to list it.
    sockets.sort(key=lambda row: (row.state != "LISTEN", row.port, row.kind))
    shown, withheld = _cap(sockets, limit)

    header = (f"{len(sockets)} socket(s) in this network namespace"
              + (f", port {port}" if port is not None else "")
              + (f", kind {kind}" if kind != "any" else "")
              + (", listening only" if listening else ""))
    body = "\n".join(_row_text(row, owners.get(row.inode)) for row in shown)
    if withheld:
        body += f"\n... and {withheld} more not shown (limit {limit})."
    note = ""
    if problems:
        note = "\nSome tables could not be read: " + "; ".join(problems)
    if port is not None and not any(
        row.port == port and row.state == "LISTEN" for row in sockets
    ):
        note += (
            f"\nNothing is LISTENING on port {port}"
            + ("; the rows above involve it as a peer." if sockets else "")
        )
    return f"{header}\n{body}{note}"


def _cap(rows: List[Socket], limit: int) -> Tuple[List[Socket], int]:
    if len(rows) <= limit:
        return rows, 0
    return rows[:limit], len(rows) - limit


SKILLS = [Skill(name="port_owner", schema=SCHEMA, run=_run)]