"""A phone on the **same Wi-Fi**, without KDE Connect or GSConnect installed.

The two desktop integrations `phone.py` uses are not on a stock image - neither
KDE Connect nor the GSConnect extension - so the phone link works nowhere a
user has not already installed something. The KDE Connect protocol is open and
specified, and this repository already hand-writes two device protocols
(`phone_bluez.py` for PBAP/MAP/OBEX, `moyoung.py` for a closed vendor one), so
speaking it directly is in character.

`lan_link.py` is the module; this is the surface the model sees.

**What works, and what does not, in one place.** Discovery works and is tested
against a fake peer over real sockets. Pairing, the TLS upgrade and every plugin
packet after it are **not implemented and not claimed** - there is no KDE
Connect phone on any network this code has been able to run against, so they
could only be written from the spec and never executed. `pair` says exactly
that instead of half-pairing wrongly, which is the reason `bluetooth_call`'s
call events were left unbuilt rather than shipped unverified.
"""

from __future__ import annotations

import logging

from shani_chronoa import lan_link
from shani_chronoa.skills import Skill

logger = logging.getLogger(__name__)

_CONSENT_KEY = "lan-phone-enabled"

_ACTIONS = ("status", "devices", "announce", "pair")


def _consent(config) -> "tuple[bool, str]":
    """(allowed, why-not) - the house signature every gated tool returns."""
    if not config.get_bool(_CONSENT_KEY, False):
        return False, (f"Finding phones on this network is turned off. Nothing "
                       f"was sent. Enable '{_CONSENT_KEY}' in Settings to allow "
                       f"it.")
    return True, ""


def _hostname() -> str:
    import socket
    try:
        return socket.gethostname()
    except OSError:
        return "this computer"


def _run(arguments: dict):
    config = arguments.get("_config")
    if config is None:
        from shani_chronoa.config import ChronoaConfig
        config = ChronoaConfig()
    allowed, refusal = _consent(config)
    if not allowed:
        return refusal

    action = (arguments.get("action") or "status").strip().lower()
    if action not in _ACTIONS:
        return (f"action must be one of {', '.join(_ACTIONS)}; "
                f"{action!r} is not a phone-link action")

    if action == "pair":
        state, why = lan_link.pairing_status()
        return (f"Pairing is {state}: {why} Discovery is real - use 'devices' "
                f"to see what answers, and 'announce' to make this computer "
                f"findable.")

    if action == "status":
        state, _why = lan_link.pairing_status()
        return (f"This computer speaks the KDE Connect LAN protocol "
                f"(version {lan_link.PROTOCOL_VERSION}, UDP {lan_link.UDP_PORT}). "
                f"Devices can be listed and this computer can announce itself. "
                f"Pairing is {state}, so no phone can be *controlled* until that "
                f"is written - see 'pair'.")

    if action == "announce":
        did, why = lan_link.announce(_hostname())
        if did:
            note = (f" ({why})" if why else "")
            return (f"Announced this computer as '{_hostname()}' on the local "
                    f"network{note}. A phone running KDE Connect on the same "
                    f"Wi-Fi will now offer to pair.")
        return (f"Could not announce this computer ({why}). The network may not "
                f"carry broadcast.")

    # action == "devices"
    seconds = arguments.get("seconds")
    try:
        seconds = float(seconds) if seconds not in (None, "") else 5.0
    except (TypeError, ValueError):
        seconds = 5.0
    seconds = min(max(seconds, 1.0), 30.0)
    found = lan_link.listen_once(seconds)
    if not found:
        return (f"No device announced itself on the network in {seconds:.0f} "
                f"seconds. That is a real answer, not a failure: either nothing "
                f"is running the protocol here, or this network drops broadcast "
                f"(corporate and university Wi-Fi often do).")
    lines = [f"{len(found)} device(s) on the local network:"]
    for entry in found:
        lines.append(
            f"- {entry.get('deviceName') or '(no name)'} "
            f"[{entry.get('deviceType') or 'unknown type'}] "
            f"at {entry.get('address')}, protocol {entry.get('protocolVersion')}, "
            f"control port {entry.get('tcpPort', lan_link.UDP_PORT)}")
    lines.append("Pairing is not implemented yet, so these can be seen and not "
                 "used - see 'pair'.")
    return "\n".join(lines)


SCHEMA = {
    "type": "function",
    "function": {
        "name": "lan_phone",
        "description": (
            "Find phones on the SAME Wi-Fi network, using the KDE Connect "
            "protocol directly - so it works without KDE Connect or GSConnect "
            "installed. 'devices': what announced itself on this network in the "
            "last few seconds (phones, tablets, other computers). 'announce': "
            "make this computer findable by a phone's KDE Connect app. 'status': "
            "what this computer can and cannot do on the LAN. 'pair': what the "
            "pairing half of the protocol is and why it is not here. Needs the "
            "'lan-phone-enabled' consent key."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "action": {
                    "type": "string",
                    "enum": list(_ACTIONS),
                    "description": "devices: list what answers. announce: make "
                                   "this computer findable. status: what works "
                                   "here. pair: why pairing is not written. "
                                   "Defaults to status.",
                    "default": "status",
                },
                "seconds": {
                    "type": "number",
                    "description": "For devices: how long to listen, 1-30 "
                                   "(default 5). Devices announce on a timer, "
                                   "so a short listen can miss one.",
                },
            },
            "required": [],
        },
    },
}

SKILLS = [Skill(name="lan_phone", schema=SCHEMA, run=_run)]
