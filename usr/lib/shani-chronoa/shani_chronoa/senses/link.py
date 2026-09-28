"""Network link detail — negotiated speed, duplex, driver, and what a wireless
interface does *not* report.

**The trap that makes this worth a module: `ethtool` reports "Unknown!" rather
than failing, on a wired interface that is simply down.** Measured on this
machine, with no root:

    $ ethtool enp4s0
        Speed: Unknown!
        Duplex: Unknown! (255)
        Link detected: no
    $ ethtool wlp0s20f3
        Link detected: yes
        (no Speed line at all)

Three different absence shapes, each of which a parser that greps for "Speed:"
turns into a confident wrong answer: a wired port reporting `Unknown!`, a
wireless interface emitting no speed line whatsoever, and a genuinely fast
link reporting a real number. `speed` in sysfs has the same problem in a
quieter form — measured `speed=-1` on the down wired port and an *empty file*
on the wireless one, while `operstate` and `carrier` still say `up`/`1`.

So link speed is only reported when a real number came back, and the absence is
described rather than substituted with 0. A 0 Gb/s link is a real and
meaningless-looking answer; "speed not reported for this interface" is the
true one, and the difference matters to anyone asking whether their network is
the bottleneck.

**This does not attempt link *quality*.** `rfsense` already reads
`/proc/net/wireless` for signal level and its variance, which is a different
and more useful question than how fast the link is. Duplicating RSSI here
would be worse than not having it.

No new package: `ethtool` is in `shani-tools-network`, which is in
`Packages-Base` for every shipped profile.
"""

import logging
import shutil
import subprocess
from pathlib import Path
from typing import Dict, List, Optional, Union

from shani_chronoa.config import ChronoaConfig
from shani_chronoa.senses import SENSITIVITY_PUBLIC, Sense
from shani_chronoa.senses.context import Percept

logger = logging.getLogger(__name__)

KIND = "machine-state"
SENSITIVITY = SENSITIVITY_PUBLIC
_TTL_SECONDS = 300.0
_POLL_INTERVAL = 300.0

_NET = Path("/sys/class/net")
_TIMEOUT = 10
_LOOPBACK = "lo"

# ethtool prints these rather than failing, and both mean "no reading".
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
    if not config.sense_allowed("link"):
        return f"Not reading link state: {config.sense_allowed_reason('link')}."

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
            "ethtool_present": shutil.which("ethtool") is not None,
        },
    )


_SCHEMA = {
    "type": "function",
    "function": {
        "name": "link",
        "description": (
            "Read the network interfaces on this machine: whether each is up, "
            "whether it has carrier, its MTU and hardware address, and for "
            "wired interfaces the negotiated speed and duplex, plus the driver "
            "and firmware version. Reports no speed for wireless interfaces and "
            "for a port that is down, because neither reports one - a link "
            "with no speed reading is stated as such rather than as zero. Also "
            "flags interfaces that share a hardware address, which means a "
            "bridge, bond or container network rather than separate links. "
            "Does not report signal strength or noise: the rfsense sense "
            "already covers that, and link quality is a different question from "
            "link speed."
        ),
        "parameters": {"type": "object", "properties": {}},
    },
}

_SENSE = Sense(
    name="link",
    kind=KIND,
    ttl_seconds=_TTL_SECONDS,
    sensitivity=SENSITIVITY,
    schema=_SCHEMA,
    run=_run,
    poll_interval=_POLL_INTERVAL,
)

SENSES = [_SENSE]
