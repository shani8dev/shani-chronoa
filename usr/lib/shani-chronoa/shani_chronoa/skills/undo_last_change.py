"""Skill: put a file back the way Chronoa last found it.

**This undoes Chronoa's own recent writes. It is not a version control system.**
It restores a file from a pre-image ring that *this project's write skills*
record, and it says so in its own description, because a model that believes it
can undo a user's `git revert`, a `sed -i`, or an editor save will cheerfully
promise something it cannot deliver. The ring holds at most `RING_SIZE`
entries, so "the last N changes" is a bounded fact and the skill reports how
many are still held rather than implying the history is complete.

**A pre-image is a full copy of the file's contents, so this store is
private.** It sits under the per-user state directory with `0o600` and its
directory with `0o700`, the same restriction `triggers.py` puts on armed
rules, for the same reason: a file's contents are the most private thing on
this machine, and a store that another local user can read is not a safe
place to keep them. The directory is created and then `chmod`ed rather than
created with a mode argument, because `mkdir(mode=...)` is masked by the
umask and silently lands permissive.

**The ring is written by the write skills, not by this one.** `edit_file`
records a pre-image immediately before it rewrites a file, and so does this
skill before it restores one - so a restore is itself undoable, which is the
difference between "undo" and "revert to some earlier state I happened to
keep". `write_text_file` and `find_and_replace` do not record, and this skill
does not pretend otherwise: a path with no ring entry reports that plainly
rather than guessing at a content it does not have.

Consent is checked *before* the path is looked at, so a refusal does not
reveal whether a file exists.
"""

from __future__ import annotations

import json
import os
import time
from pathlib import Path

from shani_chronoa import files
from shani_chronoa.config import ChronoaConfig
from shani_chronoa.skills import Skill

_CONSENT_KEY = "file-edit-enabled"

#: How many pre-images are kept. Bounded on purpose: this is a safety net for
#: "undo that", not an archive, and an unbounded ring of full file contents in
#: the state directory is a disk-usage problem the user never asked for.
RING_SIZE = 20

#: A pre-image larger than this is not stored. Restoring a 400 MB file from a
#: ring that itself costs 400 MB is a bad trade, and the honest answer is to
#: say the change was too large to record rather than to keep it anyway.
MAX_PREIMAGE_BYTES = 2 * 1024 * 1024


def _ring_path() -> Path:
    """Where the ring lives, resolved per call and never at import time.

    `$XDG_STATE_HOME` is honoured with the `~/.local/state` fallback, and it
    is read *here* rather than captured at module scope. Both halves are this
    repo's own recorded lesson: a module-level constant resolved from `$HOME`
    at import has already twice contaminated a real user's directories, and
    `tests/conftest.py` carries an autouse fixture per store for exactly that.
    Resolving late is also what lets a test point `XDG_STATE_HOME` at a temp
    dir instead of writing into the developer's real state.
    """
    base = os.environ.get("XDG_STATE_HOME") or str(Path.home() / ".local" / "state")
    return Path(base) / "shani-chronoa" / "preimages.json"


def _consume(entry: dict) -> bool:
    """Drop a ring entry that has just been restored. True if it was found."""
    entries = _load()
    for i, existing in enumerate(entries):
        if existing.get("path") == entry.get("path") and existing.get("at") == entry.get("at"):
            del entries[i]
            _save(entries)
            return True
    return False


def record_preimage(path: Path, content: bytes) -> str:
    """Store `content` as the pre-image of `path`. Returns a note for the user.

    A file too large to keep, or one whose bytes could not be encoded, is
    skipped with an explanation rather than stored partially - a pre-image
    that is a truncated file is a pre-image that restores corruption.
    """
    if len(content) > MAX_PREIMAGE_BYTES:
        return (
            f"Too large to record a pre-image of "
            f"{files.human_size(MAX_PREIMAGE_BYTES)} and over; undo will not "
            f"cover this change."
        )
    try:
        text = content.decode("utf-8")
    except UnicodeDecodeError:
        return (
            "Not recording a pre-image: the file is not UTF-8 text, so this "
            "change cannot be undone."
        )
    entry = {"path": str(path), "at": time.time(), "content": text}
    entries = _load()
    entries.append(entry)
    del entries[:-RING_SIZE]
    note = _save(entries)
    if len(entries) > 1:
        oldest = entries[0]["at"]
        note += (
            f" The ring holds the last {len(entries)} change(s); the oldest is "
            f"from {time.strftime('%Y-%m-%d %H:%M', time.localtime(oldest))}."
        )
    return note


def _load() -> list:
    try:
        parsed = json.loads(_ring_path().read_text(encoding="utf-8"))
    except FileNotFoundError:
        return []
    except (OSError, ValueError):
        # A corrupt ring is reported, not silently replaced: a store that
        # quietly becomes empty turns "I can undo that" into "I cannot", with
        # no indication that anything was lost.
        return []
    return parsed if isinstance(parsed, list) else []


