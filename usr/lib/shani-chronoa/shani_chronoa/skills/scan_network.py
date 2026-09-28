"""Skill: find what else is on the local network.

Behind the `network` sense consent gate, and bounded to private address space.
Both of those are load-bearing rather than decorative:

- A network scan is perception of a whole neighbourhood, not of this machine.
  It sits behind the same `ChronoaConfig().sense_allowed("network")` check the
  `network` sense uses, so one switch governs both, and the refusal explains
  itself through `sense_allowed_reason()` rather than just failing.
- The range is refused unless it is private, link-local or loopback. A tool
  that will scan whatever range it is handed is a tool that will scan the
  internet, and "scan 8.8.8.0/24" is a request an LLM can be talked into making
  from a harmless-sounding question.

Host discovery only (`-sn`, ICMP/ARP), never a port scan. "What is on my
network" is the question this answers; a port sweep is a different and much more
intrusive request that belongs in its own gated skill if it is ever wanted.

Reverse DNS is off by default (`-n`). Resolving a found address sends a query
naming that device to a DNS server, which is a real disclosure to a third party
and is not obviously implied by "what is on my network" - so it is opt-in.

Reporting rules, because each of these is a way to return a clean-looking wrong
answer:

- `nmap` exiting non-zero does **not** mean it failed. Exit 1 means some hosts
  did not respond, and it still printed the ones that did. Discarding output
  on a non-zero status throws away real results - the exact inverse of the
  usual "only trust success" instinct, and worth stating because it is
  counter-intuitive.
- An empty result is not an empty network. A firewall dropping ICMP, or ARP
  being unavailable, both produce zero hosts on a busy LAN.
- Without root, nmap often cannot read MAC addresses, so vendor identification
  silently degrades. That is stated rather than presented as "no vendors found".
"""

from __future__ import annotations

import fcntl
import ipaddress
import logging
import shutil
import socket
import struct
import subprocess
from pathlib import Path

from shani_chronoa.config import ChronoaConfig
from shani_chronoa.skills import Skill

logger = logging.getLogger(__name__)

_SCHEMA = {
    "type": "function",
    "function": {
        "name": "scan_network",
        "description": (
            "Find which devices are on the local network, with their names, "
            "addresses and hardware vendors. Local addresses only. Requires the "
            "network sense to be enabled."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "range": {
                    "type": "string",
                    "description": (
                        "Optional CIDR to scan, e.g. '192.168.1.0/24'. Must be a "
                        "private, link-local or loopback range. Defaults to the "
                        "subnet this machine is on."
                    ),
                },
                "resolve_names": {
                    "type": "boolean",
                    "description": (
                        "Look up hostnames via reverse DNS. Off by default: it "
                        "sends a query naming each discovered device to a DNS "
                        "server."
                    ),
                    "default": False,
                },
            },
        },
    },
}

_TIMEOUT = 120
_MAX_HOSTS = 254
_SIOCGIFADDR = 0x8915
_SIOCGIFNETMASK = 0x891B


def _default_interface() -> str | None:
    """The interface holding the default route, straight from /proc."""
    try:
        rows = Path("/proc/net/route").read_text().splitlines()
    except OSError:
        return None
    for row in rows[1:]:
        parts = row.split()
        if len(parts) > 2 and parts[1] == "00000000":
            return parts[0]
    return None


def _iface_address(interface: str) -> tuple[str, str] | None:
    """(address, netmask) for an interface, via ioctl so no binary is needed."""
    try:
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    except OSError:
        return None
    try:
        packed = struct.pack("256s", interface.encode()[:15])
        addr = socket.inet_ntoa(fcntl.ioctl(sock.fileno(), _SIOCGIFADDR, packed)[20:24])
        mask = socket.inet_ntoa(fcntl.ioctl(sock.fileno(), _SIOCGIFNETMASK, packed)[20:24])
        return addr, mask
    except (OSError, ValueError):
        return None
    finally:
        sock.close()


def _local_subnet() -> str | None:
    interface = _default_interface()
    if not interface:
        return None
    found = _iface_address(interface)
    if not found:
        return None
    addr, mask = found
    try:
        return str(ipaddress.ip_network(f"{addr}/{mask}", strict=False))
    except ValueError:
        return None


def _scannable(target: str) -> str | None:
    """Accept only private/link-local/loopback space, and cap the size."""
    try:
        network = ipaddress.ip_network(target, strict=False)
    except ValueError as exc:
        return f"'{target}' is not a valid network range ({exc})."
    if not (network.is_private or network.is_link_local or network.is_loopback):
        return (
            f"Refusing to scan {network}: only private, link-local or loopback "
            f"ranges are allowed. This skill is for the local network."
        )
    if network.num_addresses > _MAX_HOSTS + 2:
        return (
            f"Refusing to scan {network}: {network.num_addresses} addresses is "
            f"more than this will do ({_MAX_HOSTS} max). Narrow the range."
        )
    return None


