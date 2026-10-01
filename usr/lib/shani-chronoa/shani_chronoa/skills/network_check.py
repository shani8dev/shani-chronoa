"""Skill: is the internet working, and if not, where does it stop?

Walks the path the way a person troubleshooting would, and stops at the
first step that fails: is there a network connection at all (the default
route), does the router answer (ping the gateway), is the internet reachable
(ping a public address), does DNS work (resolve a name), and does a website
load. Each answer names the step, so "the router answers but DNS fails" is
something a person can act on.

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
