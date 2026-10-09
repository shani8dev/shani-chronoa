"""Skill: is the internet working, and if not, where does it stop?

Walks the path the way a person troubleshooting would, and stops at the
first step that fails: is there a network connection at all (the default
route), does the router answer (ping the gateway), is the internet reachable
(ping a public address), does DNS work (resolve a name), and does a website
load. Each answer names the step, so "the router answers but DNS fails" is
something a person can act on.

It also checks for an **IP conflict** - the same address answering from two
different MACs in the kernel's ARP cache. That is the classic cause of a
connection that works sometimes and drops other times, it is invisible to a
ping (a ping reaches whichever host replied), and nothing else here looks.

In privacy mode only the local steps run: the rest send packets beyond this
network.
"""

import re
import socket
import subprocess
import time

from shani_chronoa.skills import Skill

PUBLIC_IP = "1.1.1.1"
NAME = "example.com"

_SCHEMA = {
    "type": "function",
    "function": {
        "name": "check_internet",
        "description": "Check whether the internet is working and, if not, which step fails: "
                       "network connection, router, internet, DNS, or websites. Also reports "
                       "the delay to the router and to the internet.",
        "parameters": {"type": "object", "properties": {}},
    },
}


def _ping(host: str):
    try:
        r = subprocess.run(["ping", "-c", "3", "-W", "2", "-q", host], capture_output=True, text=True, timeout=15)
    except (OSError, subprocess.TimeoutExpired):
        return None
    if r.returncode != 0:
        return None
    m = re.search(r"= [\d.]+/([\d.]+)/", r.stdout)
    return float(m.group(1)) if m else 0.0


def _arp_entries() -> "list[tuple[str, str, str]]":
    """`(ip, device, mac)` from `ip neigh show` - the kernel's ARP cache.

    **Why the kernel cache and not `arping -D`.** A duplicate address - two
    hosts answering for one IP - is the classic cause of "the internet works
    sometimes", and ping never shows it. `arping -D` is the usual probe, but
    its exit-status contract could not be measured on this box (no root, not
    installed), and a status code read from memory is exactly the wrong
    answer this module is built to avoid. The kernel's own cache is the same
    information - a conflicting address shows up as two entries for one IP
    with different MACs - it needs no extra binary (`ip` is already the tool
    this skill reads the route from) and it cannot send anything.
    """
    try:
        r = subprocess.run(["ip", "neigh", "show"], capture_output=True,
                           text=True, timeout=5)
    except (OSError, subprocess.TimeoutExpired):
        return []
    if r.returncode != 0:
        return []
    entries = []
    for line in (r.stdout or "").splitlines():
        parts = line.split()
        if len(parts) < 2:
            continue
        ip = parts[0]
        mac = ""
        dev = ""
        i = 1
        while i < len(parts) - 1:
            if parts[i] == "dev":
                dev = parts[i + 1]
            elif parts[i] == "lladdr":
                mac = parts[i + 1]
            i += 2
        if ip and dev and mac:
            entries.append((ip, dev, mac))
    return entries


def _conflicts(ip: "str | None" = None) -> "list[str]":
    """Addresses the cache has seen at two different MACs, router first.

    A conflict is only ever *what the cache has recorded*: the entries are
    short-lived, so no conflict here is not proof of none. That is stated in
    the answer rather than left to the reader.
    """
    seen: "dict[str, set[str]]" = {}
    for addr, _dev, mac in _arp_entries():
        if mac in ("00:00:00:00:00:00", "ff:ff:ff:ff:ff:ff"):
            continue
        seen.setdefault(addr, set()).add(mac)
    conflicts = sorted(a for a, macs in seen.items() if len(macs) > 1)
    if ip and ip in conflicts:
        conflicts.remove(ip)
        conflicts.insert(0, ip)
    return conflicts


def _gateway():
    try:
        r = subprocess.run(["ip", "-4", "route", "show", "default"], capture_output=True, text=True, timeout=5)
    except (OSError, subprocess.TimeoutExpired):
        return None, None
    m = re.search(r"default via (\S+) dev (\S+)", r.stdout)
    return (m.group(1), m.group(2)) if m else (None, None)


def _run(_arguments: dict) -> str:
    from shani_chronoa import egress
    gw, dev = _gateway()
    if not gw:
        return "No network connection: there is no default route (Wi-Fi or cable is not connected)."
    steps = [f"Connected through {dev}, router {gw}."]
    conflicts = _conflicts(gw)
    if conflicts:
        # Router first, and named as the router: a second MAC answering for
        # the router's address is the one case where the answer is urgent
        # rather than merely interesting.
        if conflicts[0] == gw:
            who = f"the router's address {gw} itself"
        else:
            who = f"{conflicts[0]} on this network"
        steps.append(f"**Possible IP conflict:** {who} has answered from two "
                     "different MAC addresses. Two machines answering for one "
                     "address is a common cause of connections that work "
                     "sometimes and drop other times - and it is invisible to "
                     "a ping, which reaches whichever one replied. This is what "
                     "the ARP cache has recorded, not a guarantee none exist.")
    rt = _ping(gw)
    steps.append(f"The router answers ({rt:.0f} ms)." if rt is not None else
                 "The router does not answer pings (many do not; carrying on).")
    if egress.privacy_mode_enabled():
        return " ".join(steps) + " Privacy mode is on, so I did not test anything beyond your network."
    ip = _ping(PUBLIC_IP)
    if ip is None:
        return " ".join(steps) + f" The internet is NOT reachable: {PUBLIC_IP} does not answer."
    steps.append(f"The internet is reachable ({ip:.0f} ms to {PUBLIC_IP}).")
    t0 = time.time()
    try:
        socket.getaddrinfo(NAME, 443)
    except OSError:
        return " ".join(steps) + " But DNS is NOT working: names do not resolve, so websites will not load."
    steps.append(f"DNS works ({(time.time() - t0) * 1000:.0f} ms).")
    try:
        import httpx
        t0 = time.time()
        r = httpx.head(f"https://{NAME}", timeout=8)
        egress.record("skill:check_internet", f"https://{NAME}", method="HEAD", status=r.status_code,
                      privacy_mode=False)
        steps.append(f"Websites load (HTTP {r.status_code} in {(time.time() - t0) * 1000:.0f} ms).")
    except Exception as e:  # noqa: BLE001
        steps.append(f"But a website did not load ({e.__class__.__name__}).")
    return " ".join(steps)


SKILLS = [Skill(name="check_internet", schema=_SCHEMA, run=_run)]
