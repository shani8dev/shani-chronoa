"""Skill: Tailscale - is it up, this machine's tailnet address, and which
other devices are online. `tailscale status --json`; reading only."""

import json
import shutil
import subprocess

from shani_chronoa.skills import Skill

_SCHEMA = {
    "type": "function",
    "function": {
        "name": "tailscale_status",
        "description": "Tailscale status: whether it is connected, this machine's Tailscale IP, and which "
                       "of your other devices are online.",
        "parameters": {"type": "object", "properties": {}},
    },
}


def _run(_arguments: dict) -> str:
    if not shutil.which("tailscale"):
        return "Tailscale is not installed."
    r = subprocess.run(["tailscale", "status", "--json"], capture_output=True, text=True, timeout=15)
    try:
        d = json.loads(r.stdout)
    except ValueError:
        return "Tailscale is not running (tailscaled is stopped)."
    state = d.get("BackendState", "?")
    if state != "Running":
        return f"Tailscale is {state.lower()}" + (" - run 'tailscale up' to sign in." if state == "NeedsLogin" else ".")
    me = d.get("Self", {})
    peers = d.get("Peer", {}).values()
    online = [p.get("HostName") for p in peers if p.get("Online")]
    return (f"Tailscale is connected as {me.get('HostName')} ({', '.join(me.get('TailscaleIPs', [])[:1])}). "
            f"{len(online)} of {len(peers)} other device(s) online" + (f": {', '.join(online[:10])}." if online else "."))


SKILLS = [Skill(name="tailscale_status", schema=_SCHEMA, run=_run)]
