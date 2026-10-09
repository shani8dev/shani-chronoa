"""Skill: mount or unmount a filesystem.

Gated, and this is the action with the widest blast radius in the project after
deleting files. Mounting runs code from a device that was not there a moment ago
- that is what a filesystem *is* - and unmounting something in use can lose
work or take a running service down.

Mounting is done through the desktop's own `udisksctl` where available, because
that is the path which applies the same polkit rules the GUI does. Falling back
to a bare `mount` would bypass the authorisation that makes this safe, so it
does not: if udisks is absent, the answer says so rather than reaching for root.

Honesty rules:

- **A successful `mount` is verified by reading the mount table back**, because
  a command exiting 0 and a filesystem actually being mounted are different
  claims, and the second is the one that matters.
- Unmounting something busy reports that it is busy, from the real error, rather
  than claiming success.
- The target is always printed with its device, because "mounted" without saying
  what is mounted where is not an answer.
"""

from __future__ import annotations

import shutil
import subprocess

from shani_chronoa import files
from shani_chronoa.config import ChronoaConfig
from shani_chronoa.skills import Skill

_CONSENT_KEY = "mount-control-enabled"
_TIMEOUT = 40

SCHEMA = {
    "type": "function",
    "function": {
        "name": "manage_mount",
        "description": (
            "Mount an unmounted filesystem or drive, or unmount one, through "
            "the desktop's own disk service so its authorisation rules apply. "
            "Verifies the result against the mount table. Requires the "
            "'mount-control-enabled' consent key."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "action": {
                    "type": "string",
                    "description": "'mount', 'unmount' or 'list'. Defaults to list.",
                },
                "device": {
                    "type": "string",
                    "description": (
                        "The device to mount, e.g. /dev/sdb1, or the mount "
                        "point to unmount. Omit for list."
                    ),
                },
            },
        },
    },
}


def _consent(config: ChronoaConfig) -> "tuple[bool, str]":
    """Return (allowed, reason). Fail-closed: an undeclared key denies."""
    if not config.get_bool(_CONSENT_KEY, False):
        return False, (
            f"changing mounts is turned off (enable '{_CONSENT_KEY}' in "
            f"Settings). Listing what is mounted needs no such permission - only "
            f"mounting or unmounting does, because mounting runs code from a "
            f"device that was not there a moment ago."
        )
    return True, ""


def _udisks() -> str:
    if shutil.which("udisksctl") is None:
        return ""
    try:
        proc = subprocess.run(["udisksctl", "status"], capture_output=True,
                              text=True, timeout=_TIMEOUT, check=False)
    except (subprocess.TimeoutExpired, OSError):
        return ""
    return proc.stdout or ""


