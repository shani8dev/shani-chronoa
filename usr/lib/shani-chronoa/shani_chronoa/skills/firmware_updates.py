"""Skill: firmware updates available through fwupd/LVFS - BIOS, SSDs, docks, touchpads.

Read-only: `fwupdmgr get-updates --json` against the metadata already
downloaded (fwupd-refresh.timer keeps it fresh on the images); installing is
left to the desktop's Software app or `fwupdmgr update`, because a firmware
flash can need a reboot and must not be interrupted.
"""

from __future__ import annotations

import json
import shutil
import subprocess

from shani_chronoa.skills import Skill

SCHEMA = {
    "type": "function",
    "function": {
        "name": "firmware_updates",
        "description": ("List firmware updates fwupd knows for this machine's devices (BIOS, SSD, dock, "
                        "touchpad...) and what each fixes. Read-only; nothing is installed."),
        "parameters": {"type": "object", "properties": {}},
    },
}


def _run(arguments: dict) -> str:
    if shutil.which("fwupdmgr") is None:
        return "fwupd is not installed, so firmware updates cannot be checked here."
    try:
        proc = subprocess.run(["fwupdmgr", "get-updates", "--json", "--no-unreported-check", "--no-metadata-check"],
                              capture_output=True, text=True, timeout=60, check=False)
    except subprocess.TimeoutExpired:
        return "fwupd did not answer within a minute."
    text = (proc.stdout or "").strip()
    try:
        data = json.loads(text) if text else {}
    except json.JSONDecodeError:
        msg = (proc.stderr or text).strip().splitlines()
        return f"fwupd could not list updates: {msg[0][:160] if msg else f'exit {proc.returncode}'}."
    devices = data.get("Devices", [])
    rows = []
    for d in devices:
        for r in d.get("Releases", [])[:1]:
            urgency = r.get("Urgency", "")
            rows.append(f"- {d.get('Name', '?')}: {d.get('Version', '?')} -> {r.get('Version', '?')}"
                        + (f" ({urgency} urgency)" if urgency and urgency != "unknown" else "")
                        + (f" - {r.get('Summary')}" if r.get("Summary") else ""))
    if not rows:
        return "No firmware updates are available for this machine's devices."
    return f"{len(rows)} firmware update(s) available:\n" + "\n".join(rows) + \
        "\nInstall them from the Software app (or `fwupdmgr update`); some need a reboot."


SKILLS = [Skill(name="firmware_updates", schema=SCHEMA, run=_run)]
