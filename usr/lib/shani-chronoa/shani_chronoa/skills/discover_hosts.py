"""Skill: who is on this network right now, including devices that have not spoken to this machine.

`neighbour_table` reads the kernel's own ARP cache, which is the right tool and
has one blind spot: it only contains hosts that have *already* talked to this
machine. A device on the same segment that has never sent anything here is
absent from it entirely. So "is there anything else on this LAN" cannot be
answered by reading a passive cache, and this is the tool that answers it by
asking.

**Scoped to private ranges, always, and refused rather than widened.** The
target is derived from *this machine's own address and prefix*, so a machine on
192.168.31.0/24 scans 192.168.31.0/24 and never anything else. There is no
argument that takes an arbitrary range: that is the difference between auditing
the network you are standing on and sweeping the Internet, and the second is not
a capability an assistant on a laptop should have behind a chat message. A
public target is refused by name, with the reason.

**`arp-scan` needs raw sockets, which is an honest limitation rather than a
bug.** Unprivileged it can still use its own discovery path on many
interfaces and will say plainly when it cannot open one. The message does not
pretend a partial scan was complete.

**Discovery tells you what is there, never what it is running.** Each row is an
address and a hardware address. `scan_network` is the separate, gated skill
that probes ports on a named host; this one does not connect to anything it
finds, because a sweep that also connects is a different act with a different
consent question attached.

**No packet contents are read.** This reads addresses that are already on the
wire in the clear as part of normal ARP, and nothing else - not a payload, not
a hostname it did not already resolve.
"""

from __future__ import annotations

import ipaddress
import logging
import re
import shutil
import subprocess
from typing import List, Optional, Tuple

from shani_chronoa import sysfs
from shani_chronoa.skills import Skill

logger = logging.getLogger(__name__)

_ARP_SCAN = "arp-scan"
_SYS_CLASS_NET = "/sys/class/net"
_TIMEOUT = 90

#: How many hosts to sweep. A /24 is 254; this leaves headroom under it and the
#: ceiling is stated so the number is never a surprise.
_MAX_HOSTS = 254

_IFACE = re.compile(r"^[A-Za-z0-9_.:@-]{1,15}$")


def _le_hex(value: str) -> str:
    """`/proc/net/route` writes addresses as little-endian hex: 0101A8C0 is
    192.168.1.0. Getting this backwards yields a plausible-looking but wrong
    network, so it is one named function rather than inline arithmetic."""
    try:
        packed = bytes.fromhex(value)
    except ValueError:
        return ""
    if len(packed) != 4:
        return ""
    return ".".join(str(b) for b in reversed(packed))


def _local_ipv4() -> List[Tuple[str, str, str]]:
    """(interface, network, prefix-length) for each IPv4 segment on this machine.

    **From `/proc/net/route`, not `/proc/net/fib_trie`.** fib_trie lists local
    addresses with their host bits set and **no prefix length at all**
    (`|-- 172.17.0.1`), so the segment a host is on cannot be derived from it -
    an earlier version of this function read that file and so always concluded
    the machine had no IPv4, which then surfaced as "no network to discover"
    rather than as its own bug. The routing table carries both the network
    destination and its mask, which is exactly what is needed.

    Read from `/proc` rather than by running `ip -4 addr`, so a minimal image
    without `iproute2` still answers.
    """
    out: List[Tuple[str, str, str]] = []
    text = sysfs.read_text("/proc/net/route")
    if not text:
        return out
    for line in text.splitlines()[1:]:
        fields = line.split()
        if len(fields) < 8:
            continue
        device, destination, mask = fields[0], _le_hex(fields[1]), _le_hex(fields[7])
        if not destination or not mask:
            continue
        try:
            network = ipaddress.ip_network(f"{destination}/{mask}", strict=False)
        except ValueError:
            continue
        if network.is_loopback or not network.is_private:
            continue
        out.append((device, str(network), str(network.prefixlen)))
    return out


def _resolve_target(arguments: dict) -> Tuple[Optional[str], Optional[str]]:
    """The subnet to sweep: derived from this machine, never from a URL."""
    explicit = arguments.get("network")
    if explicit is not None:
        if not isinstance(explicit, str):
            return None, f"'network' must be a CIDR, not {explicit!r}."
        try:
            network = ipaddress.ip_network(explicit.strip(), strict=False)
        except ValueError as exc:
            return None, f"{explicit!r} is not a network: {exc}."
        if not network.is_private:
            return None, (
                f"Refusing to sweep {network}: it is not a private range. This "
                f"only asks about the network this machine is standing on. If "
                f"you own a public range you want audited, name it in a ticket "
                f"and it can be arranged deliberately."
            )
        if network.num_addresses > _MAX_HOSTS:
            return None, (
                f"{network} is {network.num_addresses} addresses; this sweeps at "
                f"most {_MAX_HOSTS}. Sweeping a range this size from a desktop "
                f"is slow and loud. Narrow it to the segment you mean."
            )
        return str(network), None

    found = _local_ipv4()
    if not found:
        return None, (
            "No private IPv4 segment on this machine, so there is nothing local "
            "to discover - the routing table reports only loopback, or is "
            "unreadable, and those are different problems reported the same way "
            "here. Pass network= with a private CIDR to name one."
        )
    # The widest private network this machine is on - a /24 over a /16 - is the
    # one a person means by "this network".
    # The narrowest private segment wins: a /24 on the WiFi is "this network"
    # far more than the /16 a VPN might also be attached to.
    best = max((ipaddress.ip_network(n) for _d, n, _p in found),
               key=lambda n: n.prefixlen)
    return str(best), None


