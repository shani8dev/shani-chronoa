"""Skill: open a file or folder with whatever application handles it.

Deliberately not "run this file". Handing an arbitrary path to a shell is the
generic-exec pattern this project refuses; handing it to the desktop's own
`open`/`xdg-open` is the same thing a double-click does, and inherits the
system's association table rather than inventing a second one.

Honesty rules: an empty path is refused rather than opening the home directory.
A missing opener is reported as a missing package, not as a silent no-op, which
is what `xdg-open` exiting quietly would otherwise look like.
"""

from __future__ import annotations

import shutil
import subprocess
import sys

from shani_chronoa import files
from shani_chronoa.skills import Skill

_TIMEOUT = 20

SCHEMA = {
    "type": "function",
    "function": {
        "name": "open_file",
        "description": (
            "Open a file or folder using the desktop's default application for "
            "it - the same thing double-clicking it would do."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "The file or directory to open."},
            },
        },
    },
}


def _run(arguments: dict) -> str:
    raw = (arguments.get("path") or "").strip()
    if not raw:
        return "No path was given, so there is nothing to open."
    try:
        target = files.resolve(raw)
    except files.PathProblem as exc:
        return str(exc)
    if not target.exists():
        return files.describe(FileNotFoundError(2, "No such file or directory"),
                              target, "open")

    launcher = "open" if sys.platform == "darwin" else "xdg-open"
    if shutil.which(launcher) is None:
        return (
            f"Could not open {target}: {launcher} is not installed on this "
            f"machine, so nothing was launched. On Arch it comes from xdg-utils."
        )
    try:
        proc = subprocess.run(
            [launcher, str(target)], capture_output=True, text=True,
            timeout=_TIMEOUT, check=False,
        )
    except subprocess.TimeoutExpired:
        return f"Asked the desktop to open {target} but it did not answer in {_TIMEOUT}s."
    except OSError as exc:
        return f"Could not open {target}: {exc}"
    if proc.returncode != 0:
        detail = (proc.stderr or proc.stdout or "").strip().splitlines()
        return (
            f"The desktop refused to open {target} (exit {proc.returncode})"
            + (f": {detail[-1]}" if detail else ".")
        )
    kind = "folder" if target.is_dir() else "file"
    return f"Asked the desktop to open the {kind} {target}."


SKILLS = [Skill(name="open_file", schema=SCHEMA, run=_run)]
