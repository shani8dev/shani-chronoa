"""Skill: firmware updates available through fwupd/LVFS - BIOS, SSDs, docks, touchpads.

Read-only: `fwupdmgr get-updates --json` against the metadata already
downloaded (fwupd-refresh.timer keeps it fresh on the images); installing is
left to the desktop's Software app or `fwupdmgr update`, because a firmware
flash can need a reboot and must not be interrupted.

**And what this machine has, not only what it could update.** "No firmware
updates are available" used to be the whole answer, which names nothing you
are running - so the same local metadata is read a second time through
`fwupdmgr get-devices --json` and the updatable devices are listed with their
current versions. That is where the BIOS version a support page asks for comes
from. Measured on a real machine: 27 devices, several with **no version at
all** (printed as "version unknown", never as a blank), and `fwupdmgr` can
answer with an empty device list rather than an error, which is reported as
undetermined and never as "this machine has no firmware".
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
                        "touchpad...) and what each fixes, plus the firmware versions this machine is "
                        "running. Read-only; nothing is installed."),
        "parameters": {"type": "object", "properties": {}},
    },
}


def get_devices() -> "tuple[list, str]":
    """`fwupdmgr get-devices --json`: the firmware this machine has.

    **Why this is part of the same answer.** "Are there firmware updates?"
    is half a question: the other half is what version the machine is on
    now, and that is the half `firmware_updates` could not answer - with no
    updates available it said only "No firmware updates are available for
    this machine's devices", which names nothing you have. `get-devices` is
    the same local metadata, read-only, and it is the only source for the
    BIOS version a support page asks for.

    Returns `(devices, reason)`; a non-empty reason means the list is not
    trustworthy. `fwupdmgr` can exit 0 having listed nothing when the daemon
    is not running, so an empty list is reported as undetermined rather than
    as "this machine has no firmware".
    """
    try:
        proc = subprocess.run(["fwupdmgr", "get-devices", "--json"],
                               capture_output=True, text=True, timeout=60, check=False)
    except subprocess.TimeoutExpired:
        return [], "fwupd did not answer within a minute, so the firmware list is UNKNOWN"
    text = (proc.stdout or "").strip()
    if not text:
        detail = (proc.stderr or "").strip().splitlines()
        return [], ("fwupd listed no devices"
                    + (f": {detail[0][:120]}" if detail else " (no output)")
                    + ", so which firmware this machine has is UNKNOWN")
    try:
        data = json.loads(text)
    except json.JSONDecodeError as exc:
        return [], f"fwupd's device list was not readable JSON ({exc})"
    devices = data.get("Devices") or []
    if not devices:
        return [], "fwupd listed no devices, so which firmware this machine has is UNKNOWN"
    return devices, ""


def _inventory_lines(devices: list) -> "list[str]":
    """The firmware worth naming: updatable devices first, each with its version.

    **Updatable before internal.** A BIOS or SSD firmware version is what a
    person or a support page asks for; the internal devices - SPI
    controllers, BootGuard configuration - are mostly not updatable and
    would bury the two lines that matter.

    **A missing version is `unknown`, never a blank or a guess.** fwupd
    reports several devices with no `Version` at all (measured on a real
    machine: 27 devices, some with `None`), and printing nothing after the
    colon reads as a formatting fault rather than as fwupd not knowing.
    """
    updatable, internal = [], 0
    for d in devices:
        flags = d.get("Flags") or []
        if "updatable" in flags:
            version = (d.get("Version") or "").strip()
            updatable.append((d.get("Name") or "unnamed device",
                              version or "version unknown"))
        else:
            internal += 1
    lines = []
    if updatable:
        lines.append(f"This machine's updatable firmware ({len(updatable)}):")
        for name, version in updatable[:12]:
            lines.append(f"- {name}: {version}")
        if len(updatable) > 12:
            lines.append(f"- ... and {len(updatable) - 12} more")
    else:
        lines.append("fwupd lists no updatable firmware on this machine.")
    if internal:
        lines.append(f"({internal} internal device(s) are not updatable and are not listed.)")
    return lines


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
        installed, why = get_devices()
        if installed:
            return ("No firmware updates are available for this machine's devices.\n"
                    + "\n".join(_inventory_lines(installed)))
        return ("No firmware updates are available for this machine's devices. "
                + why + ".")
    installed, _why = get_devices()
    # **The inventory is the machine's, not the update list's.** The devices
    # `get-updates` returns are only the ones with a pending update, so using
    # them here would have shown "this machine's updatable firmware" as the
    # subset that happens to be stale. When `get-devices` cannot answer, the
    # update list is still a real list of real devices, so it is used rather
    # than dropping the answer.
    return (f"{len(rows)} firmware update(s) available:\n" + "\n".join(rows) + "\n"
            + "\n".join(_inventory_lines(installed or devices))
            + "\nInstall them from the Software app (or `fwupdmgr update`); some need a reboot.")


SKILLS = [Skill(name="firmware_updates", schema=SCHEMA, run=_run)]
