"""Sense: are there lab networks on this machine, and are they actually there?

The lab-network builder (`netprovision.py`, reached through the `network_*` skills)
creates isolated networks out of network namespaces, a bridge, veth pairs and
one nftables masquerade rule. This sense reports **what is currently on the
machine**, so a turn can answer "did that network get built, and is it still
there" without being trusted to the builder's own record.

**Why this is not the `network` sense wearing a second name.** `network` walks
`/sys/class/net` and reports every interface on this machine — up, carrier,
speed, DNS. That is the right question for WiFi and Ethernet. This one answers a
different question: *which of the isolated networks the builder made still
exist, and does each recorded subnet still have its namespace.* An orphaned
namespace after a half-finished create is a real condition with a real cleanup,
and nothing else reports it: it is absent from `network`'s interface list
because a namespace's interfaces do not appear in the root namespace's
`/sys/class/net` at all.

**What it reads, and what each read can be when it fails.**

- **The builder's record** (`~/.local/share/shani-chronoa/network-records.json`,
  resolved through `netprovision.state_dir()`): which networks this tool claims
  to have built. Unreadable is **UNKNOWN**, never "none".
- **`/var/run/netns/`**: the namespaces the kernel actually has. This is the
  part that can contradict the record, and the contradiction is the point — a
  network whose namespaces are all gone is a network that is recorded but not
  real, and reporting it as healthy would be the confident-wrong-answer failure.
  A missing directory is **UNKNOWN** rather than "no namespaces", because on a
  system without persistent netns that is a fact about the read.

**It never needs root, and never pings.** Everything here is a directory
listing and a JSON file, both readable by the desktop user. The reachability
half — whether a subnet can actually reach anything — needs `ip netns exec`, and
that is `lab_network_status`'s job through the privileged helper, not this sense's.
Reporting a number the sense did not measure is exactly what this module
exists to avoid.

**Nothing here is actuation.** No network is created, changed or removed; the
builder's own helper does that, behind its own consent key and the machine
password. This only looks, which is why it needs no key of its own beyond the
`machine-state` baseline every other read-only sense sits under.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import List, Optional, Union

from shani_chronoa.config import ChronoaConfig
from shani_chronoa.netprovision import load_record, record_path, state_dir
from shani_chronoa.senses import SENSITIVITY_PUBLIC, Sense
from shani_chronoa.senses.context import Percept
from shani_chronoa import sysfs

logger = logging.getLogger(__name__)

KIND = "machine-state"
SENSITIVITY = SENSITIVITY_PUBLIC
_TTL_SECONDS = 60.0
_POLL_INTERVAL = 60.0

#: Where the kernel's named network namespaces live. This is the one path that
#: is not resolved through a helper, because there is no helper for it: it is a
#: kernel interface location and `iproute2`'s own. It is absent on a machine
#: without persistent netns, which is UNKNOWN, not "empty".
_NETNS_DIR = Path("/var/run/netns")

_SCHEMA = {
    "type": "function",
    "function": {
        "name": "labnetworks",
        "description": (
            "Report the isolated lab networks this machine has: which ones the "
            "builder recorded, which of their namespaces actually exist in the "
            "kernel right now, and whether each subnet is intact. Read-only - "
            "it never creates, changes or removes a network, and it does not "
            "measure whether a subnet can reach anything (that needs privileges "
            "and is a separate question). Reports UNKNOWN - never 'none' - when "
            "the record or the namespace directory cannot be read."
        ),
        "parameters": {"type": "object", "properties": {}},
    },
}


def _namespaces_on_disk() -> Optional[List[str]]:
    """The kernel's named namespaces, or None when the directory is unreadable.

    None is a real answer and has to be distinguishable from an empty list: a
    machine with no `/var/run/netns` has not been shown to have no namespaces,
    it has been shown to keep them somewhere this cannot read.
    """
    if not _NETNS_DIR.is_dir():
        return None
    try:
        return sorted(p.name for p in _NETNS_DIR.iterdir())
    except OSError as exc:
        logger.debug("labnetworks: could not list %s: %s", _NETNS_DIR, exc)
        return None


def _record_state() -> Optional[dict]:
    """The builder's record, or None when it cannot be read.

    **This reads the file itself rather than calling `load_record()`, and that
    is the whole point.** `load_record()` returns `{}` for a missing file, a
    corrupt file, an unreadable one and a file holding the wrong JSON type
    alike — it catches `OSError` and `ValueError` and returns an empty dict. For
    a caller that only wants the networks, that is right. For a sense whose
    entire value is telling the truth about their state, it is fatal: it would
    make a corrupt record indistinguishable from an empty one, and "no lab
    networks exist" is a claim somebody will act on.

    So the three cases are kept apart here: a file that is not there means none
    recorded, a file that is there and parses means that many, and anything else
    is UNKNOWN.
    """
    path = record_path()
    if not path.exists():
        return {}
    try:
        raw = path.read_text(encoding="utf-8")
    except OSError as exc:
        logger.debug("labnetworks: could not read %s: %s", path, exc)
        return None
    if not raw.strip():
        return {}
    try:
        parsed = json.loads(raw)
    except ValueError:
        logger.debug("labnetworks: %s is not valid JSON", path)
        return None
    if not isinstance(parsed, dict):
        return None
    return parsed


def _describe(name: str, entry: dict, present: Optional[set]) -> List[str]:
    """One network, and whether the kernel still agrees it exists."""
    subnets = entry.get("subnets") or []
    cidr = entry.get("cidr", "?")
    nat = "on" if entry.get("nat") else "off"
    lines = [
        f"{name}  {cidr}  masquerade {nat}  {len(subnets)} subnet(s)"
        f"  recorded {entry.get('created', 'at an unrecorded time')}"
    ]
    if present is None:
        lines.append(
            "  whether its namespaces exist is UNKNOWN - the kernel's netns "
            f"directory ({_NETNS_DIR}) is not readable here"
        )
        return lines

    # The network's own namespace and one per subnet are what make it real.
    expected = {name} | {f"{name}--{s.get('name')}" for s in subnets}
    missing = sorted(n for n in expected if n not in present)
    if not missing:
        lines.append("  every namespace it needs is present")
    else:
        lines.append(
            f"  {len(missing)} of {len(expected)} namespaces are ABSENT "
            f"({', '.join(missing)}) - this network is recorded but not "
            f"actually on the machine, and destroy would clean up the rest"
        )

    for subnet in subnets:
        sub_name = subnet.get("name")
        kind = "public" if subnet.get("public") else "private"
        state = "present" if f"{name}--{sub_name}" in present else "MISSING"
        lines.append(
            f"    {sub_name} {subnet.get('cidr')} ({kind}) namespace {state}"
        )
    return lines


def _run(_config: Optional[ChronoaConfig] = None) -> Union[Percept, str]:
    record = _record_state()
    present_list = _namespaces_on_disk()
    present = None if present_list is None else set(present_list)

    if record is None and present is None:
        return _SENSE.to_percept(
            f"Lab network state is UNKNOWN: the builder's record under "
            f"{state_dir()} could not be read AND the kernel's namespace "
            f"directory ({_NETNS_DIR}) is not readable here. Neither 'no lab "
            f"networks' nor 'some exist' is something this can say.",
            source="network-record+netns-dir",
            metadata={"record_known": False, "namespaces_known": False,
                      "networks": None},
        )

    if record is None:
        return _SENSE.to_percept(
            f"The lab-network record under {state_dir()} could not be read, so "
            f"which networks are meant to exist is UNKNOWN. The kernel's "
            f"namespace directory was readable"
            + (f" and holds {len(present)} namespace(s), but without the "
               f"record there is nothing to compare them against - an "
               f"unrecognised namespace is not the same as a lab network."
               if present else " but holds no namespaces.").rstrip(),
            source="network-record+netns-dir",
            metadata={"record_known": False, "namespaces_known": present is not None,
                      "networks": None},
        )

    if not record:
        extra = ""
        if present is not None:
            # Namespaces matching the builder's own naming, without a record.
            orphans = [n for n in sorted(present) if "--" in n or n.startswith("br-")]
            if orphans:
                extra = (
                    f" Note: {len(orphans)} namespace(s) look like lab-network "
                    f"leftovers with no record behind them "
                    f"({', '.join(orphans[:6])}"
                    f"{', ...' if len(orphans) > 6 else ''}). This tool will not "
                    f"remove a network it has no record of."
                )
        return _SENSE.to_percept(
            f"No lab networks are recorded. The builder's record at "
            f"{state_dir() / 'network-records.json'} holds none."
            + extra,
            source="network-record+netns-dir",
            metadata={"record_known": True, "namespaces_known": present is not None,
                      "networks": 0,
                      "namespace_count": None if present is None else len(present)},
        )

    lines = [f"{len(record)} lab network(s) recorded in "
             f"{state_dir() / 'network-records.json'}:"]
    broken = []
    for name in sorted(record):
        lines.extend(_describe(name, record[name], present))
        expected = {name} | {f"{name}--{s.get('name')}"
                             for s in (record[name].get("subnets") or [])}
        if present is not None and any(n not in present for n in expected):
            broken.append(name)
    if broken:
        lines.append(
            f"{len(broken)} of them ({', '.join(broken)}) have namespaces "
            f"missing from the kernel. That is a half-built or already-"
            f"partly-removed network, not a working one - a network whose "
            f"namespaces are gone cannot route anything, whatever its record "
            f"still says."
        )
    lines.append(
        f"{len(present)} namespace(s) exist in total"
        + ("" if present is not None else " (count UNKNOWN)")
        + ". This reads only; it does not change any network."
    )

    return _SENSE.to_percept(
        "\n".join(lines),
        source="network-record+netns-dir",
        metadata={
            "record_known": True,
            "namespaces_known": present is not None,
            "networks": len(record),
            "incomplete": broken,
            "namespace_count": None if present is None else len(present),
        },
    )


_SENSE = Sense(
    name="labnetworks",
    kind=KIND,
    ttl_seconds=_TTL_SECONDS,
    sensitivity=SENSITIVITY,
    schema=_SCHEMA,
    run=_run,
    poll_interval=_POLL_INTERVAL,
)

#: The registry discovers senses by looking for this list. A module that omits
#: it is skipped *silently* for a builtin (see `_register`), so a sense can be
#: complete, importable, and still absent from every surface with nothing in the
#: log to say why.
SENSES = [_SENSE]

#: Kept for a caller that wants the sense itself rather than the list.
SENSE = _SENSE