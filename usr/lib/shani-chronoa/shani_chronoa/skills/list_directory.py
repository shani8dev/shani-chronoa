"""Skill: list what is in a folder.

Answers the single most common question a person asks a computer - "what is in
here?" - which nothing else in this project could answer. `disk_usage` reports
how full a filesystem is and `find_files` searches by name; neither shows the
contents of a directory the user is looking at.

Honesty rules, which are the whole reason this is not three lines of `os.listdir`:

- A directory that cannot be listed says **permission denied**, and says the
  directory exists. It does not become an empty listing, because an empty
  listing is indistinguishable from a folder with nothing in it and the user
  would conclude the wrong thing about their own disk.
- A path that is a file rather than a folder says so, rather than listing the
  file as though it were a container.
- A truncated listing says it was truncated and how to widen it. Returning the
  first N entries unmarked turns "there is more" into "this is everything".
"""

from __future__ import annotations

import os

from shani_chronoa import files
from shani_chronoa.skills import Skill

_MAX_ENTRIES = 200

SCHEMA = {
    "type": "function",
    "function": {
        "name": "list_directory",
        "description": (
            "List the files and folders in a directory, with each one's type "
            "and size. Use this to see what is in a folder before opening, "
            "moving or deleting anything in it."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "path": {
                    "type": "string",
                    "description": (
                        "Directory to list. Defaults to the home directory. "
                        "`~` and $VARS are expanded."
                    ),
                },
                "include_hidden": {
                    "type": "boolean",
                    "description": "Include dotfiles. Defaults to false.",
                },
            },
        },
    },
}


def _run(arguments: dict) -> str:
    raw = (arguments.get("path") or "").strip() or "~"
    include_hidden = arguments.get("include_hidden")
    try:
        target = files.resolve(raw)
    except files.PathProblem as exc:
        return str(exc)

    if not target.exists():
        return files.describe(FileNotFoundError(2, "No such file or directory"),
                              target, "list")
    if not target.is_dir():
        return f"{target} is a file, not a directory. Use read_text_file to see it."

    try:
        names = sorted(os.listdir(target))
    except PermissionError:
        return (
            f"Could not list {target}: permission denied. The directory exists "
            f"but this user may not read it, so its contents are unknown - not "
            f"empty."
        )
    except OSError as exc:
        return files.describe(exc, target, "list")

    if not include_hidden:
        names = [n for n in names if not n.startswith(".")]

    if not names:
        return f"{target} contains no {'visible ' if not include_hidden else ''}entries."

    shown = names[:_MAX_ENTRIES]
    lines = [f"{target}: {len(names)} entr{'y' if len(names) == 1 else 'ies'}"]
    for name in shown:
        lines.append(files.entry_line(target / name, target))
    if len(names) > len(shown):
        lines.append(
            f"  ... and {len(names) - len(shown)} more, not shown (limit is "
            f"{_MAX_ENTRIES}). Narrow the path or use find_files."
        )
    return "\n".join(lines)


SKILLS = [Skill(name="list_directory", schema=SCHEMA, run=_run)]
