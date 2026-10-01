"""Skill: VPN connections saved in NetworkManager - list, connect, disconnect.
nmcli, as Settings does it; behind the same consent as changing Wi-Fi
(wifi-connect-enabled), since it changes where traffic goes."""

import shutil
import subprocess

from shani_chronoa.config import ChronoaConfig
from shani_chronoa.skills import Skill

_CONSENT_KEY = "wifi-connect-enabled"
_VPN_TYPES = ("vpn", "wireguard")

_SCHEMA = {
    "type": "function",
    "function": {
        "name": "vpn_control",
        "description": "List saved VPN connections and which is active, or connect / disconnect one by "
                       "name. Connecting needs the 'wifi-connect-enabled' consent key.",
        "parameters": {"type": "object", "properties": {
            "action": {"type": "string", "enum": ["list", "up", "down"]},
            "name": {"type": "string"}}, "required": ["action"]},
    },
}


def vpns() -> list:
    r = subprocess.run(["nmcli", "-t", "-f", "NAME,TYPE,ACTIVE", "connection", "show"],
                       capture_output=True, text=True, timeout=10)
    out = []
    for line in r.stdout.splitlines():
        parts = line.rsplit(":", 2)
        if len(parts) == 3 and parts[1] in _VPN_TYPES:
            out.append((parts[0].replace("\\:", ":"), parts[2] == "yes"))
    return out


def _consent(config: ChronoaConfig) -> "tuple[bool, str]":
    if not config.get_bool(_CONSENT_KEY, False):
        return False, (f"Not done: changing connections is switched off ('{_CONSENT_KEY}'). Turn on "
                       "'Let Chronoa change WiFi' in Settings to allow it.")
    return True, ""


def _run(arguments: dict) -> str:
    if not shutil.which("nmcli"):
        return "NetworkManager's nmcli is not available."
    found = vpns()
    action = arguments.get("action")
    if action == "list":
        return ("VPNs: " + "; ".join(f"{n} ({'connected' if a else 'off'})" for n, a in found) + ".") if found \
            else "No VPN connections are saved. Add one in Settings > Network > VPN."
    allowed, reason = _consent(ChronoaConfig())
    if not allowed:
        return reason
    want = (arguments.get("name") or "").strip().lower()
    match = [n for n, _a in found if want and want in n.lower()] or ([found[0][0]] if len(found) == 1 else [])
    if len(match) != 1:
        return f"Which VPN? Saved: {', '.join(n for n, _a in found) or 'none'}."
    r = subprocess.run(["nmcli", "connection", action, "id", match[0]], capture_output=True, text=True, timeout=60)
    return (f"VPN {match[0]} {'connected' if action == 'up' else 'disconnected'}." if r.returncode == 0
            else f"Could not change {match[0]}: {r.stderr.strip()[:160]}")


SKILLS = [Skill(name="vpn_control", schema=_SCHEMA, run=_run)]
