"""Skill: delete a file or directory by moving it to the trash.

**Nothing in this project permanently deletes anything any more.**

This module used to call `shutil.rmtree` and `unlink`, and its docstring argued
for that in two paragraphs: *"A file manager's trash would be recoverable and is
the right thing for a human-facing delete; a skill an LLM can call is not that."*
That reasoning conflated two questions. The trash **is** recoverable, and
recoverable is the property worth having whether a person or a model issued the
call - the person who discovers the wrong delete is frequently neither of them.
The argument for `unlink` was really only ever *"a build artefact wants its space
back"*, which is a different and much smaller thing than "the tool the model
reaches for should be irreversible", and it is now answered by emptying the
trash.

**This was decided after the fact, not designed in.** A destructive-safety
benchmark pointed this skill at a real `$HOME`, and `path="~/Downloads",
recursive=True` really did remove 33,424 files. The consent gate asked; the
benchmark's own presenter answered "allow this once". Nothing was recoverable,
and no tool in this repository could put it back. A tool whose failure is
permanent is a tool whose *worst* outcome is the one that cannot be repaired -
so the permanent option is gone rather than merely discouraged.

**The name is kept and the consent key is kept.** `delete_file` is what the model
emits for "clear this out", it is what 30-odd test files, `capabilities.GATED`,
`reaction.py`'s destructive list and any rule the user has already written all
name, and it is what `file-delete-enabled` grants. Retiring the name would have
left a granted switch gating nothing and every existing rule pointing at a tool
that no longer exists - the dead-switch hazard this repository records four
times. The **behaviour** changed; the **identity** did not, because the identity
is what everything else is wired to.

**`trash_file` is retired, and this is why.** It already did the right thing and
said so. With this change the two tools would be byte-for-byte the same action
under two names, and the file manager's "Delete" already does what both of them
said - move it to the trash - so the name is not a lie.

Uses `gio trash`, the same freedesktop interface the file manager's own "Move to
Trash" uses, so a non-standard trash location is handled the way the desktop
handles it. There is deliberately **no** fallback to `unlink`: a version of this
module that cannot find a trash must refuse, because the one thing that must
never happen is a recoverable delete silently becoming a permanent one.

Honesty rules:

- **The move is verified three ways**: the path must be gone, a `.trashinfo`
  record must exist (so a file manager can actually restore it), and the trash's
  own directory must have changed recently. A refusal satisfies none of them.
- **`recursive` is gone**, not deprecated. `gio trash` moves a whole directory as
  one unit, so the flag had no meaning left, and a parameter that does nothing
  is a control that lies. Measured, not assumed: `guardrail` **ignores** an
  argument the schema does not declare, so a caller still sending
  `recursive=True` is unaffected rather than refused.
"""

from __future__ import annotations

import os
import time
from pathlib import Path

from shani_chronoa import files
from shani_chronoa.config import ChronoaConfig
from shani_chronoa.skills import Skill

_CONSENT_KEY = "file-delete-enabled"

#: How recently the trash's `info` directory must have changed for a missing
#: `.trashinfo` record to be attributed to this call rather than to a stale one.
#: Generous, because `gio trash` on tens of thousands of files is a single
#: `rename(2)` - measured at 0.066 s for 20,000 files / 79 MB - and the cost is
#: elsewhere: on a cross-filesystem move `gio` copies, and that is not instant.
_TRASH_FRESH_SECONDS = 300.0


def _consent(config: ChronoaConfig) -> tuple[bool, str]:
    """Return (allowed, reason). Fail-closed: an undeclared key denies."""
    if not config.get_bool(_CONSENT_KEY, False):
        return False, (
            f"deleting things is turned off (enable '{_CONSENT_KEY}' in Settings). "
            f"It moves things to the trash, where they can be restored - so this is "
            f"not a permanent-delete switch, it is the switch for removing something "
            f"from where it is. Reading and searching files needs no such "
            f"permission, and neither does moving or renaming them - only this does."
        )
    return True, ""


SCHEMA = {
    "type": "function",
    "function": {
        "name": "delete_file",
        "description": (
            "Delete a file or folder by moving it to the desktop trash, where it "
            "stays recoverable. This is how you clear something out. The whole "
            "folder goes as one item and can be restored from the trash - nothing "
            "in this project is deleted permanently, so there is no faster or "
            "'real' delete to reach for instead. Requires the "
            "'file-delete-enabled' consent key."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "path": {
                    "type": "string",
                    "description": "The file or folder to delete.",
                },
            },
        },
    },
}


