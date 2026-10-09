"""Skill: which speakers and microphones are there, and switch the default.

`volume` can make the current output louder; it cannot move sound from the
laptop speaker to the HDMI monitor or the USB headset. "Play through my
headphones" is one of the most common things a person asks a desktop, and
nothing here could do it. `wpctl` (wireplumber) is what both GNOME and Plasma
use underneath, and `wpctl set-default` is the switch.

Ungated, like `set_volume` and `set_mute`: it is a reversible change to the
user's own session, made at their request, and it is undone by asking again.

Honesty rules:

- A device is chosen by its id or by a name fragment, and a fragment that
  matches more than one device is **refused with the candidates listed**, never
  resolved by guessing - "HDMI" on a laptop with three HDMI outputs has no right
  answer the code could pick.
- The default is read back after switching (and again by `POST_CONDITION`);
  `wpctl set-default` exiting 0 and the default having moved are different claims.
"""

from __future__ import annotations

import re
import shutil
import subprocess

from shani_chronoa import files
from shani_chronoa.skills import Skill

_TIMEOUT = 10
_LINE = re.compile(r"^[\s│├└─]*?(\*)?\s*(\d+)\.\s+(.+?)(?:\s+\[vol:[^\]]*\])?\s*$")
_KINDS = {"output": "Sinks", "input": "Sources"}

SCHEMA = {
    "type": "function",
    "function": {
        "name": "audio_output",
        "description": (
            "List the speakers/headphones (outputs) and microphones (inputs) and "
            "which one is the default, or switch the default output or input - "
            "'play through my headphones', 'use the USB microphone', 'send sound "
            "to the HDMI monitor'. Pick a device by its number or part of its name."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "action": {"type": "string", "enum": ["list", "set"],
                           "description": "list (default) or set."},
                "device": {"type": "string",
                           "description": "set: the device's number, or part of its name."},
                "kind": {"type": "string", "enum": ["output", "input"],
                         "description": "set: output (default) or input."},
            },
        },
    },
}


def parse_status(stdout: str) -> "dict[str, list[dict]]":
    """{'Devices': [...], 'Sinks': [...], 'Sources': [...]} from `wpctl status`.

    `Devices` was added for `bluetooth_call`, which needs the *cards* - the
    Bluetooth ones carry the `Profile 0: hfp_hf` lines that decide where a
    call's voice goes, and no sink or source line has one. The section state
    machine below already walked past them.
    """
    out = {"Devices": [], "Sinks": [], "Sources": []}
    in_audio, section = False, None
    for raw in stdout.splitlines():
        if raw and not raw[0].isspace() and raw[0] not in "│├└":
            in_audio = raw.strip() == "Audio"
            section = None
            continue
        if not in_audio:
            continue
        header = raw.strip(" │├└─")
        if header.endswith(":"):
            name = header[:-1].strip()
            section = name if name in out else None
            continue
        if section is None:
            continue
        m = _LINE.match(raw)
        if m:
            out[section].append({"id": int(m.group(2)), "name": m.group(3).strip(),
                                 "default": bool(m.group(1))})
    return out


def read_devices() -> "dict | None":
    if shutil.which("wpctl") is None:
        return None
    try:
        proc = subprocess.run(["wpctl", "status"], capture_output=True, text=True,
                              timeout=_TIMEOUT, check=False)
    except (subprocess.TimeoutExpired, OSError):
        return None
    if proc.returncode != 0:
        return None
    return parse_status(proc.stdout)


def pick(devices: "list[dict]", wanted: str) -> "tuple[dict | None, list[dict]]":
    """(the one match, or None; every candidate that matched)."""
    wanted = wanted.strip()
    if wanted.isdigit():
        hits = [d for d in devices if d["id"] == int(wanted)]
    else:
        hits = [d for d in devices if wanted.lower() in d["name"].lower()]
    return (hits[0] if len(hits) == 1 else None), hits


def _kind(arguments: dict) -> str:
    kind = (arguments.get("kind") or "output").strip().lower()
    return kind if kind in _KINDS else "output"


def _listing(devs: dict) -> str:
    lines = []
    for kind, section in _KINDS.items():
        rows = devs[section]
        label = "Outputs (speakers, headphones)" if kind == "output" else "Inputs (microphones)"
        lines.append(f"{label}:" if rows else f"{label}: none found.")
        for d in rows:
            lines.append(f"  {'*' if d['default'] else ' '} {d['id']:>4}  {d['name']}")
    lines.append("* = the default.")
    return "\n".join(lines)


def _run(arguments: dict) -> str:
    if shutil.which("wpctl") is None:
        return files.tool_missing("wpctl", "list or switch audio devices")
    devs = read_devices()
    if devs is None:
        return ("wpctl could not read the audio graph (is PipeWire running in this "
                "session?), so the audio devices are UNKNOWN.")
    action = (arguments.get("action") or "list").strip().lower()
    if action == "list":
        return _listing(devs)
    if action != "set":
        return f"Action must be list or set, not {action!r}."
    kind = _kind(arguments)
    rows = devs[_KINDS[kind]]
    wanted = (arguments.get("device") or "").strip()
    if not wanted:
        return f"No device was named, so there is nothing to switch to.\n{_listing(devs)}"
    chosen, hits = pick(rows, wanted)
    if chosen is None:
        if not hits:
            return f"No {kind} matches {wanted!r}, so nothing was changed.\n{_listing(devs)}"
        names = "; ".join(f"{d['id']} {d['name']}" for d in hits)
        return (f"{wanted!r} matches {len(hits)} {kind}s ({names}), so nothing was "
                f"changed. Name it more exactly, or by number.")
    if chosen["default"]:
        return f"{chosen['name']} is already the default {kind}, so nothing was changed."
    try:
        proc = subprocess.run(["wpctl", "set-default", str(chosen["id"])], capture_output=True,
                              text=True, timeout=_TIMEOUT, check=False)
    except (subprocess.TimeoutExpired, OSError) as exc:
        return f"wpctl could not switch the default ({exc}); nothing is confirmed to have changed."
    if proc.returncode != 0:
        detail = (proc.stderr or proc.stdout or "").strip()
        return f"wpctl refused to switch to {chosen['name']}: {detail or 'no detail'}."
    after = read_devices()
    now = next((d for d in (after or {}).get(_KINDS[kind], []) if d["default"]), None)
    if now and now["id"] == chosen["id"]:
        return f"The default {kind} is now {chosen['name']} (verified by reading it back)."
    return (f"wpctl accepted the switch, but the default {kind} reads back as "
            f"{now['name'] if now else 'UNKNOWN'}, so this is not verified.")


def _post_condition(arguments: dict):
    if (arguments.get("action") or "list").strip().lower() != "set":
        return None
    wanted = (arguments.get("device") or "").strip()
    devs = read_devices()
    if not wanted or devs is None:
        return None
    rows = devs[_KINDS[_kind(arguments)]]
    chosen, _ = pick(rows, wanted)
    if chosen is None:
        return None
    return chosen["default"], f"default {_kind(arguments)} is {'' if chosen['default'] else 'not '}{chosen['name']}"


POST_CONDITION = _post_condition

SKILLS = [Skill(name="audio_output", schema=SCHEMA, run=_run)]
