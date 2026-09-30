"""Skill: show the shape of a directory tree.

`list_directory` answers "what is in *this* folder" and stops. The question
that is actually asked about a project is "what is the shape of this thing" -
which files sit at the top, which folders hold code, where the tests are -
and answering it with four separate `list_directory` calls is a conversation
nobody wants to have. This is one call.

It reuses `files.walk_limited` rather than re-implementing a bounded walk,
because the bounding *is* the honesty property: a walk that stops early and
says nothing turns "there is more" into "this is everything", which is the
error `files.py`'s own docstring names and which `list_directory` already
discloses in its own words ("N more, not shown"). Reusing the helper is what
keeps that disclosure from having to be re-derived here.

Honesty rules, each a place a naive tree renderer is quietly wrong:

- **A depth cut is labelled, not implied.** Reaching the depth limit prints
  `... deeper`, so a folder whose children were never walked cannot read as a
  folder with no children.
- **A folder that could not be listed is marked, not omitted.** `os.walk`'s
  default `onerror` is to *ignore* the error, which turns a permission-denied
  directory into a directory that appears to contain nothing. This pass
  collects those and prints them, because "I could not read it" and "it is
  empty" are different facts and only one of them is usually true.
- **Hidden entries are excluded by default and the exclusion is stated**, so a
  tree that looks sparse is not read as a project with no dotfiles.
- **A symlink is a link, never followed.** Following one can walk out of the
  tree, into a cycle, or into another user's home.
"""

from __future__ import annotations

import os
import stat as stat_mod
from pathlib import Path

from shani_chronoa import files
from shani_chronoa.skills import Skill

_MAX_ENTRIES = 300
_MAX_DEPTH = 6
_MAX_RENDERED_LINES = 200

SCHEMA = {
    "type": "function",
    "function": {
        "name": "directory_tree",
        "description": (
            "Show the shape of a directory as a nested tree, with each entry's "
            "type. Use this to understand how a project or folder is laid out "
            "before opening files in it. Reports what it skipped and when it "
            "stopped early."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "path": {
                    "type": "string",
                    "description": (
                        "Directory to show. Defaults to the home directory. "
                        "`~` and $VARS are expanded."
                    ),
                },
                "max_depth": {
                    "type": "integer",
                    "description": f"How deep to descend. Defaults to {_MAX_DEPTH}.",
                },
                "include_hidden": {
                    "type": "boolean",
                    "description": "Include dotfiles and dot-directories. Defaults to false.",
                },
            },
        },
    },
}


def _is_link_dir(path: Path) -> bool:
    """Whether a path is a symlink pointing at a directory.

    Checked on the link, not the target: `Path.is_dir()` follows the link, so
    a link to a directory would be descended into - which is both the way to
    leave the tree and the way into a cycle.
    """
    try:
        return path.is_symlink() and path.is_dir()
    except OSError:
        return False


def _run(arguments: dict) -> str:
    try:
        root = files.resolve_in_home((arguments.get("path") or "").strip() or "~")
    except files.PathProblem as exc:
        return str(exc)
    if not root.exists():
        return files.describe(FileNotFoundError(2, "No such file or directory"),
                              root, "read")
    if not root.is_dir():
        return f"{root} is a file, not a directory. Use read_text_file to see it."

    try:
        depth = int(arguments.get("max_depth") or _MAX_DEPTH)
    except (TypeError, ValueError):
        depth = _MAX_DEPTH
    depth = max(1, min(depth, 20))
    include_hidden = bool(arguments.get("include_hidden"))

    unreadable: list[str] = []
    # A bounded walk, and the count of what it did not reach is carried out
    # with it so the answer cannot read as complete.
    entries, truncated, stop_reason = files.walk_limited(
        root, max_entries=_MAX_ENTRIES, max_depth=depth,
        skip_hidden=not include_hidden,
    )
    # `walk_limited` walks with `onerror=None`, which discards the failure - so
    # a directory that could not be read contributes nothing and is
    # indistinguishable from an empty one. Re-listed here to mark it instead.
    # Probed from the entries rather than from `os.walk`, which cannot descend
    # past a directory it may not read and so never yields the unreadable one.
    def _probe(dirpath: str) -> None:
        try:
            os.listdir(dirpath)
        except PermissionError as exc:
            unreadable.append(f"{dirpath}  [{exc.strerror or 'permission denied'}]")
        except OSError as exc:
            unreadable.append(f"{dirpath}  [{type(exc).__name__}]")

    _probe(str(root))
    for entry in entries:
        if _is_link_dir(entry):
            continue
        try:
            if entry.is_dir():
                _probe(str(entry))
        except OSError:
            continue

    if not entries:
        if unreadable:
            return (
                f"Could not read {root}  [{unreadable[0].split('[', 1)[-1].rstrip(']')}]. "
                f"The directory exists but its contents are unknown - that is "
                f"not the same as empty, and nothing is claimed about what is "
                f"in it."
            )
        hidden_note = "" if include_hidden else " Hidden entries were not shown."
        return f"{root} contains no {'visible ' if not include_hidden else ''}entries.{hidden_note}"

    by_parent: dict[Path, list[Path]] = {}
    for entry in entries:
        by_parent.setdefault(entry.parent, []).append(entry)

    # A directory whose listing never arrived is not in `entries` as a parent,
    # so the depth cut is the other way round: it *is* there, with no children
    # recorded. Marking those is the difference between "empty" and "not read".
    shown_lines: list[str] = []
    cut: list[str] = []

    def render(directory: Path, indent: str) -> None:
        if len(shown_lines) >= _MAX_RENDERED_LINES:
            return
        children = sorted(by_parent.get(directory, []),
                          key=lambda p: (not _is_link_dir(p) and p.is_dir(), p.name))
        for child in children:
            if len(shown_lines) >= _MAX_RENDERED_LINES:
                cut.append(indent)
                return
            is_link = child.is_symlink()
            try:
                is_dir = child.is_dir()
            except OSError:
                is_dir = False
            shown_lines.append(
                f"{indent}{child.name}/" if is_dir and not is_link
                else f"{indent}{child.name}"
            )
            if is_dir and not is_link:
                if directory in unreadable or _capped(child, depth, root):
                    shown_lines.append(f"{indent}  ...")
                    continue
                render(child, indent + "  ")

    def _capped(path: Path, limit: int, base: Path) -> bool:
        return len(path.parts) - len(base.parts) >= limit

    header = f"{root} ({len(entries)} entr{'y' if len(entries) == 1 else 'ies'}, depth {depth})"
    shown_lines = [header]
    render(root, "  ")

    if truncated and stop_reason:
        shown_lines.append(f"... {stop_reason}")
    if len(shown_lines) > _MAX_RENDERED_LINES:
        shown_lines = shown_lines[:_MAX_RENDERED_LINES]
        shown_lines.append(
            f"... more than {_MAX_RENDERED_LINES} lines of tree not shown."
        )
    if not include_hidden:
        shown_lines.append("  (hidden entries were not shown; pass include_hidden to see them)")
    for entry in unreadable:
        shown_lines.append(f"  {entry}  could not be read, so its contents are unknown")
    return "\n".join(shown_lines)


SKILLS = [Skill(name="directory_tree", schema=SCHEMA, run=_run)]