def _parse_hosts(lines: list[str]) -> list[dict[str, str]]:
    """Pull one record per host out of nmap's output.

    nmap prints three lines per host, not one: the report line, the latency
    line, then the MAC and vendor. Only the report line carries the name, and
    in the default no-reverse-DNS mode it carries nothing but the address - so
    reading that line alone yields bare IPs and silently throws away the vendor
    that makes the answer useful.
    """
    marker = "Nmap scan report for "
    hosts: list[dict[str, str]] = []
    for index, line in enumerate(lines):
        if marker not in line:
            continue
        rest = line.split(marker, 1)[1].strip()
        name, address = "", rest
        if "(" in rest and rest.endswith(")"):
            name, _, address = rest.partition(" (")
            address, name = address.rstrip(")"), name.strip()

        mac = vendor = ""
        for following in lines[index + 1:index + 4]:
            if marker in following:
                break
            if "MAC Address:" in following:
                _, _, tail = following.partition("MAC Address:")
                tail = tail.strip()
                if "(" in tail:
                    mac, _, vendor = tail.partition(" (")
                    vendor = vendor.rstrip(")").strip()
                else:
                    mac = tail
                break

        hosts.append({"name": name, "address": address,
                      "mac": mac, "vendor": vendor})
    return hosts


def _describe(lines: list[str], resolved: bool) -> str:
    hosts = _parse_hosts(lines)
    if not hosts:
        return (
            "No hosts answered. That does not mean the network is empty - a "
            "firewall dropping ICMP, or ARP being unavailable, both look "
            "identical to an empty LAN from here."
        )

    out = [f"{len(hosts)} host(s) responded:"]
    for host in hosts:
        label = f"{host['name']} ({host['address']})" if host["name"] else host["address"]
        detail = f"{host['vendor']} [{host['mac']}]" if host["mac"] else host["vendor"]
        out.append(f"  {label}" + (f" - {detail}" if detail else ""))

    if not resolved and not any(h["name"] for h in hosts):
        out.append(
            "No hostnames were resolved (reverse DNS is off). Pass "
            "resolve_names=true to look them up."
        )
    if not any(h["mac"] for h in hosts):
        out.append(
            "No hardware addresses were read, so these hosts could not be "
            "identified by vendor - expected when the scan did not have the "
            "privilege to read them, and not the same as a network of generic "
            "hardware."
        )
    return "\n".join(out)


def _run(arguments: dict) -> str:
    config = ChronoaConfig()
    if not config.sense_allowed("network"):
        return (
            f"Network scanning is not permitted: "
            f"{config.sense_allowed_reason('network')}."
        )

    requested = str(arguments.get("range") or "").strip()
    if requested:
        refusal = _scannable(requested)
        if refusal:
            return refusal
        target = requested
    else:
        target = _local_subnet()
        if not target:
            return (
                "Could not work out which network this machine is on: the "
                "default route or its address could not be read. Pass an "
                "explicit range, e.g. range='192.168.1.0/24'."
            )
        refusal = _scannable(target)
        if refusal:
            return refusal

    if shutil.which("nmap") is None:
        return (
            "Could not scan: `nmap` is not installed on this machine, so no "
            "scan was run. That is not the same as finding no devices."
        )

    resolve = bool(arguments.get("resolve_names"))
    cmd = ["nmap", "-sn"]
    if not resolve:
        cmd.append("-n")
    cmd.append(target)

    try:
        proc = subprocess.run(
            cmd, capture_output=True, text=True, timeout=_TIMEOUT, check=False)
    except subprocess.TimeoutExpired:
        return (
            f"Giving up on the scan of {target} after {_TIMEOUT}s. Which hosts "
            f"are present is unknown - a partial list is not a short network."
        )
    except (OSError, subprocess.SubprocessError) as exc:
        return f"Could not scan {target}: nmap could not be run ({exc})."

    body = _describe(proc.stdout.splitlines(), resolve)
    # Exit 1 means some hosts did not respond, not that the scan failed.
    if proc.returncode == 1:
        return f"{body}\n\nSome hosts did not respond to the probe."
    if proc.returncode != 0:
        detail = (proc.stderr or "").strip() or "unknown error"
        return f"Could not scan {target}: nmap failed ({detail})."
    return body


SKILLS = [Skill(name="scan_network", schema=_SCHEMA, run=_run)]
