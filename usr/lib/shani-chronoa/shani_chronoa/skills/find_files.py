"""Skill: find files by name.

The companion to `list_directory`: that answers "what is in this folder", this
answers "where is that file, anywhere under here". Name search only - it never
opens a file and never looks at its contents, so it is safe to run across a
whole home directory. `search_file_contents` is the one that reads.

Honesty rules:

- "no files matched that pattern" is a different sentence from "there are no
  files here". The first is a fact about the search; the second is a claim
  about the disk, and the skill never makes it.
- A result set that hit the limit says so, and says what to narrow.
- A directory that could not be walked does not silently contribute zero
  results, because that is indistinguishable from a directory with no matches.
"""

from __future__ import annotations

import fnmatch
import os
from pathlib import Path

from shani_chronoa import files
from shani_chronoa.skills import Skill

_MAX_RESULTS = 100
_MAX_DEPTH = 12

SCHEMA = {
    "type": "function",
    "function": {
        "name": "find_files",
        "description": (
            "Find files and folders by name under a directory, using a "
            "wildcard pattern such as '*.log' or 'report*'. Matches on the name "
            "only and never opens the files."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "pattern": {
                    "type": "string",
                    "description": (
                        "Wildcard pattern, e.g. '*.png'. Matched against each "
                        "entry's own name, not its full path."
                    ),
                },
                "path": {
                    "type": "string",
                    "description": "Where to search. Defaults to the home directory.",
                },
                "include_hidden": {
                    "type": "boolean",
                    "description": "Include dotfiles and dot-directories. Defaults to false.",
                },
                "max_results": {
                    "type": "integer",
                    "description": f"Stop after this many matches. Defaults to {_MAX_RESULTS}.",
                },
            },
        },
    },
}


def _run(arguments: dict) -> str:
    pattern = (arguments.get("pattern") or "").strip()
    if not pattern:
        return (
            "No pattern was given. Use a wildcard such as '*.log' or "
            "'report*' - a bare word matches only a file with exactly that name."
        )
    raw_path = (arguments.get("path") or "").strip() or "~"
    try:
        root = files.resolve(raw_path)
    except files.PathProblem as exc:
        return str(exc)
    if not root.exists():
        return files.describe(FileNotFoundError(2, "No such file or directory"),
                              root, "search")
    if not root.is_dir():
        return f"{root} is a file, not a directory, so there is nothing to search under."

    include_hidden = bool(arguments.get("include_hidden"))
    try:
        limit = int(arguments.get("max_results") or _MAX_RESULTS)
    except (TypeError, ValueError):
        limit = _MAX_RESULTS
    limit = max(1, min(limit, 1000))

    matches: list[Path] = []
    skipped: list[str] = []
    truncated = False
    for dirpath, dirnames, filenames in os.walk(root, onerror=lambda e: skipped.append(str(e))):
        here = Path(dirpath)
        if len(here.parts) - len(root.parts) >= _MAX_DEPTH:
            dirnames[:] = []
        if not include_hidden:
            dirnames[:] = [d for d in dirnames if not d.startswith(".")]
            filenames = [f for f in filenames if not f.startswith(".")]
        for name in filenames + dirnames:
            if files_here_matches(name, pattern):
                matches.append(here / name)
                if len(matches) >= limit:
                    truncated = True
                    break
        if truncated:
            break

    if not matches:
        base = f"No entries under {root} match {pattern!r}."
        if not include_hidden:
            base += " Hidden entries were not searched."
        if skipped:
            base += f" {len(skipped)} directory(ies) could not be read and were skipped."
        return base

    lines = [f"{len(matches)}{'+' if truncated else ''} match(es) for {pattern!r} under {root}:"]
    for m in matches:
        lines.append(f"  {m}")
    if truncated:
        lines.append(f"  ... stopped at the {limit}-match limit; this is not everything.")
    if skipped:
        lines.append(
            f"  note: {len(skipped)} directory(ies) could not be read and were "
            f"not searched, so matches may exist that are not listed."
        )
    return "\n".join(lines)


def files_here_matches(name: str, pattern: str) -> bool:
    """Case-insensitive glob match, with a plain-substring fallback.

    The fallback exists because the common request is "find the thing called
    config" rather than "find everything matching con*", and reporting no match
    for a name that plainly contains the word would be a lie by omission.
    """
    if fnmatch.fnmatch(name.lower(), pattern.lower()):
        return True
    return pattern.lower() in name.lower() and "*" not in pattern


SKILLS = [Skill(name="find_files", schema=SCHEMA, run=_run)]
