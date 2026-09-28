"""Skill: report available package updates, the way a human would check.

Prefers `checkupdates` (pacman-contrib) over `pacman -Qu` for a reason that is
not stylistic: `checkupdates` works against a throwaway copy of the sync
database, so it never takes the real database lock and never prompts, whereas
`pacman -Qu` reads the live one. Neither is run as root, and neither installs
anything - this skill reports, and installing stays a deliberate human action.

The answer that must not exist is "0 updates available" from a machine with no
package manager. That is what a bare `except` around the subprocess produces on
any non-Arch system, and it is indistinguishable from a fully up-to-date
machine. So every failure path here names what was missing or what went wrong,
and none of them can return an empty list as if it were a clean bill of health.

Exit codes from `checkupdates` are load-bearing and easy to conflate: 0 means
no updates, **2** means updates were found, 1 is a real error, and 3 means
another pacman held the database lock. Treating 2 as failure - or 1 as "clean" -
inverts the answer.
"""

from __future__ import annotations

import logging
import shutil
import subprocess

from shani_chronoa.skills import Skill

logger = logging.getLogger(__name__)

_SCHEMA = {
    "type": "function",
    "function": {
        "name": "check_updates",
        "description": (
            "Check whether this machine has package updates waiting, without "
            "installing anything and without needing root. Works on Arch and "
            "other pacman systems."
        ),
        "parameters": {"type": "object", "properties": {}},
    },
}

_TIMEOUT = 120
_MAX_SHOWN = 40


def _run_with(cmd: list[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        cmd, capture_output=True, text=True, timeout=_TIMEOUT, check=False)


def _format(pairs: list[str], source: str) -> str:
    if not pairs:
        return (
            f"No package updates are waiting ({source}). The package manager "
            f"answered, so this is a real all-clear rather than an unchecked "
            f"machine."
        )
    head = f"{len(pairs)} package update(s) waiting ({source}):"
    shown = pairs[:_MAX_SHOWN]
    tail = (
        f"\n... and {len(pairs) - _MAX_SHOWN} more." if len(pairs) > _MAX_SHOWN else ""
    )
    return head + "\n" + "\n".join(f"  {p}" for p in shown) + tail


def _run(_arguments: dict) -> str:
    if shutil.which("checkupdates"):
        source = "checkupdates"
        cmd = ["checkupdates"]
    elif shutil.which("pacman"):
        source = "pacman -Qu"
        cmd = ["pacman", "-Qu"]
    else:
        return (
            "Could not check for updates: neither `checkupdates` nor `pacman` is "
            "installed, so this machine's package manager is not one I know how "
            "to query. That is not the same as having no updates - I could not "
            "look."
        )

    try:
        proc = _run_with(cmd)
    except subprocess.TimeoutExpired:
        return (
            f"Could not check for updates: `{source}` did not finish within "
            f"{_TIMEOUT}s. Whether updates are waiting is unknown."
        )
    except (OSError, subprocess.SubprocessError) as exc:
        return f"Could not check for updates: {source} could not be run ({exc})."

    if source == "checkupdates":
        if proc.returncode == 2:
            return _format(
                [ln.strip() for ln in proc.stdout.splitlines() if ln.strip()],
                source)
        if proc.returncode == 0:
            return _format([], source)
        if proc.returncode == 3:
            return (
                "Could not check for updates: another pacman process is holding "
                "the package database lock, so the sync databases could not be "
                "read. This is not an all-clear - try again once the other "
                "package operation finishes."
            )
        detail = (proc.stderr or proc.stdout or "").strip() or "unknown error"
        return f"Could not check for updates: {source} failed ({detail})."

    if proc.returncode != 0:
        detail = (proc.stderr or proc.stdout or "").strip() or "unknown error"
        return f"Could not check for updates: {source} failed ({detail})."
    return _format([ln.strip() for ln in proc.stdout.splitlines() if ln.strip()], source)


SKILLS = [Skill(name="check_updates", schema=_SCHEMA, run=_run)]
