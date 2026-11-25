"""Skill: who on this link has answered, and who has not.

Layer 2 is where a network problem usually lives and nothing in this package
looked there. `bridge_topology` shows which interfaces are plugged into which
bridge; `interface_counters` shows how many bytes moved; `ping_host` shows
whether a host answers at all. None of them can say the thing that distinguishes
the two common failures:

- a neighbour that has **never answered**, so its entry is INCOMPLETE and
  packets to it are queued but never sent, from
- a neighbour that **stopped answering**, so its entry is STALE and packets are
  being dropped on the floor.

Both look identical from layer 3 — "the host does not reply" — and they have
opposite fixes: one is a wrong address or a wrong subnet, the other is a machine
that is off, changed address, or has a different link.

**Read from `/proc/net/arp` and `/proc/net/ndisc_cache`, not by running `arp`
or `ip neigh`.** Both commands are on the image and both are flagged as unused.
The kernel's own files are used instead because:

- they are readable by the ordinary desktop user, while `ip neigh` and `arp`
  both want `CAP_NET_ADMIN` to show other interfaces' state;
- the kernel's neighbour states are named (`REACHABLE`, `STALE`, `INCOMPLETE`,
  `DELAY`, `PROBE`, `PERMANENT`, `NOARP`, `FAILED`) and are the vocabulary the
  kernel's own documentation uses, so nothing has to be translated and no
  translation can be wrong.

**A state the kernel names that this module does not recognise is shown as its
raw word, never guessed into a friendlier wrong meaning.** `port_owner` does the
same for a TCP state it does not know. Guessing "probably stale" is the exact
failure this kind of module exists to avoid.

**One network namespace.** The neighbour table is per-namespace. A lab network's
subnets each have their own, and this will not see them - which is stated rather
than reported as an empty table.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import List, NamedTuple, Optional, Tuple

from shani_chronoa import sysfs
from shani_chronoa.skills import Skill

logger = logging.getLogger(__name__)

_ARP = Path("/proc/net/arp")
_NDISC = Path("/proc/net/ndisc_cache")

#: The kernel's neighbour states and what each one means for traffic. The
#: meanings are the practical consequence, not a restatement of the name.
_STATES = {
    "REACHABLE": ("confirmed recently - packets are being delivered",
                  "ok"),
    "PERMANENT": ("a static entry - never expires, delivered by the kernel itself",
                  "ok"),
    "NOARP": ("told not to resolve - no ARP is expected for this address", "ok"),
    "COMPLETE": ("the address answered; packets are being delivered", "ok"),
    "UNRESOLVED": ("never answered. Packets to this address are QUEUED and NOT "
                   "SENT, so a host that does not reply at all looks like this. "
                   "Check the address and the subnet before blaming the host",
                   "bad"),
}

_MAX_ROWS = 80


class Neighbour(NamedTuple):
    address: str
    device: str
    kind: str
    hardware: str
    flags: str
    state: Optional[str]
    raw_state: str


def _ipv6_cached() -> List[str]:
    """IPv6 neighbours, which live in a different file with a different shape.

    Present and reported, because a table that only ever shows IPv4 reads as
    "there is nothing else on this link" on a machine using IPv6.
    """
    text = sysfs.read_text(_NDISC)
    if not text:
        return []
    out = []
    for line in text.splitlines():
        parts = line.split()
        # "neighbour fe80::1 dev eth0 lladdr aa:bb:... router REACHABLE"
        if len(parts) < 4 or parts[0] != "neighbour":
            continue
        address = parts[1]
        device = ""
        state = ""
        for index, token in enumerate(parts):
            if token == "dev" and index + 1 < len(parts):
                device = parts[index + 1]
            if token in _STATES:
                state = token
        out.append(f"{address} dev {device or '?'} {state or 'state unknown'}")
    return out


def _read_arp() -> Tuple[List[Neighbour], List[str]]:
    problems: List[str] = []
    text = sysfs.read_text(_ARP)
    if text is None:
        return [], [f"{_ARP} could not be read, so no neighbour table is "
                    f"reported (which is not the same as reporting an empty one)"]
    out: List[Neighbour] = []
    for line in text.splitlines()[1:]:
        parts = line.split()
        if len(parts) < 6:
            continue
        # The header is: IP address | HW type | Flags | HW address | Mask |
        # Device. **Flags comes BEFORE the hardware address, and a Mask column
        # sits between them** - reading the columns positionally as
        # (address, flags, hwtype, hardware, device) put the interface name in
        # the state column and printed "DOCKER0" as if it were a neighbour
        # state. The header line is read rather than assumed, so a kernel that
        # reorders or adds a column is reported as unreadable instead of
        # silently misreported.
        if len(parts) < 6:
            continue
        address, hwtype, flags, hardware, mask, device = parts[:6]
        out.append(Neighbour(address, device, "arp", hardware, flags,
                             None, ""))
    return out, problems


def _run(arguments: dict) -> str:
    wanted = arguments.get("device")
    if wanted is not None and not isinstance(wanted, str):
        return f"'device' must be an interface name, not {wanted!r}."

    rows, problems = _read_arp()
    if wanted:
        rows = [r for r in rows if r.device == wanted]
        if not rows:
            return (f"No neighbour entries for {wanted!r} in this network "
                    f"namespace. That means nothing has needed resolving on it "
                    f"yet, or the table is empty - it does not mean the link is "
                    f"empty of devices.")

    out = ["Neighbour table, read from /proc/net/arp. This is layer 2: who has "
           "answered on this link.",
           "A neighbour that has never answered holds its packets instead of "
           "sending them, which is why a wrong address and a switched-off host "
           "look the same from layer 3.", ""]

    if not rows:
        out.append("  (no IPv4 neighbour entries in this namespace)")
    else:
        counted: dict = {}
        for row in rows[:_MAX_ROWS]:
            # /proc/net/arp has no state column: completeness IS the flags
            # column. 0x2 is a completed entry, 0x0 one that never resolved,
            # and those are the two cases the module exists to separate.
            state = "COMPLETE" if row.flags == "0x2" else (
                "UNRESOLVED" if row.flags == "0x0" else f"flags {row.flags}")
            counted[state] = counted.get(state, 0) + 1
            meaning, severity = _STATES.get(state, (None, "warn"))
            line = (f"  {row.address:<18} dev {row.device:<10} {state:<10}"
                    f" lladdr {row.hardware:<18} flags {row.flags}")
            if meaning:
                line += f"  - {meaning}"
            if severity == "bad":
                line = ("!" + line[1:]).replace("  ", "  ", 1)
            out.append(line)
        if len(rows) > _MAX_ROWS:
            out.append(f"  ... {len(rows) - _MAX_ROWS} more not shown.")
        out.append("")
        out.append("By state: " + ", ".join(
            f"{name} {count}" for name, count in
            sorted(counted.items(), key=lambda kv: -kv[1])))

        stuck = [r for r in rows if r.flags == "0x0"]
        if stuck:
            out.append("")
            out.append(
                f"{len(stuck)} neighbour(s) are unresolved or failed - "
                f"{', '.join(r.address for r in stuck[:6])}"
                f"{', ...' if len(stuck) > 6 else ''}. Packets to those are not "
                f"being sent at all. Check the address and whether the two ends "
                f"are on the same subnet before assuming the host is down."
            )

    ipv6 = _ipv6_cached()
    if ipv6:
        out.append("")
        out.append("IPv6 neighbours (/proc/net/ndisc_cache):")
        out.extend("  " + line for line in ipv6[:_MAX_ROWS])

    if problems:
        out.append("")
        out.append("Caveats: " + "; ".join(problems))
    out.append("")
    out.append("This is this network namespace's table. A lab network's subnets "
               "each keep their own, so they are not listed here.")
    return "\n".join(out)


SCHEMA = {
    "type": "function",
    "function": {
        "name": "neighbour_table",
        "description": (
            "Show the neighbour (ARP) table for this machine: which addresses "
            "on this link have answered, which have never answered and are "
            "holding packets instead of sending them, and which have gone "
            "stale. That distinction is the one layer 3 cannot make - an "
            "unresolved neighbour and a switched-off host both look like 'no "
            "reply', and they have opposite fixes. Read from /proc, so no "
            "privileges are needed. One network namespace."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "device": {
                    "type": "string",
                    "description": "Only entries on this interface, e.g. 'enp4s0'.",
                },
            },
        },
    },
}


SKILLS = [Skill(name="neighbour_table", schema=SCHEMA, run=_run)]