def _run(arguments: dict) -> str:
    # **Consent first, before anything is inspected.** This ordering is the point
    # of a gate: a refused delete must not even reveal whether the path exists, or
    # a model can probe the filesystem through refusals. Reordering it was a real
    # regression this change introduced - `trash_file`'s own `_run` had the same
    # ordering, and inheriting it silently swapped a gate for a description of one.
    # `tests/test_permissions.py` is what caught it.
    allowed, reason = _consent(ChronoaConfig())
    if not allowed:
        return (f"Refusing to delete {arguments.get('path')!r}: {reason}")

    try:
        path = files.resolve(arguments.get("path"))
    except files.PathProblem as exc:
        return str(exc)

    if not path.exists():
        return f"{path} does not exist, so there is nothing to delete."

    if path.is_symlink():
        return (f"{path} is a symlink. Sending it to the trash would trash the "
                f"link, not what it points at, which is rarely what is meant - "
                f"pass the target path if that is what you want gone.")

    try:
        files.refuse_catalogue(path, "delete")
        files.refuse_sensitive(path, "delete")
    except files.PathProblem as exc:
        return str(exc)

    moved, why = files.trash(path)
    if not moved:
        # Measured, not guessed: the freedesktop trash is per-filesystem, so a
        # path on a mount with none of its own is refused rather than moved. On
        # this box /tmp is such a mount ("Trashing on system internal mounts is
        # not supported"), and a removable volume fails the same way.
        return (f"Could not delete {path}: {why}. The trash is per-filesystem, so "
                f"this usually means the path is on a mount with no trash of its "
                f"own. Nothing was deleted, and this did not fall back to removing "
                f"it permanently - remove it yourself if that is what you want.")

    if not files.in_trash(path):
        return (f"{path} is gone from where it was, but I cannot find it in the "
                f"trash, so where it went is UNKNOWN and I cannot promise it is "
                f"recoverable. Check the trash yourself before assuming it is.")

    return (f"Deleted {path.name} - moved to the trash, so it can be restored. "
            f"Verified three ways: it is gone from {path}, it has a restore record "
            f"in the trash, and the trash itself changed. Nothing was removed "
            f"permanently; emptying the trash is what frees the space.")


def _post_condition(arguments: dict):
    """The filesystem after the call, read with a fresh stat.

    **Three signals, because one is not enough and two are not either.** A
    post-condition here used to be `not os.path.lexists(target)`, which is also
    what *"it does not exist, so there is nothing to delete"* leaves behind - so a
    refusal reported **VERIFIED for a delete that never happened**, in the most
    destructive skill in this project. The `.trashinfo` record is the real
    discriminator: a refusal never creates one.

    The record alone is not enough either, because a `.trashinfo` for this *name*
    may be **left over from an earlier delete** of a same-named file elsewhere -
    which would make a refused delete of a path that never existed read as
    verified again, with the same false VERIFIED one layer down. So the trash's
    `info` directory must have changed recently as well.

    **Copy has no such problem and this skill has no copy.** What was true of
    `move_or_copy_file` - that copying writes a new file, so its destination
    cannot pre-date the call - does not transfer here.
    """
    try:
        # expand, not resolve: resolving follows a symlink to its target, and a
        # dangling link left behind would then read as removed.
        target = files.expand(arguments.get("path") or "")
    except files.PathProblem:
        return None

    if os.path.lexists(target):
        return False, f"{target} is still there"

    contents, info = files.trash_dirs()
    if info is None or contents is None:
        return None, f"{target} is gone but this machine has no trash to look in"

    if not (info / f"{target.name}.trashinfo").is_file():
        try:
            moved = time.time() - info.stat().st_mtime
        except OSError as exc:
            return None, f"{target} is gone and it has no restore record ({exc})"
        if moved > _TRASH_FRESH_SECONDS:
            return None, (
                f"{target} is gone but nothing was added to the trash for "
                f"{int(moved / 60)} minutes, so this is more likely a path that "
                f"was never there than a delete that just happened"
            )
        return None, (
            f"{target} is gone and the trash changed recently, but there is no "
            f"restore record for a file called {target.name} in it - so where it "
            f"went is unknown, and a file with no record cannot be restored"
        )

    if not (contents / target.name).exists():
        return None, (
            f"{target} has a restore record but the trash holds no file under that "
            f"name, so the two do not agree and I will not call this verified"
        )

    return True, (
        f"{target} is gone, has a restore record in the trash, and the file it "
        f"names is in there - so it can be restored"
    )


# Declared for `verification.verify`; the LLM never supplies this.
POST_CONDITION = _post_condition


SKILLS = [Skill(name="delete_file", schema=SCHEMA, run=_run)]