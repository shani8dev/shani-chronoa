"""Skill: report a file's size, type, timestamps and permissions.

**This is a genuine gap, not a convenience.** `list_directory` reports a
directory entry's kind, size and whether it is a link, and nothing in the
package surfaces when a file was last *written* or who can *read* it. Those are
the two questions people actually ask about a file they are looking at - "when
did I last edit this" and "can anyone else on this machine see it" - and the
`ls -l` habit is the reason the question exists at all.

`os.stat` is read, not inferred: the mode is the real `st_mode` and the times
are the real `st_mtime`/`st_atime`/`st_ctime`. Nothing here guesses.

Two honesty rules that follow from what `stat` does and does not say:

- **`lstat` for the link, `stat` for the target.** A symlink's own mtime is
  when the link was made, not when the file it points at was written.
  Reporting the link's timestamp as the file's would be confidently wrong
  about exactly the case a user asks about, so both are reported and named.
- **Mode is not ACLs and not capabilities.** `st_mode` says what the owner,
  group and others are permitted. It says nothing about a POSIX ACL, a
  `facl` mask, an immutable attribute, or a mount made `nosuid`. So the reply
  reports the mode and says that other mechanisms are not considered, rather
  than concluding "this file is private" from a bitfield that cannot support
  that conclusion.
"""

from __future__ import annotations

import os
import stat as stat_mod
import time
from pathlib import Path

from shani_chronoa import files
from shani_chronoa.skills import Skill


def _describe_type(mode: int) -> str:
    if stat_mod.S_ISLNK(mode):
        return "symlink"
    if stat_mod.S_ISDIR(mode):
        return "directory"
    if stat_mod.S_ISFIFO(mode):
        return "fifo"
    if stat_mod.S_ISSOCK(mode):
        return "socket"
    if stat_mod.S_ISBLK(mode):
        return "block device"
    if stat_mod.S_ISCHR(mode):
        return "character device"
    if stat_mod.S_ISREG(mode):
        return "regular file"
    return "unknown"


def _when(timestamp: float) -> str:
    """An absolute time and how long ago it was.

    The absolute one is the answer; the relative one is what makes it usable.
    A bare timestamp is a fact the caller has to subtract from the current
    time themselves, and "3 hours ago" is the form the question was asked in.
    """
    absolute = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(timestamp))
    delta = max(0.0, time.time() - timestamp)
    if delta < 60:
        return f"{absolute} ({int(delta)}s ago)"
    if delta < 3600:
        return f"{absolute} ({int(delta // 60)} min ago)"
    if delta < 86400:
        return f"{absolute} ({int(delta // 3600)} h ago)"
    return f"{absolute} ({int(delta // 86400)} days ago)"


def _mode_text(mode: int) -> str:
    owner = "r" if mode & stat_mod.S_IRUSR else "-"
    owner += "w" if mode & stat_mod.S_IWUSR else "-"
    owner += "x" if mode & stat_mod.S_IXUSR else "-"
    group = "r" if mode & stat_mod.S_IRGRP else "-"
    group += "w" if mode & stat_mod.S_IWGRP else "-"
    group += "x" if mode & stat_mod.S_IXGRP else "-"
    other = "r" if mode & stat_mod.S_IROTH else "-"
    other += "w" if mode & stat_mod.S_IWOTH else "-"
    other += "x" if mode & stat_mod.S_IXOTH else "-"
    return f"{stat_mod.S_IMODE(mode):04o} ({owner}{group}{other})"


def _own(uid: int) -> str:
    try:
        import pwd
        return pwd.getpwuid(uid).pw_name
    except (ImportError, KeyError, OSError):
        return f"uid {uid}"


def _group(gid: int) -> str:
    try:
        import grp
        return grp.getgrgid(gid).gr_name
    except (ImportError, KeyError, OSError):
        return f"gid {gid}"


SCHEMA = {
    "type": "function",
    "function": {
        "name": "get_file_info",
        "description": (
            "Report one file's type, size, when it was last modified, and who "
            "can read it. Use this to answer 'when did I last edit this' or "
            "'who else can read this file' - no other skill reports "
            "timestamps or permissions."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "The file to describe."},
            },
            "required": ["path"],
        },
    },
}


def _run(arguments: dict) -> str:
    try:
        # Confinement is checked on the *resolved* path, not on `named`: a
        # symlink sitting inside the home directory and pointing at /etc/shadow
        # would otherwise pass. `named` is only what `lstat` reads, because
        # resolving is what erases the fact that it is a link.
        named = files.expand(arguments.get("path") or "")
        files.resolve_in_home(arguments.get("path") or "")
    except files.PathProblem as exc:
        return str(exc)
    target = named

    try:
        link_stat = os.lstat(target)
    except FileNotFoundError:
        return f"{target} does not exist."
    except PermissionError:
        return (
            f"Could not stat {target}: permission denied. A parent directory "
            f"does not let this user through, so its details are unknown - "
            f"which is not the same as it not existing."
        )
    except OSError as exc:
        return files.describe(exc, target, "inspect")

    kind = _describe_type(link_stat.st_mode)
    lines = [f"{target}  ({kind})"]

    if stat_mod.S_ISREG(link_stat.st_mode):
        lines.append(f"  size: {link_stat.st_size} bytes ({files.human_size(link_stat.st_size)})")
    lines.append(f"  mode: {_mode_text(link_stat.st_mode)}")
    lines.append(f"  owner: {_own(link_stat.st_uid)}  group: {_group(link_stat.st_gid)}")
    lines.append(f"  modified: {_when(link_stat.st_mtime)}")
    lines.append(f"  accessed: {_when(link_stat.st_atime)}")
    lines.append(
        f"  status changed: {_when(link_stat.st_ctime)}"
        + (" (a rename, a permission change, or a write - not only a write)"
           if stat_mod.S_ISREG(link_stat.st_mode) else "")
    )
    lines.append(f"  links: {link_stat.st_nlink}")

    if stat_mod.S_ISLNK(link_stat.st_mode):
        try:
            destination = os.readlink(target)
        except OSError as exc:
            lines.append(f"  points at: could not be read ({type(exc).__name__})")
        else:
            lines.append(f"  points at: {destination}")
            # The link's own mtime is when the link was made. Reporting it as
            # "modified" without saying which file it describes is the trap.
            try:
                target_stat = os.stat(target)
            except OSError:
                lines.append(
                    "  target: unreachable - the link points at nothing that "
                    "can be read, so the file's own size and times are unknown."
                )
            else:
                lines.append(
                    f"  target type: {_describe_type(target_stat.st_mode)}"
                )
                if stat_mod.S_ISREG(target_stat.st_mode):
                    lines.append(
                        f"  target size: {target_stat.st_size} bytes "
                        f"({files.human_size(target_stat.st_size)})"
                    )
                lines.append(f"  target modified: {_when(target_stat.st_mtime)}")
                lines.append(
                    "  note: the times above the 'target' line describe the "
                    "link itself, not the file it points at."
                )

    lines.append(
        "  Note: mode bits are not ACLs, immutable attributes, or mount flags, "
        "so this is what the permission bits say - not a guarantee that nobody "
        "else can read it."
    )
    return "\n".join(lines)


SKILLS = [Skill(name="get_file_info", schema=SCHEMA, run=_run)]