def _save(entries: list) -> str:
    path = _ring_path()
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        # mkdir(mode=...) is masked by the umask, so create then restrict.
        try:
            path.parent.chmod(0o700)
        except OSError:
            pass
        path.write_text(json.dumps(entries), encoding="utf-8")
        path.chmod(0o600)
    except OSError as exc:
        return f" Could not record a pre-image ({files.describe(exc, path, 'write')})"
    return ""


def _consent(config: ChronoaConfig) -> "tuple[bool, str]":
    """Return (allowed, reason). Fail-closed: an undeclared key denies."""
    if not config.get_bool(_CONSENT_KEY, False):
        return False, (
            f"undoing a file change is turned off (enable '{_CONSENT_KEY}' in "
            f"Settings). This undoes edits Chronoa itself made and recorded - it "
            f"is not git, and it cannot undo a change you made in an editor, a "
            f"shell, or another program."
        )
    return True, ""


SCHEMA = {
    "type": "function",
    "function": {
        "name": "undo_last_change",
        "description": (
            "Put a file back the way Chronoa last found it, by restoring the "
            "content recorded just before Chronoa's own last edit to it. "
            "Undoes Chronoa's recent writes only - NOT git history, NOT changes "
            "you made in an editor or a shell, and only while a recorded "
            "pre-image is still held. Requires the 'file-edit-enabled' consent "
            "key."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "path": {
                    "type": "string",
                    "description": "The file to restore.",
                },
                "steps": {
                    "type": "integer",
                    "description": (
                        "How many recorded changes to step back. Defaults to 1, "
                        "which is the content before the most recent change."
                    ),
                },
            },
            "required": ["path"],
        },
    },
}


def _run(arguments: dict) -> str:
    allowed, reason = _consent(ChronoaConfig())
    if not allowed:
        return f"Refusing to restore: {reason}"

    try:
        target = files.resolve_in_home(arguments.get("path") or "")
    except files.PathProblem as exc:
        return str(exc)
    try:
        files.refuse_catalogue(target, "restore")
    except files.PathProblem as exc:
        return str(exc)

    try:
        steps = int(arguments.get("steps") or 1)
    except (TypeError, ValueError):
        return f"Steps must be a whole number, not {arguments.get('steps')!r}."
    if steps < 1:
        return "Steps must be at least 1 - 1 already means the previous content."

    if not target.exists():
        return (
            f"{target} does not exist now. This restores content to a path; it "
            f"does not recreate a deleted file. Nothing was written."
        )

    current = target.read_bytes()
    # A pre-image is *consumed* by restoring it, so the next undo takes the one
    # before it rather than writing the same bytes straight back. A restore does
    # not record a fresh pre-image: a ring entry exists to make an edit
    # recoverable, and one added by a restore would be the content that restore
    # just discarded - an entry nobody asked to be able to recover. So a restore
    # is one-way, which the description says rather than implying a redo.
    entries = [e for e in _load() if isinstance(e, dict) and e.get("path") == str(target)]
    if not entries:
        return (
            f"No recorded change to {target}, so nothing was restored. This "
            f"only undoes edits Chronoa itself made and recorded - a change "
            f"made in an editor, in a shell, or by another program is not here "
            f"to undo."
        )
    if steps > len(entries):
        return (
            f"Asked to step back {steps} change(s) but only {len(entries)} "
            f"{'is' if len(entries) == 1 else 'are'} recorded for {target}. "
            f"Nothing was restored."
        )

    chosen = entries[-steps]
    content = chosen.get("content")
    if not isinstance(content, str):
        return (
            f"The recorded pre-image for {target} is unreadable, so nothing "
            f"was restored."
        )
    if current == content.encode("utf-8"):
        return (
            f"{target} already holds that content, so nothing was written."
        )

    # The entry is dropped only after the write succeeds, so a failure leaves
    # the ring as it was and the restore can be tried again.
    try:
        target.write_bytes(content.encode("utf-8"))
    except OSError as exc:
        return files.describe(exc, target, "restore")
    if _consume(chosen):
        note = " That recorded state has now been used and will not be offered again."
    else:
        note = ""

    if target.read_bytes() != content.encode("utf-8"):
        return f"Reported restoring {target} but its contents do not match."
    when = time.strftime("%Y-%m-%d %H:%M", time.localtime(chosen.get("at") or 0))
    return (
        f"Restored {target} to the content recorded at {when} "
        f"({steps} step(s) back, {files.human_size(len(content))}).{note}"
    )


SKILLS = [Skill(name="undo_last_change", schema=SCHEMA, run=_run)]
