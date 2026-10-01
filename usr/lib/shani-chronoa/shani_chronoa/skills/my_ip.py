"""Skill: this computer's IP addresses - its local ones always, its public
one only with the web sense on (finding it means asking a server outside)."""

import json
import subprocess

from shani_chronoa.config import ChronoaConfig
from shani_chronoa.skills import Skill

PUBLIC = "https://api.ipify.org"

_SCHEMA = {
    "type": "function",
    "function": {
        "name": "my_ip_address",
        "description": "This computer's IP addresses: local (LAN) addresses per network interface, "
                       "and the public internet address when the web sense is on.",
        "parameters": {"type": "object", "properties": {}},
    },
}


def local_addresses() -> list:
    try:
        r = subprocess.run(["ip", "-j", "addr"], capture_output=True, text=True, timeout=5)
        data = json.loads(r.stdout or "[]")
    except (OSError, ValueError, subprocess.TimeoutExpired):
        return []
    out = []
    for iface in data:
        if iface.get("ifname") == "lo" or "UP" not in iface.get("flags", []):
            continue
        for a in iface.get("addr_info", []):
            if a.get("scope") == "global":
                out.append(f"{iface['ifname']}: {a['local']}")
    return out


def _run(_arguments: dict) -> str:
    local = local_addresses()
    parts = [("Local: " + ", ".join(local)) if local else "No local address: not connected."]
    config = ChronoaConfig()
    if config.sense_allowed("web") and local:
        from shani_chronoa.netjson import get_json
        try:
            parts.append(f"Public: {get_json('skill:my_ip_address', PUBLIC, {'format': 'json'}).get('ip')}.")
        except Exception as e:  # noqa: BLE001
            parts.append(f"The public address could not be found ({e.__class__.__name__}).")
    elif local:
        parts.append("The public address needs the web sense, so I did not look it up.")
    return " ".join(parts)


SKILLS = [Skill(name="my_ip_address", schema=_SCHEMA, run=_run)]
