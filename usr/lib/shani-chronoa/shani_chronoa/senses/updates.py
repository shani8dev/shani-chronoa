"""Sense: package updates that are waiting, and how stale the answer is.

"Does my machine need updating?" is a daily question with no answer anywhere in
this project. `check_updates` (a skill) answers it only when asked, and answers
it from a *freshly synced* database, which is a different and much more expensive
claim than the one a sense can make.

The honesty problem here is not the comparison, it is the freshness. `pacman -Qu`
compares the installed set against the local sync database, and that database is
only as current as the last `pacman -Sy`. A machine that has not synced in three
weeks reports "0 updates" and that is technically true and completely useless -
it is the exact shape of a plausible wrong answer this package is built to avoid.
So the age of the sync database is reported as a first-class part of the answer,
and an old database downgrades the headline rather than sitting quietly under it.

Comparison itself is delegated to pacman's own `vercmp` via `pacman -Qu` rather
than reimplemented. A hand-rolled version comparison gets epoch handling, `pkgrel`
and the `1.0a` vs `1.0` rules wrong, and a wrong ordering here means reporting
the wrong number of updates on a machine that needs them.

Honesty rules:

- **No sync database is UNKNOWN, not "0 updates waiting."** Those are opposite
  answers and conflating them is the failure this sense exists to prevent.
- A stale database is never presented as a clean bill of health. The count is
  reported together with how old the comparison is.
- Package *names* only. Versions and upgrade sizes come from pacman, and are
  passed through rather than summarised, so nothing is invented here.
"""

from __future__ import annotations

import logging
import os
import re
import shutil
import subprocess
import time
from pathlib import Path
from typing import List, Optional, Union

from shani_chronoa.senses import SENSITIVITY_PUBLIC, Sense
from shani_chronoa.senses.context import Percept

logger = logging.getLogger(__name__)

KIND = "machine-state"
SENSITIVITY = SENSITIVITY_PUBLIC
_TTL_SECONDS = 600.0
_POLL_INTERVAL = 600.0
_TIMEOUT = 30

_SYNC_DIR = Path("/var/lib/pacman/sync")
#: Past this, the comparison is reported as stale in its own right rather than
#: being presented as a current answer. A week is long enough that a machine
#: which has genuinely not synced is the likely explanation, and short enough
#: that a fresh install is not accused of being stale.
_STALE_SECONDS = 7 * 24 * 3600

_SCHEMA = {
    "type": "function",
    "function": {
        "name": "updates",
        "description": (
            "Report how many package updates are waiting, and how old the "
            "package database the comparison is made against is."
        ),
        "parameters": {"type": "object", "properties": {}},
    },
}


def _newest_sync_db() -> Optional[Path]:
    """Most recently written sync database, or None when there is none.

    `.sig` sidecars sit alongside the databases and are not databases, so they
    are excluded by suffix rather than by pattern.
    """
    try:
        dbs = [p for p in _SYNC_DIR.iterdir()
               if p.suffix == ".db" and p.is_file()]
    except OSError:
        return None
    if not dbs:
        return None
    return max(dbs, key=lambda p: p.stat().st_mtime)


def read_pending() -> Optional[List[str]]:
    """Package names with a newer version in the sync db, or None if unknown.

    `pacman -Qu` is the comparison. It needs no privileges for a query, so this
    does not shell out to anything elevated.
    """
    if shutil.which("pacman") is None:
        return None
    try:
        proc = subprocess.run(["pacman", "-Qu"], capture_output=True,
                              text=True, timeout=_TIMEOUT, check=False)
    except (subprocess.TimeoutExpired, OSError):
        return None
    # pacman exits 1 when it has nothing to report and 0 when it has something,
    # which is the opposite of the usual convention. Both mean the query
    # succeeded; only an unreadable database is a failure.
    if proc.returncode not in (0, 1):
        return None
    out = []
    for line in proc.stdout.splitlines():
        name = line.split()[0] if line.split() else ""
        if name:
            out.append(name)
    return out


_KERNEL_PACKAGES = ("linux", "linux-lts", "linux-zen", "linux-hardened")
_RELEASE_RE = re.compile(r"(\d+)\.(\d+)\.(\d+)")


def _release(full: str) -> Optional[str]:
    """The leading major.minor.patch of a kernel string, or None.

    Arch's installed version and `uname -r` never compare as plain strings: the
    package reports `6.9.1.arch1-1` and the running kernel reports
    `6.9.1-arch1-1`. The same release number is written with a dot in one and a
    dash in the other, and the flavour and pkgrel differ too, so a direct
    comparison reports a mismatch on every machine including a fully up-to-date
    one. Only the release number is comparable, so only that is compared - and
    both full strings are printed, so a person can check the reasoning.
    """
    match = _RELEASE_RE.search(full or "")
    return match.group(0) if match else None


