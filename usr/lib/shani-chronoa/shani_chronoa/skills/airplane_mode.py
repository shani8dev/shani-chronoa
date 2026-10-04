"""Skill: airplane mode - which radios are on, and switch them all off or back on.

Read straight from the kernel (/sys/class/rfkill), so status needs no tool and
no permission. Switching uses `rfkill` (util-linux), which a desktop user may
run thanks to logind's device access; it is gated by `radio-control-enabled`,
off by default, because cutting Wi-Fi mid-call is not a thing to do unasked.
A radio with a *hard* block (a physical switch or a BIOS setting) cannot be
turned on from software, and is reported as such rather than as "on".
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

from shani_chronoa.config import ChronoaConfig
from shani_chronoa.skills import Skill

_CONSENT_KEY = "radio-control-enabled"
RFKILL_DIR = Path("/sys/class/rfkill")

SCHEMA = {
    "type": "function",
    "function": {
        "name": "airplane_mode",
        "description": ("Report which radios (Wi-Fi, Bluetooth, mobile broadband) are on, or turn airplane "
                        "mode on (all radios off) or off. Changing requires the 'radio-control-enabled' "
                        "consent key."),
        "parameters": {"type": "object", "properties": {
            "action": {"type": "string", "enum": ["status", "on", "off"],
                       "description": "status, on (radios off), or off (radios back on)."}}},
    },
}


def _consent(config: ChronoaConfig) -> "tuple[bool, str]":
    if not config.get_bool(_CONSENT_KEY, False):
        return False, f"switching radios is turned off (enable '{_CONSENT_KEY}' in Settings)"
    return True, ""


def radios() -> "list[dict]":
    out = []
    for d in sorted(RFKILL_DIR.glob("rfkill*")) if RFKILL_DIR.is_dir() else []:
        def read(name):
            try:
                return (d / name).read_text().strip()
            except OSError:
                return ""
        out.append({"type": read("type"), "name": read("name"), "soft": read("soft") == "1",
                    "hard": read("hard") == "1"})
    return out


def _describe(rs: "list[dict]") -> str:
    labels = {"wlan": "Wi-Fi", "bluetooth": "Bluetooth", "wwan": "mobile broadband", "nfc": "NFC", "uwb": "UWB",
              "gps": "GPS", "fm": "FM"}
    parts = []
    for r in rs:
        state = "blocked by a switch or the BIOS" if r["hard"] else ("off" if r["soft"] else "on")
        parts.append(f"{labels.get(r['type'], r['type'])} ({r['name']}): {state}")
    return "; ".join(parts)


def _run(arguments: dict) -> str:
    action = (arguments.get("action") or "status").strip().lower()
    if action not in ("status", "on", "off"):
        return f"action must be status, on or off, not {action!r}."
    rs = radios()
    if not rs:
        return "This machine reports no radios to the kernel's rfkill interface."
    if action == "status":
        all_off = all(r["soft"] or r["hard"] for r in rs)
        return f"Airplane mode is {'on' if all_off else 'off'}. " + _describe(rs) + "."
    allowed, reason = _consent(ChronoaConfig())
    if not allowed:
        return f"Refusing to switch radios: {reason}."
    if shutil.which("rfkill") is None:
        return "rfkill (util-linux) is not installed, so the radios cannot be switched from here."
    proc = subprocess.run(["rfkill", "block" if action == "on" else "unblock", "all"], capture_output=True,
                          text=True, timeout=10, check=False)
    if proc.returncode != 0:
        return f"rfkill refused: {(proc.stderr or proc.stdout).strip()[:160]}"
    after = radios()
    want = action == "on"
    ok = all((r["soft"] or r["hard"]) == want or (not want and r["hard"]) for r in after)
    return (f"Airplane mode is now {'on' if want else 'off'}" + (" (verified)" if ok else " - but not every radio "
            "reads back as asked") + ". " + _describe(after) + ".")


def _post_condition(arguments: dict):
    action = (arguments.get("action") or "status").strip().lower()
    if action not in ("on", "off"):
        return None
    rs = radios()
    want = action == "on"
    return all((r["soft"] or r["hard"]) == want or (not want and r["hard"]) for r in rs), _describe(rs)


POST_CONDITION = _post_condition

SKILLS = [Skill(name="airplane_mode", schema=SCHEMA, run=_run)]
