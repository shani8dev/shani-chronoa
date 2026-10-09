"""Skill: turn the Wi-Fi radio on or off on its own, or say whether it is on.

`airplane_mode` switches *every* radio and `connect_wifi` joins or leaves a
network; nothing switched just the Wi-Fi radio, which is the everyday request
("turn off wifi", "is wifi on?").

NetworkManager first (`nmcli radio wifi on|off`), because on a desktop that
runs it, NetworkManager owns the radio: switching underneath it with rfkill
works, but NetworkManager then reports a state it did not choose. Without
NetworkManager, `rfkill block|unblock wlan` (util-linux) - the same tool and
the same logind device access `airplane_mode` relies on.

Gated by `radio-control-enabled`, the key `airplane_mode` already uses, off by
default: this is a subset of what that key already permits (switching radios),
and cutting Wi-Fi mid-call is not a thing to do unasked. Reporting is free.

Honesty rules:

- The state is **read back** after the change, from the same source that was
  asked to change it; a command that exits 0 is not a radio that is off.
- A *hard* block (a physical switch or a BIOS setting) cannot be lifted from
  software and is reported as such, never as "on".
"""

from __future__ import annotations

import shutil
import subprocess

from shani_chronoa.config import ChronoaConfig
from shani_chronoa.skills import Skill
from shani_chronoa.skills import airplane_mode as _air

_CONSENT_KEY = "radio-control-enabled"
_TIMEOUT = 15
_ACTIONS = ("status", "on", "off")

SCHEMA = {
    "type": "function",
    "function": {
        "name": "toggle_wifi",
        "description": ("Turn the Wi-Fi radio on or off (only Wi-Fi - airplane_mode switches every radio, "
                        "connect_wifi joins a network), or report whether Wi-Fi is on. Changing requires "
                        "the 'radio-control-enabled' consent key; status needs no permission."),
        "parameters": {"type": "object", "properties": {
            "action": {"type": "string", "enum": list(_ACTIONS),
                       "description": "status (default), on, or off."}}},
    },
}


def _consent(config: ChronoaConfig) -> "tuple[bool, str]":
    if not config.get_bool(_CONSENT_KEY, False):
        return False, (f"switching radios is turned off (enable '{_CONSENT_KEY}' in Settings). "
                       f"Asking whether Wi-Fi is on needs no such permission - only switching it does")
    return True, ""


def _wlan() -> "list[dict]":
    return [r for r in _air.radios() if r["type"] == "wlan"]


def _nmcli(*args: str):
    try:
        return subprocess.run(["nmcli", *args], capture_output=True, text=True,
                              timeout=_TIMEOUT, check=False)
    except (subprocess.TimeoutExpired, OSError):
        return None


def _nm_state() -> str:
    """'on', 'off' or 'unknown' as NetworkManager reports it."""
    if shutil.which("nmcli") is None:
        return "unknown"
    proc = _nmcli("radio", "wifi")
    if proc is None or proc.returncode != 0:
        return "unknown"
    word = (proc.stdout or "").strip().lower()
    return {"enabled": "on", "disabled": "off"}.get(word, "unknown")


def _rfkill_state(rs: "list[dict]") -> str:
    if not rs:
        return "unknown"
    if all(r["hard"] for r in rs):
        return "hard-blocked"
    return "off" if all(r["soft"] or r["hard"] for r in rs) else "on"


def state() -> "tuple[str, str]":
    """(state, source): state is on/off/hard-blocked/unknown."""
    rs = _wlan()
    if rs and all(r["hard"] for r in rs):
        return "hard-blocked", "rfkill"
    nm = _nm_state()
    if nm != "unknown":
        return nm, "NetworkManager"
    return _rfkill_state(rs), "rfkill"


def _hard_note(rs: "list[dict]") -> str:
    hard = [r["name"] for r in rs if r["hard"]]
    return (f" The Wi-Fi radio ({', '.join(hard)}) is blocked by a hardware switch or the BIOS, "
            f"which software cannot undo.") if hard else ""


def _run(arguments: dict) -> str:
    if not isinstance(arguments, dict):
        return "toggle_wifi needs an action: status, on or off."
    raw = arguments.get("action") or "status"
    if not isinstance(raw, str):
        return f"action must be status, on or off, not {raw!r}."
    action = raw.strip().lower()
    if action not in _ACTIONS:
        return f"action must be status, on or off, not {action!r}."

    rs = _wlan()
    current, source = state()
    if action == "status":
        if current == "unknown":
            return ("Whether Wi-Fi is on could not be read: NetworkManager did not answer and the "
                    "kernel reports no Wi-Fi radio.")
        if current == "hard-blocked":
            return "Wi-Fi is off." + _hard_note(rs)
        return f"Wi-Fi is {current} (according to {source})." + _hard_note(rs)

    allowed, reason = _consent(ChronoaConfig())
    if not allowed:
        return f"Refusing to switch Wi-Fi: {reason}."
    if action == "on" and current == "hard-blocked":
        return "Wi-Fi cannot be turned on from software." + _hard_note(rs)
    if current == action:
        return f"Wi-Fi is already {action}, so nothing was changed."

    if shutil.which("nmcli") is not None and _nm_state() != "unknown":
        proc = _nmcli("radio", "wifi", action)
        tool = "nmcli"
        if proc is None:
            return f"NetworkManager did not answer within {_TIMEOUT}s, so whether Wi-Fi is now {action} is UNKNOWN."
        after = _nm_state()
    elif shutil.which("rfkill") is not None and rs:
        tool = "rfkill"
        try:
            proc = subprocess.run(["rfkill", "block" if action == "off" else "unblock", "wlan"],
                                  capture_output=True, text=True, timeout=_TIMEOUT, check=False)
        except (subprocess.TimeoutExpired, OSError) as exc:
            return f"rfkill could not be run ({exc}), so Wi-Fi was not switched."
        after = _rfkill_state(_wlan())
    elif not rs:
        return ("Neither NetworkManager nor the kernel reports a Wi-Fi radio on this machine, so "
                "there is nothing to switch.")
    else:
        return ("NetworkManager is not answering and rfkill (util-linux) is not installed, so Wi-Fi "
                "cannot be switched from here.")

    if proc.returncode != 0:
        return f"{tool} refused to turn Wi-Fi {action}: {(proc.stderr or proc.stdout).strip()[:160] or 'no message'}."
    if after == action:
        return f"Wi-Fi is now {action} (verified with {tool})."
    if after == "unknown":
        return (f"{tool} accepted turning Wi-Fi {action}, but reading the state back did not work, "
                f"so this is not verified.")
    return f"Asked {tool} to turn Wi-Fi {action}, but it still reads {after}." + _hard_note(_wlan())


def _post_condition(arguments: dict):
    action = (arguments.get("action") or "status").strip().lower() if isinstance(arguments, dict) else ""
    if action not in ("on", "off"):
        return None
    current, source = state()
    if current == "unknown":
        return None
    return current == action, f"Wi-Fi reads {current} ({source})"


POST_CONDITION = _post_condition

SKILLS = [Skill(name="toggle_wifi", schema=SCHEMA, run=_run)]