def read_kernel_state() -> Optional[dict]:
    """Running and installed kernel, or None when it cannot be established."""
    running = os.uname().release
    installed = None
    installed_from = None
    for name in _KERNEL_PACKAGES:
        if shutil.which("pacman") is None:
            break
        try:
            proc = subprocess.run(["pacman", "-Q", name], capture_output=True,
                                  text=True, timeout=_TIMEOUT, check=False)
        except (subprocess.TimeoutExpired, OSError):
            return None
        if proc.returncode == 0 and proc.stdout.strip():
            parts = proc.stdout.split()
            if len(parts) >= 2:
                installed = parts[1]
                installed_from = name
                break
    if installed is None:
        return {"running": running, "installed": None, "package": None,
                "running_release": _release(running), "installed_release": None}
    return {"running": running, "installed": installed,
            "package": installed_from,
            "running_release": _release(running),
            "installed_release": _release(installed)}


def _reboot_verdict(state: dict) -> str:
    """The honest wording for the running-versus-installed comparison."""
    if state["installed"] is None:
        return ("  A newer kernel is installed: UNKNOWN - no installed kernel "
                "package could be identified among "
                + ", ".join(_KERNEL_PACKAGES)
                + ". The running kernel is " + state["running"] + ".")
    run_rel, inst_rel = state["running_release"], state["installed_release"]
    if run_rel is None or inst_rel is None:
        return (f"  Running kernel {state['running']}, installed "
                f"{state['package']} {state['installed']} - the release numbers "
                f"could not be compared, so whether a reboot would pick up a new "
                f"kernel is UNKNOWN.")
    if run_rel == inst_rel:
        return (f"  Running kernel {state['running']} matches the installed "
                f"{state['package']} {state['installed']}, so a reboot would not "
                f"change the kernel.")
    return (f"  A reboot would change the kernel: running {state['running']} "
            f"(release {run_rel}), installed {state['package']} "
            f"{state['installed']} (release {inst_rel}). Compared on the release "
            f"number only, because Arch writes the same version with a dot in the "
            f"package and a dash in `uname -r`.")


def _run(arguments: dict) -> Union[str, Percept]:
    if shutil.which("pacman") is None:
        return (
            "Pending updates are UNKNOWN: pacman is not installed, so this is "
            "not an Arch-family system this sense can answer for. That is not "
            "the same as having no updates waiting."
        )

    db = _newest_sync_db()
    pending = read_pending()

    if pending is None:
        return (
            "Pending updates are UNKNOWN: pacman could not compare the installed "
            "packages against the sync database, so no count is given rather "
            "than a zero that would mean nothing."
        )

    if db is None:
        return (
            f"{len(pending)} package(s) look updatable, but the sync database "
            f"this was compared against is not where it was expected "
            f"({_SYNC_DIR}), so its age is UNKNOWN and the count should not be "
            f"relied on. Package managers sync on a schedule; run a sync before "
            f"treating zero as an answer."
        )

    age = time.time() - db.stat().st_mtime
    stale = age > _STALE_SECONDS
    kernel = read_kernel_state()

    if not pending:
        verdict = (f"No updates are waiting against a package database that is "
                   f"{_human_age(age)} old.")
        if stale:
            verdict += (
                " That database is stale, so this is closer to 'nothing is known "
                "to be waiting' than to 'the machine is up to date' - it means "
                "nothing has been offered to compare against yet."
            )
    else:
        shown = pending[:40]
        verdict = (f"{len(pending)} package update(s) waiting, compared against a "
                   f"package database {_human_age(age)} old:")
        verdict += "\n" + "\n".join(f"  {name}" for name in shown)
        if len(pending) > len(shown):
            verdict += f"\n  and {len(pending) - len(shown)} more"
        if stale:
            verdict += (
                "\nThe database is stale, so the real number is at least this "
                "and probably higher."
            )

    if kernel is not None:
        verdict += "\n" + _reboot_verdict(kernel)

    return _SENSE.to_percept(
        verdict,
        source="pacman",
        metadata={
            "kernel_running": None if kernel is None else kernel["running"],
            "kernel_installed": None if kernel is None else kernel["installed"],
            "pending": len(pending),
            "pending_sample": pending[:40],
            "database_age_seconds": None if db is None else int(age),
            "database_stale": bool(stale),
            "database": None if db is None else str(db),
        },
    )


def _human_age(seconds: float) -> str:
    if seconds < 3600:
        return f"{int(seconds // 60)} minute(s)"
    if seconds < 86400:
        return f"{int(seconds // 3600)} hour(s)"
    return f"{int(seconds // 86400)} day(s)"


_SENSE = Sense(
    name="updates",
    kind=KIND,
    sensitivity=SENSITIVITY,
    schema=_SCHEMA,
    run=_run,
    ttl_seconds=_TTL_SECONDS,
    poll_interval=_POLL_INTERVAL,
)

SENSES = [_SENSE]
