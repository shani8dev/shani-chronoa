"""Skill: can this machine roll back, and which slot is it running?

Shanios is a btrfs blue-green system. `shani-deploy` installs an update into
the other slot and switches the default, so "can I undo the last update?" has a
real, checkable answer: which slot was booted, which slot boots next, which one
was in use before, and whether a boot failure or a pending reboot is on record.

Two sources, each reported for what it is:

- `shani-deploy --status --json` - the deploy tool's own read-only contract
  (no root, no lock; `status_json()` in shani-deploy documents every field).
  Every recovery field there is read from a marker another part of the system
  really writes, so this skill quotes them rather than re-deriving them.
- the `snapshots` sense (btrfs subvolumes), which answers the filesystem half.

The snapshot half follows the `snapshots` sense's own switch (on by default),
as every sense-backed skill here does - see `sense_reading.py`. The
shani-deploy half needs no switch: it is the deploy tool's documented,
unprivileged status contract.

Honesty rules: a missing `shani-deploy` is "not a Shanios deploy layout, or the
tool is absent", never "no rollback possible"; an empty `booted_slot` is
UNKNOWN, as shani-deploy itself documents.
"""

from __future__ import annotations

import json
import shutil
import subprocess

from shani_chronoa import sense_reading
from shani_chronoa.config import ChronoaConfig
from shani_chronoa.skills import Skill

_TIMEOUT = 20

SCHEMA = {
    "type": "function",
    "function": {
        "name": "snapshot_status",
        "description": (
            "Whether this Shanios machine can roll back: the OS version, which "
            "blue/green slot was booted, which slot boots next, the previous "
            "slot a rollback would return to, any recorded boot failure or "
            "pending reboot, and the btrfs snapshots on disk. Use for 'can I "
            "undo the update', 'which slot am I on', 'is an update waiting'. "
            "The snapshot list uses the 'snapshots-sense-enabled' switch. Read-only."
        ),
        "parameters": {"type": "object", "properties": {}},
    },
}


def _consent(config: ChronoaConfig) -> "tuple[bool, str]":
    """This skill's sense half follows the snapshots sense's switch (see sense_reading.py)."""
    if config.sense_allowed("snapshots"):
        return True, ""
    return False, sense_reading.refusal(config, "snapshots")


def deploy_status() -> "tuple[dict | None, str]":
    """(status document, reason it is absent)."""
    if shutil.which("shani-deploy") is None:
        return None, ("shani-deploy is not installed, so the blue/green slot "
                      "state is UNKNOWN - this may not be a Shanios install.")
    try:
        proc = subprocess.run(["shani-deploy", "--status", "--json"],
                              capture_output=True, text=True,
                              timeout=_TIMEOUT, check=False)
    except subprocess.TimeoutExpired:
        return None, f"shani-deploy did not answer within {_TIMEOUT}s, so the slot state is UNKNOWN."
    except OSError as exc:
        return None, f"shani-deploy could not be run ({exc}), so the slot state is UNKNOWN."
    if proc.returncode != 0:
        detail = (proc.stderr or proc.stdout or "").strip().splitlines()
        return None, ("shani-deploy --status --json failed"
                      + (f": {detail[-1]}" if detail else f" (exit {proc.returncode})")
                      + ", so the slot state is UNKNOWN.")
    for line in reversed(proc.stdout.splitlines()):
        line = line.strip()
        if line.startswith("{"):
            try:
                doc = json.loads(line)
            except ValueError:
                break
            if isinstance(doc, dict):
                return doc, ""
    return None, "shani-deploy printed no status document I could read, so the slot state is UNKNOWN."


def _slot(value) -> str:
    value = str(value or "").strip()
    return f"@{value}" if value else "UNKNOWN"


def describe_deploy(doc: dict) -> "list[str]":
    lines = []
    version = doc.get("version") or "UNKNOWN"
    profile = doc.get("profile") or "unknown profile"
    channel = doc.get("channel") or "unknown"
    lines.append(f"Shanios version {version} ({profile}, {channel} channel).")
    booted, current, previous = (doc.get("booted_slot") or "", doc.get("current_slot") or "",
                                 doc.get("previous_slot") or "")
    lines.append(f"Running from slot {_slot(booted)}; the next boot uses {_slot(current)}.")
    if booted and current and booted != current:
        if doc.get("candidate_boot") is True:
            lines.append(f"An update is installed in {_slot(current)} and takes effect on the next reboot.")
        else:
            lines.append(
                "The running slot differs from the default, and no finished update is "
                "pending - this usually means the bootloader fell back after a failed "
                "boot, or a rollback already switched the default.")
    if previous:
        lines.append(f"Rolling back would return to {_slot(previous)}.")
    else:
        lines.append("No previous slot is recorded, so what a rollback would return to is UNKNOWN.")
    hard, soft = doc.get("boot_hard_failure") or "", doc.get("boot_failure") or ""
    if hard:
        lines.append(f"A hard boot failure is recorded for {_slot(hard)} (the initramfs could not mount it).")
    elif soft:
        lines.append(f"A boot failure is recorded for {_slot(soft)}.")
    else:
        lines.append("No boot failure is recorded.")
    if doc.get("auto_rollback_done") is True:
        lines.append("Automatic rollback was already attempted this boot; the outcome is in the journal.")
    if doc.get("reboot_needed"):
        lines.append(f"Version {doc['reboot_needed']} is waiting for a reboot.")
    update = doc.get("update_available")
    if update is True:
        lines.append("A newer version is available on this channel.")
    return lines


def _run(_arguments: dict) -> str:
    doc, why = deploy_status()
    lines = describe_deploy(doc) if doc else [why]
    lines.append("")
    lines.append("Filesystem snapshots:")
    config = ChronoaConfig()
    allowed, why = _consent(config)
    lines.append(sense_reading.reading("snapshots") if allowed else why)
    return "\n".join(lines)


SKILLS = [Skill(name="snapshot_status", schema=SCHEMA, run=_run)]
