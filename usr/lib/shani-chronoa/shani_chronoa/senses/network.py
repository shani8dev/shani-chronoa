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
import shutil
import re
import re
import subprocess
from pathlib import Path
from typing import Dict, List, Optional, Union
from typing import Optional, Union

from shani_chronoa.config import ChronoaConfig
from shani_chronoa.senses import SENSITIVITY_PUBLIC, Sense
from shani_chronoa.senses.context import Percept

logger = logging.getLogger(__name__)

KIND = "machine-state"
SENSITIVITY = SENSITIVITY_PUBLIC
_TTL_SECONDS = 120.0
_POLL_INTERVAL = 120.0

_NET = Path("/sys/class/net")
_RESOLV = "/etc/resolv.conf"
# Loopback carries no information worth reporting and would just be noise.
_LOOPBACK = "lo"
_TIMEOUT = 10
_SKIP = {_LOOPBACK}

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


def read_resolvers() -> list:
    text = _read_text(Path(_RESOLV))
    if not text:
        return []
    return re.findall(r"^\s*nameserver\s+(\S+)", text, re.MULTILINE)


_UNKNOWN_VALUES = {"unknown!", "unknown (255)", "unknown!", "n/a"}


def _read_text(path: Path) -> Optional[str]:
    try:
        return path.read_text().strip()
    except OSError:
        return None


def _sysfs_link(interface: str) -> dict:
    entry = _NET / interface
    record = {
        "operstate": _read_text(entry / "operstate") or "unknown",
        "carrier": _read_text(entry / "carrier"),
        "mtu": _read_text(entry / "mtu"),
        "address": _read_text(entry / "address"),
        "wireless": (entry / "wireless").is_dir(),
    }
    speed = _read_text(entry / "speed")
    # `-1` is the kernel's "not applicable or not known" for a down link, and
    # the file is empty for a wireless interface. Neither is a number.
    if speed is not None and speed.lstrip("-").isdigit() and int(speed) > 0:
        record["speed_mbps"] = int(speed)
    else:
        record["speed"] = None
    duplex = _read_text(entry / "duplex")
    if duplex and duplex not in ("unknown", ""):
        record["duplex"] = duplex
    return record


def _ethtool(interface: str) -> Optional[dict]:
    """`ethtool <iface>`, parsed for the fields that are actually present.

    Returns None when the tool is absent or produced nothing usable, which is
    different from a link that is up at an unknown speed.
    """
    if shutil.which("ethtool") is None:
        return None
    try:
        proc = subprocess.run(
            ["ethtool", interface],
            capture_output=True, text=True, timeout=_TIMEOUT, check=False,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        logger.debug("ethtool failed on %s: %s", interface, exc)
        return None

    out = (proc.stdout or "").lower()
    if not out.strip():
        return None

    found: Dict[str, object] = {}
    for line in (proc.stdout or "").splitlines():
        key, _, value = line.partition(":")
        key = key.strip().lower()
        value = value.strip()
        if key == "speed":
            # "1000Mb/s" -> 1000, but "Unknown!" is not a speed.
            if value.lower().endswith("mb/s") and value[: -len("mb/s")].strip().isdigit():
                found["speed_mbps"] = int(value[: -len("mb/s")].strip())
            else:
                found["speed"] = None
        elif key == "duplex":
            found["duplex"] = None if value.lower().startswith("unknown") else value
        elif key == "link detected":
            found["carrier"] = value.lower() == "yes"
        elif key == "driver":
            found["driver"] = value
        elif key == "firmware-version":
            found["firmware"] = value
    return found or None


def read_links() -> List[dict]:
    """Every non-loopback interface, sysfs first and ethtool as a supplement."""
    try:
        names = sorted(p.name for p in _NET.iterdir() if p.name != _LOOPBACK)
    except OSError as exc:
        logger.debug("cannot list /sys/class/net: %s", exc)
        return []

    links = []
    for name in names:
        record = {"interface": name}
        record.update(_sysfs_link(name))
        extra = _ethtool(name)
        if extra:
            record["ethtool"] = extra
            if "speed_mbps" in extra:
                record["speed_mbps"] = extra["speed_mbps"]
            if "driver" in extra:
                record["driver"] = extra["driver"]
        links.append(record)
    return links


def _run(arguments: dict) -> Union[str, Percept]:
    config = ChronoaConfig()
    if not config.sense_allowed("network"):
        return f"Not reading network state: {config.sense_allowed_reason('network')}."

    links = read_links()
    if not links:
        return (
            "No network interface was readable under /sys/class/net. On a "
            "running system that is a fact about what could be read rather "
            "than a claim that the machine has no network."
        )

    lines = []
    with_speed = 0
    for link in links:
        up = link["operstate"] == "up"
        detail = f"{link['interface']}: {'up' if up else link['operstate']}"
        if link.get("carrier") == "1":
            detail += ", carrier"
        if link.get("wireless"):
            detail += ", wireless"
        if "speed_mbps" in link:
            detail += f", {link['speed_mbps']} Mb/s"
            duplex = link.get("duplex") or (link.get("ethtool") or {}).get("duplex")
            if duplex:
                detail += f" {duplex}"
            with_speed += 1
        elif up:
            # The honest statement. "0 Mb/s" would be a confident wrong answer.
            detail += ", link up but this interface reports no speed"
        if link.get("driver"):
            detail += f" (driver {link['driver']})"
        lines.append(detail)

    resolvers = read_resolvers()
    lines.append(f"DNS: {', '.join(resolvers) if resolvers else 'none configured'}")

    # Duplicate addresses mean a bridge, bond or container network, and are
    # worth naming because otherwise the list looks like several machines.
    seen: Dict[str, int] = {}
    for link in links:
        if link.get("address"):
            seen[link["address"]] = seen.get(link["address"], 0) + 1
    shared = [addr for addr, count in seen.items() if count > 1]
    if shared:
        lines.append(
            f"{len(shared)} interface(s) share a hardware address, so this is a "
            f"bridge, bond or container network rather than {len(links)} links "
            f"to {len(links)} places: {', '.join(shared)}"
        )

    lines.append(
        f"{len(links)} interface(s); {with_speed} report a link speed. "
        f"Wireless interfaces do not report one, and a down port reports "
        f"Unknown rather than zero - neither is a measurement."
    )

    return _SENSE.to_percept(
        "\n".join(lines),
        source="sysfs-net+ethtool",
        metadata={
            "interfaces": len(links),
            "up": sum(1 for l in links if l["operstate"] == "up"),
            "with_speed": with_speed,
            "wireless": sum(1 for l in links if l.get("wireless")),
            "resolvers": len(resolvers),
            "ethtool_present": shutil.which("ethtool") is not None,
        },
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
