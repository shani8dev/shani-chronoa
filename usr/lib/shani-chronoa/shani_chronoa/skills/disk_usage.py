"""Skill: report what is using disk space.

Answers the two questions that actually get asked - "which filesystem is
filling up" and "what is eating the space in this directory" - and refuses to
answer the second one in a way that would be wrong.

`df` is cheap, exact and instant, so it is the primary answer. Walking a
directory tree with `du` is neither: it is O(files) and on a large tree it can
run for minutes. So the walk is bounded three ways - one filesystem only
(`-x`, so a bind mount or a home on another disk does not silently skew the
total), a depth limit, and a hard timeout - and **every** way it can come back
incomplete is reported rather than hidden:

- a timeout says the figures are partial and names the timeout, because a
  partial list sorted largest-first is exactly the shape of a complete one, and
  the user would otherwise conclude the biggest thing was found;
- `du` being absent is a missing binary, not a full disk;
- a permission-denied path is listed, since "no large directories" would
  otherwise be an artefact of a directory the user cannot read.

Nothing here writes, deletes, or suggests deleting. Acting on a full disk is a
human decision, and the useful part of this skill is telling the truth about
which paths are large.
"""

from __future__ import annotations

import logging
import os
import shutil
import subprocess

from shani_chronoa.skills import Skill

logger = logging.getLogger(__name__)

_SCHEMA = {
    "type": "function",
    "function": {
        "name": "disk_usage",
        "description": (
            "Report how full each filesystem is, and optionally which "
            "directories under a given path use the most space. Read-only: it "
            "never deletes anything."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "path": {
                    "type": "string",
                    "description": (
                        "Optional directory to break down by top-level "
                        "subdirectory, e.g. '/home/me'. Omit for just the "
                        "filesystem summary. This walk can be slow on a large "
                        "tree and is stopped after a few seconds."
                    ),
                },
            },
        },
    },
}

_DF_TIMEOUT = 10
_DU_TIMEOUT = 20
_MAX_DIRS = 20
# tmpfs/devtmpfs are RAM, not disk, and their sizes are noise in an answer about
# storage filling up.
_PSEUDO_FS = ("tmpfs", "devtmpfs", "squashfs")


def _filesystems() -> str:
    try:
        proc = subprocess.run(
            ["df", "-hP", "-x", "tmpfs", "-x", "devtmpfs", "-x", "squashfs"],
            capture_output=True, text=True, timeout=_DF_TIMEOUT, check=False)
    except (OSError, subprocess.SubprocessError) as exc:
        return f"Could not read filesystem usage: df could not be run ({exc})."
    if proc.returncode != 0:
        detail = (proc.stderr or "").strip() or "unknown error"
        return f"Could not read filesystem usage: df failed ({detail})."

    lines = [ln for ln in proc.stdout.splitlines() if ln.strip()]
    if len(lines) < 2:
        return (
            "df returned no filesystem rows, so usage is unknown - this does "
            "not mean every filesystem is empty."
        )
    header, rows = lines[0], lines[1:]
    out = [header]
    kept = 0
    for row in rows:
        parts = row.split(None, 5)
        if len(parts) < 6:
            continue
        if parts[-1] in ("/", "/boot", "/home") or kept < 12:
            out.append(row)
            kept += 1
    if kept < len(rows):
        out.append(f"... and {len(rows) - kept} more filesystem(s).")
    return "\n".join(out)


def _top_directories(path: str) -> str:
    resolved = os.path.abspath(os.path.expanduser(path))
    if not os.path.isdir(resolved):
        return (
            f"'{path}' is not a directory I can read"
            + ("" if os.path.exists(resolved) else " (it does not exist)")
            + "."
        )
    if shutil.which("du") is None:
        return (
            "Could not break the path down: `du` is not installed. The "
            "filesystem summary above is unaffected."
        )

    cmd = ["du", "-x", "-h", "--max-depth=1", resolved]
    try:
        proc = subprocess.run(
            cmd, capture_output=True, text=True, timeout=_DU_TIMEOUT, check=False)
    except subprocess.TimeoutExpired:
        return (
            f"Giving up on the breakdown of {resolved} after {_DU_TIMEOUT}s: it "
            f"is too large to walk quickly. What is below is PARTIAL - the "
            f"largest directories may not have been reached, so do not read it "
            f"as the full picture."
        )
    except (OSError, subprocess.SubprocessError) as exc:
        return f"Could not break down {resolved}: du could not be run ({exc})."

    rows: list[tuple[int, str]] = []
    denied: list[str] = []
    for line in proc.stdout.splitlines():
        parts = line.split(None, 1)
        if len(parts) != 2:
            continue
        size, target = parts
        if target == resolved:
            continue
        try:
            rows.append((int(size.rstrip("KMGTPE")), target))
        except ValueError:
            # A path containing a newline or unusual bytes can defeat split().
            continue
    for line in proc.stderr.splitlines():
        if "Permission denied" in line:
            denied.append(line.strip())

    if not rows:
        detail = " ".join(denied) if denied else (proc.stderr or "").strip()
        return (
            f"No directory sizes could be read under {resolved}."
            + (f" Some paths were not readable: {detail}" if detail else "")
        )

    rows.sort(reverse=True)
    out = [f"Largest entries directly under {resolved}:"]
    for _, target in rows[:_MAX_DIRS]:
        out.append(f"  {target}")
    if len(rows) > _MAX_DIRS:
        out.append(f"  ... and {len(rows) - _MAX_DIRS} more.")
    if denied:
        out.append(
            f"{len(denied)} path(s) were not readable, so these totals are "
            f"lower bounds."
        )
    return "\n".join(out)


def _run(arguments: dict) -> str:
    parts = [_filesystems()]
    path = str(arguments.get("path") or "").strip()
    if path:
        parts.append(_top_directories(path))
    return "\n\n".join(parts)


SKILLS = [Skill(name="disk_usage", schema=_SCHEMA, run=_run)]
