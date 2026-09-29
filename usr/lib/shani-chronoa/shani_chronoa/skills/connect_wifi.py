"""Skill: join, leave or switch the machine's WiFi network.

Gated, and it is the highest-consequence action in this group: it changes what
the machine can reach, and it does so in response to an instruction that may
have been misheard. A spoken "connect to the guest network" on a machine with a
saved work network is a real failure, and one that is not obvious from looking
at the screen afterwards.

Gated on its own `wifi-connect-enabled` key rather than reusing the `network`
sense, because listing networks and joining one are different acts. Scanning is
passive; joining attaches this machine to a network.

It never handles a password. A password typed into a chat, spoken aloud, or
passed through an MCP client ends up in a transcript, and no convenience is
worth that. A network that needs a passphrase is reported as needing one, and
the user joins it themselves.
"""

from __future__ import annotations

import shutil
import subprocess

from shani_chronoa import files
from shani_chronoa.config import ChronoaConfig
from shani_chronoa.skills import Skill

_CONSENT_KEY = "wifi-connect-enabled"
_TIMEOUT = 40

SCHEMA = {
    "type": "function",
    "function": {
        "name": "connect_wifi",
        "description": (
            "Join a WiFi network by name, or leave the current one. Open "
            "networks only - this never handles a password, because a password "
            "passed through a chat or an MCP client is in a transcript. "
            "Requires the 'wifi-connect-enabled' consent key."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "ssid": {
                    "type": "string",
                    "description": "Network name to join. Omit to disconnect instead.",
                },
            },
        },
    },
}


def _consent(config: ChronoaConfig) -> tuple[bool, str]:
    """Return (allowed, reason). Fail-closed: an undeclared key denies."""
    if not config.get_bool(_CONSENT_KEY, False):
        return False, (
            f"changing WiFi connections is turned off (enable '{_CONSENT_KEY}' "
            f"in Settings). Listing nearby networks needs no such permission - "
            f"only joining or leaving one does."
        )
    return True, ""


def _run(arguments: dict) -> str:
    allowed, reason = _consent(ChronoaConfig())
    if not allowed:
        return f"Refusing to change the WiFi connection: {reason}"
    if shutil.which("nmcli") is None:
        return files.tool_missing("nmcli", "change the WiFi connection")

    # No `--ifname` here, deliberately. An earlier version passed "wlan0",
    # which is a per-machine guess dressed as a constant: this machine's radio
    # is wlp0s20f3, so that build could never join anything and reported the
    # interface as wrong rather than saying so. nmcli already picks the wifi
    # device, and a machine may have more than one.
    ssid = (arguments.get("ssid") or "").strip()
    if not ssid:
        # `nmcli -t -f STATE network` is not a valid query - for `nmcli network`
        # the only allowed field is NETWORKING, and `STATE` belongs to
        # `nmcli connection`. The invalid form failed with a message on stderr
        # and empty stdout, so the first version of this branch read that as
        # "not connected" and reported there was nothing to leave on a machine
        # that was very much connected.
        try:
            active = subprocess.run(
                ["nmcli", "-t", "-f", "NAME,TYPE", "connection", "show", "--active"],
                capture_output=True, text=True, timeout=_TIMEOUT, check=False,
            )
        except (subprocess.TimeoutExpired, OSError) as exc:
            return f"Could not read the current connection: {exc}"
        if active.returncode != 0:
            detail = (active.stderr or "").strip().splitlines()
            return "Could not read which connection is active" + (
                f": {detail[-1]}" if detail else ".")
        live = [line for line in active.stdout.splitlines() if line.strip()]
        if not live:
            return "Not connected to anything, so there was nothing to leave."
        try:
            down = subprocess.run(["nmcli", "connection", "down"], capture_output=True,
                                  text=True, timeout=_TIMEOUT, check=False)
        except (subprocess.TimeoutExpired, OSError) as exc:
            return f"Could not disconnect: {exc}"
        if down.returncode != 0:
            detail = (down.stderr or "").strip().splitlines()
            return f"Could not disconnect (exit {down.returncode})" + (
                f": {detail[-1]}" if detail else ".")
        return (
            f"Disconnected. Was on: {', '.join(line.split(':')[0] for line in live[:4])}"
            + (f" and {len(live) - 4} more" if len(live) > 4 else "") + "."
        )

    if len(ssid) > 32:
        return f"A WiFi network name is at most 32 characters; this one is {len(ssid)}."
    try:
        proc = subprocess.run(
            ["nmcli", "device", "wifi", "connect", ssid],
            capture_output=True, text=True, timeout=_TIMEOUT, check=False,
        )
    except subprocess.TimeoutExpired:
        return f"Joining {ssid!r} did not finish within {_TIMEOUT}s, so whether it worked is unknown."
    except OSError as exc:
        return f"Could not join {ssid!r}: {exc}"

    combined = (proc.stdout + proc.stderr).lower()
    if proc.returncode == 0:
        return f"Joined {ssid!r}."
    if "secrets" in combined or "password" in combined or "802.1x" in combined:
        return (
            f"{ssid!r} needs a passphrase, and this skill will not handle one: a "
            f"password passed through a chat or an MCP client is in a transcript. "
            f"Join it yourself from the desktop's network menu."
        )
    detail = (proc.stderr or proc.stdout or "").strip().splitlines()
    return f"Could not join {ssid!r} (exit {proc.returncode})" + (
        f": {detail[-1]}" if detail else ". It may be out of range or already saved with another password."
    )


SKILLS = [Skill(name="connect_wifi", schema=SCHEMA, run=_run)]