def _run(arguments: dict) -> str:
    action = (arguments.get("action") or "list").strip().lower()
    if action not in ("mount", "unmount", "list"):
        return f"Action must be mount, unmount or list, not {action!r}."

    if action == "list":
        from shani_chronoa.senses.filesystems import read_mounts
        rows = read_mounts()
        if not rows:
            return ("The mount table could not be read, so nothing is listed. "
                    "That is not the same as nothing being mounted.")
        real = [m for m in rows if m["fstype"] not in ("proc", "sysfs", "devtmpfs", "tmpfs")]
        lines = [f"{len(real)} mounted filesystem(s), excluding the usual pseudo ones:"]
        shown, withheld = files.cap_list(real, 40)
        lines += [f"  {m['target']}  ({m['fstype']} from {m['source']})" for m in shown]
        note = files.withheld_note("filesystem", withheld)
        if note:
            lines.append(f"  {note}")
        lines.append("  Use the 'filesystems' sense for space and inode detail.")
        return "\n".join(lines)

    allowed, reason = _consent(ChronoaConfig())
    if not allowed:
        return f"Refusing to change mounts: {reason}"

    if shutil.which("udisksctl") is None:
        return (
            "udisksctl is not installed, so this will not fall back to a bare "
            "mount command. That fallback would bypass the polkit authorisation "
            "which is what makes mounting safe to offer. On Arch it comes from "
            "udisks2 (the package is named udisks2, not udisks)."
        )

    device = (arguments.get("device") or "").strip()
    if not device:
        return f"No device or mount point was named, so there is nothing to {action}."

    if action == "mount":
        if shutil.which("findmnt") is None:
            return files.tool_missing("findmnt", "check whether something is already mounted")
        try:
            already = subprocess.run(["findmnt", "--target", device],
                                     capture_output=True, text=True,
                                     timeout=_TIMEOUT, check=False)
        except (subprocess.TimeoutExpired, OSError) as exc:
            return f"Could not check the mount table: {exc}"
        if already.returncode == 0:
            return f"{device} is already mounted, so nothing was changed."

    try:
        proc = subprocess.run(["udisksctl", action, "-b" if action == "mount" else "-p", device],
                              capture_output=True, text=True, timeout=_TIMEOUT, check=False)
    except subprocess.TimeoutExpired:
        return f"Asked the disk service to {action} {device} but it did not answer in {_TIMEOUT}s; the outcome is unknown."
    except OSError as exc:
        return f"Could not {action} {device}: {exc}"

    if proc.returncode != 0:
        detail = (proc.stderr or proc.stdout or "").strip().splitlines()
        joined = " ".join(detail).lower()
        if "busy" in joined:
            return f"Could not unmount {device}: it is in use. Nothing was changed."
        return f"The disk service refused to {action} {device} (exit {proc.returncode})" + (
            f": {detail[-1]}" if detail else ".")

    if action == "mount" and shutil.which("findmnt") is not None:
        try:
            check = subprocess.run(["findmnt", "--target", device, "-o", "TARGET,SOURCE,FSTYPE"],
                                   capture_output=True, text=True, timeout=_TIMEOUT, check=False)
        except (subprocess.TimeoutExpired, OSError):
            check = None
        if check is not None and check.returncode == 0 and check.stdout.strip():
            return (f"Mounted. Verified against the mount table:\n"
                    f"{check.stdout.strip()}")
    return f"Asked the disk service to {action} {device} and it reported success."


def _post_condition(arguments: dict) -> tuple[bool, str]:
    """Did the mount change, read from the mount table rather than the exit code.

    `udisksctl` returning 0 says the disk service accepted the request, which
    is not the same as the filesystem being mounted - and UNVERIFIED is the one
    verdict the replay guard refuses to store, so an unverifiable unmount could
    be replayed by a notification pressed twice.

    A mount is observable directly: `findmnt --target` answers for the mount
    point. An unmount is a negative claim, and the honest way to check one is to
    ask whether the target is *absent* from the table - which is still the mount
    table, not the exit code.
    """
    action = (arguments.get("action") or "list").strip().lower()
    if action not in ("mount", "unmount"):
        return True, f"'{action}' changes nothing, so there is nothing to verify"
    device = (arguments.get("device") or "").strip()
    if not device:
        return False, "no device was named, so there is nothing that could have changed"
    if shutil.which("findmnt") is None:
        return False, "findmnt is not installed, so the mount table cannot be read"
    try:
        found = subprocess.run(["findmnt", "--target", device], capture_output=True,
                                text=True, timeout=_TIMEOUT, check=False)
    except (subprocess.TimeoutExpired, OSError) as exc:
        return False, f"could not read the mount table: {exc.__class__.__name__}"
    mounted = found.returncode == 0 and bool(found.stdout.strip())
    if action == "mount" and mounted:
        return True, f"the mount table lists {device}"
    if action == "unmount" and not mounted:
        return True, f"the mount table no longer lists {device}"
    state = "still listed" if mounted else "not listed"
    return False, f"{device} is {state} in the mount table, so the {action} did not hold"


POST_CONDITION = _post_condition

SKILLS = [Skill(name="manage_mount", schema=SCHEMA, run=_run)]