def _run(arguments: dict) -> str:
    # The target is resolved and validated FIRST. A refusal has to be a refusal
    # whether or not the tool happens to be installed - otherwise "arp-scan is
    # not installed" is the answer to every question, including the ones whose
    # answer is "no, and not because of that".
    network, problem = _resolve_target(arguments)
    if problem:
        return problem

    if shutil.which(_ARP_SCAN) is None:
        return (f"Not done: {_ARP_SCAN} is not installed, so devices on "
                f"{network} cannot be discovered. On Arch it comes from the "
                f"'arp-scan' package.")

    iface = arguments.get("interface")
    if iface is not None and (not isinstance(iface, str)
                              or not _IFACE.match(iface.strip())):
        return (f"'interface' must be an interface name of letters, digits, "
                f"'.', '_', ':', '-' or '@' up to 15 characters, not {iface!r}.")
    device = iface.strip() if isinstance(iface, str) else ""

    argv = [_ARP_SCAN, "--retry=2", f"--timeout=800"]
    if device:
        argv += ["-I", device]
    argv.append(network)

    try:
        done = subprocess.run(argv, capture_output=True, text=True,
                              timeout=_TIMEOUT, check=False)
    except subprocess.TimeoutExpired:
        return (f"The sweep of {network} did not finish within {_TIMEOUT} "
                f"seconds and was stopped. No results are reported - a partial "
                f"sweep would read as a complete one and hide devices that did "
                f"not answer in time.")
    except OSError as exc:
        return f"Not done: {_ARP_SCAN} could not be run ({exc})."

    blob = (done.stdout or "") + "\n" + (done.stderr or "")
    lowered = blob.lower()
    if "permission" in lowered or "you need to be root" in lowered:
        return (
            f"Not done: {_ARP_SCAN} could not open a raw socket - it says: "
            f"{' '.join(blob.strip().splitlines()[-1:])}. Sweeping needs raw "
            f"socket access. Run it with sudo yourself, or use "
            f"neighbour_table, which reads the kernel's existing cache and "
            f"needs no privilege (but only shows hosts that have already "
            f"talked to this machine).")

    rows = []
    for line in done.stdout.splitlines():
        parts = line.split()
        if len(parts) >= 2 and re.fullmatch(r"\d+\.\d+\.\d+\.\d+", parts[0]):
            rows.append((parts[0], parts[1]))

    if not rows:
        note = ""
        if "0 hosts" in lowered or "no replies" in lowered:
            note = (" Every address in this range was probed and none "
                    "answered.")
        return (f"No devices answered on {network}.{note} That is a "
                f"measurement of this segment, not of the Internet, and a "
                f"host that is powered off looks exactly like one that does "
                f"not exist.")

    seen = {}
    for address, hardware in rows:
        seen.setdefault(hardware, []).append(address)
    unique_hardware = len(seen)

    out = [f"{len(rows)} address(es) on {network} answered ARP"
           + (f", from {device}" if device else "")
           + f", carrying {unique_hardware} distinct hardware address(es):",
           "",
           "  address          hardware address",
           "  " + "-" * 40]
    for address, hardware in sorted(rows, key=lambda r: ipaddress.ip_address(r[0])):
        out.append(f"  {address:<16} {hardware}")

    lines_each = sorted(hardware for _addr, hardware in rows
                        if len(seen[hardware]) > 1)
    if lines_each:
        out.append("")
        out.append(
            f"{len(lines_each)} hardware address(es) answered on more than one "
            f"address. That is normal for a device with several interfaces, or "
            f"for a virtual machine whose address changed. If a device you do "
            f"not recognise is here, that is worth knowing.")

    out.append("")
    out.append("This only discovered who answered. Nothing was connected to, "
               "and no packet contents were read. To ask what a specific host "
               "is running, name it to scan_network.")
    return "\n".join(out)


SCHEMA = {
    "type": "function",
    "function": {
        "name": "discover_hosts",
        "description": (
            "Find which devices are on this network right now, by asking rather "
            "than by reading the cache - so devices that have never talked to "
            "this machine are found too. Reports an address and a hardware "
            "address for each, and counts devices answering on more than one "
            "address. The range is always the private network this machine is "
            "on; pass network= for a different private range, and a public one "
            "is refused. Connects to nothing it finds and reads no packet "
            "contents. Needs raw-socket access, so unprivileged it may report "
            "that rather than a partial sweep."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "interface": {
                    "type": "string",
                    "description": "Only this interface, e.g. 'enp4s0'. Omit to use all.",
                },
                "network": {
                    "type": "string",
                    "description": (
                        "A private CIDR to sweep instead of the one this "
                        "machine is on. A public range is refused."
                    ),
                },
            },
        },
    },
}


SKILLS = [Skill(name="discover_hosts", schema=SCHEMA, run=_run)]