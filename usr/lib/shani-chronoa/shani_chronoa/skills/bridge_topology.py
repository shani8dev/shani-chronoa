"""Skill: which interfaces are bridges, what is enslaved to them, and what they have learned.

"Is this veth actually plugged into the bridge it was supposed to be?" is the
question that decides half of all container and lab-network debugging, and it
has a very specific failure mode: everything *looks* correct — the plan ran, no
step reported an error, the interface exists and is up — and the traffic still
does not flow, because the interface is in the namespace and the bridge is not,
or is on the other side of something that never got created.

**This answers that from `/sys`, and runs no command at all.** The obvious
wiring would be `bridge link show` and `bridge fdb show`, both of which are on
the image and both of which are flagged by the capability matrix as unused. They
are avoided deliberately:

- `/sys/class/net/<if>/brif/` lists exactly the interfaces enslaved to a bridge,
  and `/sys/class/net/<if>/master` names the bridge an interface is attached to.
  Both are readable by the ordinary desktop user, both are written by the kernel
  rather than by a tool, and both cannot be half-applied.
- `bridge fdb` needs `CAP_NET_ADMIN` to show other ports' entries, so running
  it unprivileged prints an empty or partial forwarding database that reads
  exactly like "nothing has been learned" — the confident-empty-answer failure.
  `iptables`' own trap in a different shape.

**An empty member list is reported as empty, not as broken.** The podman bridges
on a real machine legitimately have no members until a container joins, so
"bridge with nothing plugged in" is a real and common state, and saying it is a
problem would be wrong.

**This reports the shape, not whether traffic flows.** Knowing `vh01h` is
enslaved to `br-lab` tells you the plumbing is right; it does not prove anything
can ping. That is `lab_network_status`'s job and it needs privileges this does
not ask for.

**One network namespace, stated.** Everything here comes from this namespace's
`/sys/class/net`. A lab network built by `lab_network_create` puts its bridge
inside a namespace of its own, so it does not appear here — and the skill says
which namespaces it can see rather than reporting an empty topology as if that
were the whole machine.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import List, Optional, Tuple

from shani_chronoa import sysfs
from shani_chronoa.skills import Skill

logger = logging.getLogger(__name__)

_NET = Path("/sys/class/net")

#: `lo` is not a bridge and reporting it as an unattached interface is noise.
_SKIP = {"lo"}

#: Enough to name a topology on a real machine. A host with 200 veths is not
#: better served by a longer list than by being told how many there are.
_MAX_ROWS = 60


def _read_int(path: Path) -> Optional[int]:
    text = sysfs.read_text(path)
    if text is None:
        return None
    try:
        return int(text.strip())
    except ValueError:
        return None


def _members(name: str) -> Tuple[List[str], Optional[str]]:
    """The interfaces enslaved to `name`, or None when that cannot be read."""
    entry = _NET / name
    if not entry.is_dir():
        return [], f"{name} is not a network interface here"
    brif = entry / "brif"
    if not brif.is_dir():
        return [], None  # not a bridge, which is a fact rather than a failure
    try:
        return sorted(p.name for p in brif.iterdir()), None
    except OSError as exc:
        return [], f"{name}: its bridge members could not be listed ({exc})"


def _master(name: str) -> Optional[str]:
    """The bridge `name` is enslaved to, if any.

    **`/sys/class/net/<if>/master` is a symlink to a DIRECTORY**, not a file -
    so reading it as text returns nothing at all, on every enslaved interface,
    every time. That is not a permission error and not a kernel that declined;
    the path simply is not a file, and a read-based check reports every enslaved
    interface as standalone while the bridge's own `brif/` listing - read from
    the other direction - still shows it as a member. The two halves of the same
    fact disagreeing is what made the first version of this report a veth as both
    a bridge member and standalone, with "0 enslaved" contradicting the list
    directly above it. Resolved from the link target instead.
    """
    link = _NET / name / "master"
    try:
        if not link.exists():
            return None
        return link.resolve(strict=True).name or None
    except OSError:
        return None


def _stale_members(name: str, members: List[str]) -> List[str]:
    """Which of `members` no longer exist as interfaces.

    `brif/` can list a port that is gone - the veth of a container that has just
    exited is the ordinary case. Counting one of those as a member would report
    a bridge with a port that does not exist, which is the confident-wrong-answer
    shape; so it is separated out and named.
    """
    return [m for m in members if not (_NET / m).exists()]


def _forward_delay(name: str) -> Optional[int]:
    value = _read_int(_NET / name / "bridge" / "forward_delay")
    return None if value is None else value // 1000


def _run(arguments: dict) -> str:
    wanted = arguments.get("bridge")
    if wanted is not None and not isinstance(wanted, str):
        return f"'bridge' must be a bridge name, not {wanted!r}."

    if not _NET.is_dir():
        return (
            f"L2 topology is UNKNOWN: {_NET} does not exist or is not readable, "
            f"so no bridge or interface membership could be checked. That is not "
            f"the same as reporting that no bridges exist."
        )

    try:
        names = sorted(p.name for p in _NET.iterdir() if p.name not in _SKIP)
    except OSError as exc:
        return (f"L2 topology is UNKNOWN: {_NET} could not be listed "
                f"({exc.strerror or exc}). No bridge or interface membership "
                f"is reported, which is not the same as reporting none.")

    if wanted and wanted not in names:
        return (f"No interface called {wanted!r} in this network namespace. "
                f"Visible here: {', '.join(names[:12])}"
                f"{', ...' if len(names) > 12 else ''}. A bridge inside a "
                f"network namespace - one built by lab_network_create - is not "
                f"in this namespace's view.")

    problems: List[str] = []
    bridges: List[Tuple[str, List[str]]] = []
    enslaved: List[Tuple[str, str]] = []
    loose: List[str] = []

    for name in names:
        if wanted and name != wanted:
            # Still record who is enslaved to the one bridge that was asked for.
            owner = _master(name)
            if owner == wanted:
                enslaved.append((name, owner))
            continue
        owner = _master(name)
        if owner:
            enslaved.append((name, owner))
            continue
        members, problem = _members(name)
        if problem:
            problems.append(problem)
        elif members:
            bridges.append((name, members))
        elif not problem and _NET.joinpath(name, "bridge").is_dir():
            # A bridge with nothing in it. Real and common, so it is stated as
            # its own case rather than as an absence.
            bridges.append((name, []))

    # Only interfaces that are neither a bridge nor enslaved are "loose". That
    # is the useful signal: a veth the user expected to be on a bridge and is
    # not, which is the failure this whole skill exists to surface.
    #
    # Both directions of the same fact are used, deliberately. `enslaved` holds
    # (interface, its bridge) pairs, so an interface is enslaved when it is the
    # LEFT of a pair - checking the right, as an earlier version did, tests
    # whether a bridge has members and therefore classified every enslaved veth
    # as standalone while the bridge's own list printed it a line above. Two
    # halves of one fact contradicting each other on the same screen is worse
    # than either being absent, so both are cross-checked here and any mismatch
    # is reported rather than smoothed over.
    bridge_names = {name for name, _ in bridges}
    enslaved_names = {iface for iface, _ in enslaved}
    listed_members = {m for _, members in bridges for m in members
                      if (_NET / m).exists()}
    for name in names:
        if wanted and name != wanted:
            continue
        if name in bridge_names or name in enslaved_names:
            continue
        if _NET.joinpath(name, "bridge").is_dir():
            continue
        loose.append(name)

    mismatched = sorted(enslaved_names ^ listed_members)

    out = [
        "Bridge and interface membership, read from /sys/class/net. This is the "
        "plumbing - it says what is plugged into what, not whether traffic "
        "flows."
    ]
    out.append("")

    if wanted:
        out.append(f"{wanted}:")
        shown = dict(bridges).get(wanted)
        if shown is None:
            out.append("  not a bridge, or not present in this namespace")
        elif not shown:
            out.append("  a bridge with nothing enslaved to it - real and "
                       "common before anything joins it")
        else:
            for member in shown:
                out.append(f"  has {member}")
        attached = [n for n, o in enslaved if o == wanted]
        if attached:
            out.append(f"  {len(attached)} interface(s) report it as their "
                       f"bridge: {', '.join(attached[:10])}")
        return "\n".join(out)

    if bridges:
        out.append("Bridges and what is enslaved to them:")
        stale_total = 0
        for name, members in sorted(bridges):
            stale = _stale_members(name, members)
            live = [m for m in members if m not in stale]
            stale_total += len(stale)
            if live:
                out.append(f"  {name}: {', '.join(live)}")
            else:
                out.append(f"  {name}: nothing enslaved to it")
            if stale:
                out.append(f"    (bridge still lists {', '.join(stale)}, which "
                           f"no longer exists as an interface - usually a "
                           f"container that has just exited)")
        if stale_total:
            out.append("")
        out.append("")

    if loose:
        shown_loose = loose[:_MAX_ROWS]
        out.append(f"{len(loose)} interface(s) are not bridges and not enslaved to "
                   f"one: {', '.join(shown_loose)}")
        if len(loose) > len(shown_loose):
            out.append(f"  ... {len(loose) - len(shown_loose)} more not shown.")
        out.append("  A physical port is expected here. A veth that you expected "
                   "to be on a bridge, and is not, is the usual cause of a "
                   "network that built without error and carries nothing.")
        out.append("")

    out.append(f"{len(bridges)} bridge(es), {len(enslaved)} enslaved interface(s), "
               f"{len(loose)} standalone.")

    if not bridges and not loose:
        out.append("Nothing at all was readable, which is not a normal machine - "
                   "check /sys/class/net.")

    if mismatched:
        out.append("")
        out.append(
            f"WARNING: {len(mismatched)} interface(s) disagree between the two "
            f"directions of the check - a bridge's member list and the "
            f"interface's own master pointer do not match for "
            f"{', '.join(mismatched[:8])}. That is what a half-removed veth "
            f"looks like, and it is worth looking at before trusting either "
            f"list."
        )
    if problems:
        out.append("")
        out.append("Caveats: " + "; ".join(sorted(set(problems))[:4]))

    out.append("")
    out.append("This is this network namespace's view. A bridge inside a "
               "namespace built by lab_network_create does not appear here.")
    return "\n".join(out)


SCHEMA = {
    "type": "function",
    "function": {
        "name": "bridge_topology",
        "description": (
            "Show which network interfaces are bridges, exactly which interfaces "
            "are enslaved to each one, and which are standalone. Answers 'is this "
            "interface actually plugged into the bridge it should be', which is "
            "the usual reason a network that reported no errors carries no "
            "traffic. Read from /sys so it needs no privileges and cannot be "
            "half-applied. Reports the plumbing, not whether traffic flows. "
            "Covers this network namespace only."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "bridge": {
                    "type": "string",
                    "description": ("Only this bridge, e.g. 'docker0'. Omit to "
                                    "show every bridge and standalone interface."),
                },
            },
        },
    },
}


SKILLS = [Skill(name="bridge_topology", schema=SCHEMA, run=_run)]