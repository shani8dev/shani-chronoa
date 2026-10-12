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
import time

from shani_chronoa import files
from shani_chronoa.skills import Skill

#: How recently the *source's parent directory* must have changed for an absent
#: source to be attributed to this call rather than to a path that was never
#: there. Generous, because a recursive move of tens of thousands of files takes
#: tens of seconds and the mtime moves as the last one lands, not the first.
_FRESH_SECONDS = 300.0

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
            # `shutil.move` takes `(src, dst, copy_function)` - there is no
            # `overwrite` keyword, so passing one raised TypeError on **every**
            # move, and the handler below catches OSError, not TypeError, so the
            # traceback reached the model as the skill's whole answer.
            #
            # Replacement is not this function's job: the guard above has already
            # established that the destination may be replaced, and for a file
            # `os.rename` replaces it by itself. The one case needing work is a
            # directory onto an existing directory, which `shutil.move` refuses
            # with `shutil.Error` - and that *is* an OSError, so the handler
            # below already reports it as the refusal it is.
            if destination.is_dir():
                shutil.rmtree(destination)
            shutil.move(str(source), str(destination))
    except OSError as exc:
        return files.describe(exc, destination if destination.exists() else source, mode)

    if not destination.exists():
        return f"Reported success but {destination} is not there."
    verb = "Copied" if mode == "copy" else "Moved"
    return f"{verb} {source} to {destination}."



def _post_condition(arguments: dict):
    """The filesystem after the call, read with a fresh stat - not the skill's own report.

    **The freshness check is load-bearing, and it was missing.** For `move`,
    `destination exists and source is gone` is also exactly what a *refused* move
    leaves behind: "Could not move /x: it does not exist" against a destination
    that was already there satisfies both halves. So this reported **VERIFIED for
    a move that never happened**, which is the same defect `delete_file` had and
    the reason that one now checks the source's parent directory for recent
    change before certifying.

    Copy is exempt, and deliberately: it writes a *new* file, so a destination
    that did not exist before cannot have been left behind by a refusal.
    """
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
    evidence = "destination %s" % ("exists" if there else "missing")
    if mode == "move":
        evidence += ", source %s" % ("still there" if left else "gone")

    if not there:
        return False, evidence
    if mode == "copy":
        return True, evidence
    if left:
        return False, evidence

    # A refused move of a path that was never there leaves exactly the state a
    # real move leaves, so the source's parent directory is what tells the two
    # apart: a real move removes a name from it, and a refusal changes nothing.
    try:
        moved = time.time() - source.parent.stat().st_mtime
    except OSError as exc:
        return None, "could not read the source directory (%s)" % exc
    if moved > _FRESH_SECONDS:
        return None, (
            "%s is absent and its directory has not changed for %d minutes, so "
            "this is more likely a source that was never there than a move that "
            "just happened" % (source, int(moved / 60))
        )
    return True, evidence + ", and the source directory changed %.1fs ago" % moved


# Declared for `verification.verify`; the LLM never supplies this.
POST_CONDITION = _post_condition


SKILLS = [Skill(name="move_or_copy_file", schema=SCHEMA, run=_run)]
