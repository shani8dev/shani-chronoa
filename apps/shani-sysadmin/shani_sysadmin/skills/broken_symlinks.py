"""Skill: which shortcuts point at nothing?

Nothing here answered it. `find_files` searches names and `get_file_info`
describes one object; `cleanup_report` counts size. A dangling symlink is the
usual reason a launcher or a "Documents" shortcut stops working, and it is also
invisible to every size-based cleanup answer - a symlink costs no space, so
"why is my disk full" never finds it and "why doesn't this open" has nothing
to ask.

**Four shapes, and only two of them are broken.** A symlink's target can:

    works    resolve to something that exists
    dangling resolve to nothing at all        -> broken
    cycle    resolve to a path that contains itself  -> broken
    escapes  resolve to something **outside** the tree that was searched

**The last one is the reason this skill has to be careful.** A link to
`../elsewhere/file` is not broken - it points somewhere the walk did not go,
and reporting it as dangling is the confident wrong answer this package keeps
recording under other names. It is listed separately, named as *unverified
from here*, and never counted in the broken total.

**It never removes one.** A dangling link is occasionally the only record of
where something used to be, and which of several dangling links was the
important one is not a question a tool can answer by deleting.
"""

from __future__ import annotations

import errno
import os
from pathlib import Path

from shani_chronoa import files
from shani_chronoa.skills import Skill

#: Above this the walk stops early and says so, rather than returning a short
#: list that looks complete.
_CEILING = 5000

#: `OSError` errnos that mean "the target is not there". **Anything else - most
#: usefully EACCES - means the target could not be read, which is a fourth state
#: and not a broken one.** The first version defined this set and then compared
#: `exc.errno == errno.ELOOP` inline, so the constant was documentation and not
#: enforcement: dropping ELOOP from it left the whole suite green.
_BROKEN = {errno.ENOENT, errno.ENOTDIR, errno.ELOOP, errno.ENAMETOOLONG}

#: The classification, in the order the answer leads with.
_KINDS = ("dangling", "cycle", "unreadable", "escapes", "works")


def _classify(link: Path, root: Path) -> str:
    """`dangling`, `cycle`, `escapes` or `works`.

    **`resolve()` is not used for the answer** - it raises on a cycle and
    silently returns a path that does not exist in strict mode, so the
    distinction has to come from the errno the kernel actually gave.
    """
    try:
        target = os.readlink(link)
    except OSError:
        return "dangling"
    # Absolute targets resolve against the filesystem root, relative ones
    # against the link's own directory - which is what `..` means to a person.
    if not os.path.isabs(target):
        target = os.path.join(os.path.dirname(str(link)), target)
    try:
        os.stat(target)
    except OSError as exc:
        if exc.errno not in _BROKEN:
            # A permission failure is not "the target is missing".
            return "unreadable"
        return "cycle" if exc.errno == errno.ELOOP else "dangling"
    # It resolves. Whether it resolves *inside the searched tree* is the
    # question that decides whether this answer can vouch for it.
    try:
        Path(os.path.realpath(target)).relative_to(Path(os.path.realpath(root)))
    except ValueError:
        return "escapes"
    return "works"


def _walk(top: Path) -> "tuple[dict, int]":
    """(by kind, links seen). Non-link files are ignored entirely - this is not
    a directory listing."""
    by_kind = {kind: [] for kind in _KINDS}
    seen = 0
    truncated = False
    for root, dirs, names in os.walk(top, onerror=lambda _e: None):
        # **`dirs` as well as `names`.** `os.walk` puts a symlink that points at
        # a *directory* in `dirs`, so scanning `names` alone never sees it - and
        # measured on the first fixture, a `ln -s .` self-cycle was invisible
        # while its sibling links were all found. The cycle case is the one that
        # most often points at a directory.
        for name in list(names) + list(dirs):
            path = Path(root) / name
            try:
                if not path.is_symlink():
                    continue
            except OSError:
                continue
            seen += 1
            by_kind[_classify(path, top)].append(path)
            if seen >= _CEILING:
                truncated = True
                break
        if truncated:
            break
    return by_kind, seen


def _run_skill(arguments: dict) -> str:
    raw = str(arguments.get("path") or "").strip()
    try:
        top = files.expand(raw or "~")
        if not top.is_dir():
            return f"{top} is not a directory, so there are no links in it."
    except files.PathProblem as exc:
        return str(exc)

    by_kind, seen = _walk(top)
    if not seen:
        return (f"There are no symbolic links under {top}. A cleanup that finds "
                "nothing because there is nothing is a different answer from "
                "one that could not look, and this is the first.")

    broken = by_kind["dangling"] + by_kind["cycle"]
    lines = [f"{seen} symbolic link(s) under {top}."]
    lines.append(f"  **{len(broken)} broken** ({len(by_kind['dangling'])} "
                 "pointing at something that does not exist, "
                 f"{len(by_kind['cycle'])} in a cycle)"
                 + (f", {len(by_kind['unreadable'])} whose target could not "
                    f"be read" if by_kind["unreadable"] else ""))
    lines.append(f"  {len(by_kind['works'])} resolve to something inside "
                 f"{top}")
    lines.append(f"  {len(by_kind['escapes'])} point outside it - "
                 "**not broken**, and not verifiable from here either")

    for kind, blurb in (
            ("dangling", "**Dangling** - the target does not exist, so "
                         "following this link fails:"),
            ("cycle", "**Cyclic** - the target contains the link itself:")):
        if not by_kind[kind]:
            continue
        lines.append("")
        lines.append(blurb)
        for path in sorted(by_kind[kind])[:40]:
            try:
                target = os.readlink(path)
            except OSError:
                target = "(unreadable)"
            lines.append(f"- {path}  ->  {target}")
        if len(by_kind[kind]) > 40:
            lines.append(f"  ... {len(by_kind[kind]) - 40} more not shown.")

    if by_kind["escapes"]:
        lines.append("")
        lines.append("**Pointing outside the tree that was searched** - these "
                     "were not followed, because the answer would then be about "
                     "a directory nobody asked about:")
        for path in sorted(by_kind["escapes"])[:20]:
            try:
                target = os.readlink(path)
            except OSError:
                target = "(unreadable)"
            lines.append(f"- {path}  ->  {target}")
        if len(by_kind["escapes"]) > 20:
            lines.append(f"  ... {len(by_kind['escapes']) - 20} more not shown.")

    if seen >= _CEILING:
        lines.append("")
        lines.append(f"Stopped at {_CEILING} links; this is not a complete "
                     "list. Narrow the path and ask again.")
    lines.append("")
    lines.append("None of these has been removed - a dangling link is "
                 "occasionally the only record of where something used to be.")
    return "\n".join(lines)


SCHEMA = {
    "type": "function",
    "function": {
        "name": "broken_symlinks",
        "description": (
            "Find symbolic links that point at nothing under a directory. Use "
            "for 'this shortcut does not work', 'clean up my dead links', "
            "'what is broken in here'. Separates four cases: resolving links, "
            "dangling ones, cycles, and links whose target lies OUTSIDE the "
            "searched tree - which are not broken, only unverified from here. "
            "Never removes anything."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "path": {"type": "string",
                         "description": ("Directory to search. Defaults to your "
                                         "home directory.")},
            },
        },
    },
}

SKILLS = [Skill(name="broken_symlinks", schema=SCHEMA, run=_run_skill)]
