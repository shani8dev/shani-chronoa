"""Network sense: whether each interface is up, and what it is connected to.

`/sys/class/net/<if>/operstate` and `carrier` say whether a link is up;
`/etc/resolv.conf` names the resolvers this machine will actually use.

**This reads link state, not traffic, and it does not sense the radio
environment.** No packet contents, no per-connection accounting, no scan of
neighbouring access points, and nothing radiometric. A neighbour-list sense
would need `iw` in monitor mode, which is a different capability with a
different privacy profile, and is not this.

The reason it is worth having: "is the network actually up" is a question
every agent eventually needs answered and no existing sense answers, and it
is the cheapest possible read of whether a failure is local or remote.
"""

import logging
import re
from pathlib import Path
from typing import Optional, Union

from shani_chronoa.config import ChronoaConfig
from shani_chronoa.senses import SENSITIVITY_PUBLIC, Sense
from shani_chronoa.senses.context import Percept

logger = logging.getLogger(__name__)

KIND = "machine-state"
SENSITIVITY = SENSITIVITY_PUBLIC
_TTL_SECONDS = 120.0
_POLL_INTERVAL = 120.0

_NET = "/sys/class/net"
_RESOLV = "/etc/resolv.conf"
# Loopback carries no information worth reporting and would just be noise.
_SKIP = {"lo"}

_SCHEMA = {
    "type": "function",
    "function": {
        "name": "network",
        "description": (
            "Read network interface state - which interfaces exist, whether "
            "each link is up and has carrier, and the machine's configured DNS "
            "servers. Link state only: no traffic contents are read and no "
            "neighbouring networks are scanned."
        ),
        "parameters": {"type": "object", "properties": {}},
    },
}


def _read_text(path: Path) -> Optional[str]:
    try:
        return path.read_text().strip()
    except OSError:
        return None


def read_interfaces() -> list:
    out = []
    try:
        entries = sorted(Path(_NET).iterdir())
    except OSError:
        return out
    for entry in entries:
        if entry.name in _SKIP:
            continue
        out.append({
            "interface": entry.name,
            "operstate": _read_text(entry / "operstate") or "unknown",
            "carrier": _read_text(entry / "carrier") or "unknown",
            "wireless": (entry / "wireless").is_dir(),
        })
    return out


def read_resolvers() -> list:
    text = _read_text(Path(_RESOLV))
    if not text:
        return []
    return re.findall(r"^\s*nameserver\s+(\S+)", text, re.MULTILINE)


def _run(arguments: dict) -> Union[str, Percept]:
    config = ChronoaConfig()
    if not config.sense_allowed("network"):
        return f"Not reading network state: {config.sense_allowed_reason('network')}."

    interfaces = read_interfaces()
    if not interfaces:
        return "This machine exposes no network interfaces to read."

    lines = []
    for iface in interfaces:
        detail = f"{iface['interface']}: {iface['operstate']}"
        if iface["carrier"] == "1":
            detail += ", carrier"
        if iface["wireless"]:
            detail += ", wireless"
        lines.append(detail)
    resolvers = read_resolvers()
    lines.append(f"DNS: {', '.join(resolvers) if resolvers else 'none configured'}")
    up = sum(1 for i in interfaces if i["operstate"] == "up")
    return _SENSE.to_percept(
        "\n".join(lines),
        source="sysfs-net",
        metadata={"interfaces": len(interfaces), "up": up},
    )


_SENSE = Sense(
    name="network",
    kind=KIND,
    ttl_seconds=_TTL_SECONDS,
    sensitivity=SENSITIVITY,
    schema=_SCHEMA,
    run=_run,
    poll_interval=_POLL_INTERVAL,
)

SENSES = [_SENSE]
