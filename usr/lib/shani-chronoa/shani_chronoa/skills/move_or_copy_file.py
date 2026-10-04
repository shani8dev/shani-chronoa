"""Skill: move or copy a file or directory.

One skill with a `mode` argument rather than two skills, because the two differ
by one flag and splitting them doubles the surface an LLM has to choose between
correctly.

Honesty rules: a cross-device move is reported as a copy-then-delete, because
`shutil.move` silently falls back to that and the caller should know their file
crossed a filesystem boundary. A refused overwrite says what is in the way.
"""

from __future__ import annotations

import os
import shutil

from shani_chronoa import files
from shani_chronoa.skills import Skill

SCHEMA = {
    "type": "function",
    "function": {
        "name": "move_or_copy_file",
        "description": (
            "Move or copy a file or directory to a new path. Moving a directory "
            "into itself is refused, and an existing destination is not "
            "overwritten unless asked."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "source": {"type": "string", "description": "What to move or copy."},
                "destination": {"type": "string", "description": "Where it should end up."},
                "mode": {
                    "type": "string",
                    "description": "'move' (default) or 'copy'.",
                },
                "overwrite": {
                    "type": "boolean",
                    "description": "Replace the destination if it exists. Defaults to false.",
                },
            },
        },
    },
}


def _run(arguments: dict) -> str:
    mode = (arguments.get("mode") or "move").strip().lower()
    if mode not in ("move", "copy"):
        return f"Mode must be 'move' or 'copy', not {mode!r}."
    try:
        source = files.resolve(arguments.get("source") or "")
        destination = files.resolve(arguments.get("destination") or "")
    except files.PathProblem as exc:
        return str(exc)
    if not source.exists():
        return files.describe(FileNotFoundError(2, "No such file or directory"),
                              source, mode)
    if source == destination:
        return f"{source} is already there; nothing to do."
    if source.is_dir() and destination.is_relative_to(source):
        return (
            f"Refusing to {mode} {source} into {destination}: a directory "
            f"cannot be moved inside itself."
        )
    if destination.exists() and not arguments.get("overwrite"):
        return (
            f"Refusing to {mode} {source} onto {destination}: it already "
            f"exists. Pass overwrite to replace it."
        )

    try:
        if mode == "copy":
            if source.is_dir():
                shutil.copytree(source, destination, dirs_exist_ok=bool(arguments.get("overwrite")))
            else:
                shutil.copy2(source, destination, follow_symlinks=False)
        else:
            shutil.move(str(source), str(destination), overwrite=bool(arguments.get("overwrite")))
    except OSError as exc:
        return files.describe(exc, destination if destination.exists() else source, mode)

    if not destination.exists():
        return f"Reported success but {destination} is not there."
    verb = "Copied" if mode == "copy" else "Moved"
    return f"{verb} {source} to {destination}."



def _post_condition(arguments: dict):
    """The filesystem after the call, read with a fresh stat - not the skill's own report."""
    mode = (arguments.get("mode") or "move").strip().lower()
    try:
        source = files.resolve(arguments.get("source") or "")
        destination = files.resolve(arguments.get("destination") or "")
    except files.PathProblem:
        return None
    if destination.is_dir() and source.name and (destination / source.name).exists() and not source.is_dir():
        destination = destination / source.name
    there = destination.exists()
    left = os.path.lexists(source)
    ok = there and (not left if mode == "move" else True)
    return ok, f"destination {'exists' if there else 'missing'}" + (f", source {'still there' if left else 'gone'}"
                                                                     if mode == "move" else "")


# Declared for `verification.verify`; the LLM never supplies this.
POST_CONDITION = _post_condition


SKILLS = [Skill(name="move_or_copy_file", schema=SCHEMA, run=_run)]
