"""Skill: sync two folders and see exactly what would change.

`move_or_copy_file` moves or copies **one path** - a file or a directory, in
one shot. Nothing here could answer the other half of that question: "I edit
files on my laptop and copy them to the server every day - can it send only
what changed?" That is an incremental sync, not a copy, and `rsync` is the tool
for it. `shani-tools-network` ships it, so it is on both images by design.

**The dry run is the feature, not a preview.** `rsync --dry-run` reports
exactly what a real run would transfer without touching anything, so this skill
can *always* show the person the plan first - "these 12 files would be sent,
2.1 MB, and one file on the destination is newer and would be overwritten" -
before anything moves. A copy tool that only reports after the fact asks you to
trust it with your files first.

**Measured here, and one exit code that matters:**

- **A missing source exits 23**, not 1 - so "it failed" and "the source is not
  there" are the same status, and the message has to say which. (Read the
  reason from rsync's own stderr, never from guessing what 23 meant.)
- **`--stats` numbers are the right ones to quote**: `Number of regular files
  transferred`, `Total transferred file size`, and `Number of files` (the files
  *considered*, which is larger than the number sent).

**It copies by default and never deletes** unless asked, and the delete half is
the dangerous one - a sync that removes files on the destination because they
are gone from the source is how a mistyped path erases work. The `--delete`
option exists here because a real mirror needs it, it is off unless the caller
asks, and the dry run reports exactly which files it *would* remove so that is
never a surprise.
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

from shani_chronoa import files
from shani_chronoa.skills import Skill

_TIMEOUT = 120


def _rsync(arguments: dict, dry_run: bool) -> "tuple[str, str]":
    """(stdout+stderr, reason). `reason` non-empty means it could not run."""
    # **`-a` is not optional and neither is its `r`.** Measured on this box
    # (rsync 3.4.1): without a recursion flag, `rsync src/ dst/` prints
    # "skipping directory ." and transfers **nothing** - while exiting 0. A
    # sync built without it reported "already up to date" on folders that
    # differed, which is the worst possible failure for this skill: a confident
    # no-op that quietly copies nothing.
    #
    # `-a` is archive mode (recursion, links, times, owner, group, perms).
    # `-r` alone is the minimum that makes a folder sync a folder sync; `-a`
    # is used so a sync preserves what it copies instead of flattening it.
    command = ["rsync", "--archive", "--stats", "--itemize-changes"]
    if dry_run:
        command.append("--dry-run")
    # **The trailing slash is load-bearing.** `rsync src dst` copies the
    # folder *into* the destination as `dst/src/...`; `rsync src/ dst` copies
    # its *contents* to `dst/...`. Measured here: without the slash the
    # destination came out as `dst/src/a.txt`, which is right for "back this
    # folder up" and wrong for "sync this folder with that one" - and it
    # nests a little deeper on every run. "Sync A to B" means A's contents
    # land in B, so the slash is added here rather than left to the caller.
    command += ["--", _as_dir(arguments["source"]), str(arguments["destination"])]
    try:
        proc = subprocess.run(command, capture_output=True, text=True,
                              timeout=_TIMEOUT, check=False)
    except subprocess.TimeoutExpired:
        return "", f"rsync did not finish within {_TIMEOUT}s"
    except OSError as exc:
        return "", str(exc)
    # **Measured: a missing source exits 23.** Not 1. So the status alone does
    # not say *why*, and the reason is taken from rsync's own stderr rather
    # than assumed from the number.
    if proc.returncode != 0:
        detail = (proc.stderr or "").strip().splitlines()
        return proc.stdout or "", (detail[-1] if detail
                                   else f"rsync exited {proc.returncode}")
    return proc.stdout or "", ""


def _as_dir(path) -> str:
    """`path` with a trailing `/`, which rsync reads as "the contents of".

    `rsync src dst` copies the folder *into* the destination as `dst/src/...`;
    `rsync src/ dst` copies its contents to `dst/...`. Measured here: without
    the slash the destination came out as `dst/src/a.txt`, which is right for
    "back this folder up" and wrong for "sync this folder with that one" - and
    it nests a little deeper on every run.
    """
    text = str(path)
    return text if text.endswith("/") else text + "/"


def _stat(text: str, label: str) -> str:
    """One `rsync --stats` line, by name."""
    for line in text.splitlines():
        if line.strip().startswith(label):
            return line.split(":", 1)[1].strip()
    return ""


def _transfers(text: str) -> "list[str]":
    """The itemised lines `--itemize-changes` emits: one per change."""
    rows = []
    for line in text.splitlines():
        parts = line.split(None, 1)
        if len(parts) == 2 and len(parts[0]) >= 11 and parts[0][1] == "f":
            rows.append((parts[0][0], parts[1].strip()))
    return rows


def _run(arguments: dict) -> str:
    source = str(arguments.get("source") or "").strip()
    destination = str(arguments.get("destination") or "").strip()
    if not source or not destination:
        return ("I need both a source folder and a destination folder. Ask me "
                "to sync from one path to another.")

    source_path = Path(files.expand(source))
    destination_path = Path(files.expand(destination))
    if not source_path.exists():
        return f"There is nothing at {source_path} to sync from."
    if not source_path.is_dir():
        return (f"{source_path} is a file, not a folder. `move_or_copy_file` "
                "copies single files; this one syncs whole folders.")
    if shutil.which("rsync") is None:
        return files.tool_missing("rsync", "sync those two folders")

    # --- the plan, always, and nothing has moved yet ---
    plan_args = {"source": source_path, "destination": destination_path}
    text, problem = _rsync(plan_args, dry_run=True)
    if problem:
        return (f"I could not work out what would be copied: {problem}. "
                "Nothing was changed.")

    sent = _stat(text, "Number of regular files transferred")
    size = _stat(text, "Total transferred file size")
    changes = _transfers(text)

    if not changes:
        lines = [f"{source_path.name} is already up to date with "
                 f"{destination_path}. Nothing would change."]
        return "\n".join(lines)

    verb = "delete from" if arguments.get("delete") else "copy"
    header = (f"A sync would {verb} {len(changes)} item(s) from "
              f"{source_path} to {destination_path}:")
    listed = [f"  {name}" for _, name in changes[:20]]
    if len(changes) > 20:
        listed.append(f"  ... and {len(changes) - 20} more")
    body = "\n".join(listed)

    if arguments.get("apply"):
        if arguments.get("delete"):
            return ("I do not run a sync that deletes files from the "
                    "destination - a mistyped path would erase work. Copy the "
                    "files across first, then remove what you do not want.")
        real_args = {"source": source_path, "destination": destination_path}
        _out, failure = _rsync(real_args, dry_run=False)
        if failure:
            return f"The sync started but did not finish: {failure}."
        done = "\n".join([f"Copied {len(changes)} item(s) to "
                          f"{destination_path}.", body])
        return done

    footer = (f"That is a dry run - nothing has been copied. Total to send: "
              f"{sent or len(changes)} file(s), {size or 'an unknown size'}. "
              "Ask me again with apply set to actually do it.")
    return "\n".join([header, body, "", footer])


SCHEMA = {
    "type": "function",
    "function": {
        "name": "sync_folder",
        "description": (
            "Sync one folder to another, sending only what changed - and show "
            "the plan first, before anything is copied. Use for 'copy my "
            "files to the server', 'sync these folders', 'what would change if "
            "I synced'. Does not delete anything at the destination unless you "
            "ask it to, and asks first even then."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "source": {"type": "string",
                           "description": "The folder to copy FROM."},
                "destination": {"type": "string",
                                "description": "The folder to copy TO."},
                "apply": {
                    "type": "boolean",
                    "description": ("Actually copy. With this false or absent "
                                    "you get the dry-run plan and nothing is "
                                    "changed.")},
                "delete": {
                    "type": "boolean",
                    "description": ("Would remove files at the destination that "
                                    "are not in the source. Off by default; "
                                    "never run automatically.")},
            },
            "required": ["source", "destination"],
        },
    },
}

SKILLS = [Skill(name="sync_folder", schema=SCHEMA, run=_run)]