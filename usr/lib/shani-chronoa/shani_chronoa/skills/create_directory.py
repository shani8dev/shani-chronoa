"""Skill: create a directory, including parents.

`mkdir -p` behaviour: creating a directory that already exists succeeds and
says so, because that is what makes the skill safe to call twice.

Honesty rules: a failure names the cause, and a partial creation is reported
rather than left for the user to discover later.
"""

from __future__ import annotations

from shani_chronoa import files
from shani_chronoa.skills import Skill

SCHEMA = {
    "type": "function",
    "function": {
        "name": "create_directory",
        "description": (
            "Create a directory, and any missing parent directories. Succeeds "
            "without complaint if it already exists."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "The directory to create."},
            },
        },
    },
}


def _run(arguments: dict) -> str:
    try:
        target = files.resolve(arguments.get("path") or "")
    except files.PathProblem as exc:
        return str(exc)
    if target.is_dir():
        return f"{target} already exists."
    if target.exists():
        return f"Could not create {target}: something is already there and it is not a directory."
    try:
        target.mkdir(parents=True, exist_ok=False)
    except OSError as exc:
        return files.describe(exc, target, "create")
    if not target.is_dir():
        return f"Reported success but {target} is not a directory."
    return f"Created {target}."



def _post_condition(arguments: dict):
    """The filesystem after the call, read with a fresh stat - not the skill's own report."""
    try:
        target = files.resolve(arguments.get("path") or "")
    except files.PathProblem:
        return None
    return target.is_dir(), f"{target} {'is' if target.is_dir() else 'is not'} a directory"


# Declared for `verification.verify`; the LLM never supplies this.
POST_CONDITION = _post_condition


SKILLS = [Skill(name="create_directory", schema=SCHEMA, run=_run)]
