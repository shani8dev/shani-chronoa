"""Skill: put a file or a setting back the way Chronoa last found it.

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
keep". `write_text_file` (when it replaces or appends to a file),
`find_and_replace` (each file it rewrites) and `office_document` record too;
binary pre-images (.docx, .xlsx) are kept as base64. A path with no ring entry
reports that plainly rather than guessing at a content it does not have.
`list=true` shows the undo points held - the checkpoint list cline and
gemini-cli offer, scoped to Chronoa's own writes.

Consent is checked *before* the path is looked at, so a refusal does not
reveal whether a file exists.

**No post-condition is declared, and that is deliberate.** A restore
consumes the very ring entry that names the content it wrote, so after
a successful restore the expected bytes exist nowhere the arguments
can reach - and the count of remaining entries cannot stand in for
them, because "asked for 2 steps with 1 recorded" (a refusal) and
"asked for 2 steps with 2 recorded, both consumed" (a success) leave
the ring in the same shape. A post-condition that guessed would verify
a refused call as done and a failed one as held, which is the exact
failure `verification.py` exists to prevent. The skill's own
read-back (`its contents do not match`) stays, so an in-process write
failure is still caught; what is not claimed is the post-hoc replay
check. A repeat of a restore within the replay window therefore asks
again and undoes one more step - which is what a person pressing
"undo" twice means, and the bounded, per-typed-request shape of this
skill makes that the honest behaviour rather than a hole.
"""

from __future__ import annotations

import base64
import json
import os
import time
from pathlib import Path

from shani_chronoa import files, undo_ring
from shani_chronoa.config import ChronoaConfig
from shani_chronoa.skills import Skill

_CONSENT_KEY = "file-edit-enabled"

#: How many pre-images are kept per kind. Kept here as the name callers know;
#: the bound itself lives in `undo_ring`, which owns the journal now.
RING_SIZE = undo_ring.MAX_PER_KIND

#: A pre-image larger than this is not stored (see `undo_ring`).
MAX_PREIMAGE_BYTES = undo_ring.MAX_PREIMAGE_BYTES


def _consume(entry: dict) -> bool:
    """Drop a ring entry that has just been restored. True if it was found."""
    return undo_ring.consume(entry)


def recent_changes() -> "list[tuple[Path, str, str]]":
    """The ring as `(path, before, after)`, newest last - a *preview* feed.

    Exists because the Diff panel needs the same two texts this module already
    stores, and the alternative was a second, weaker undo log that would drift
    from this one. `after` is the file as it is on disk *now*, read fresh: a
    ring entry with no file behind it (deleted, or moved) is skipped rather
    than guessed at, because a preview that invents the "after" side is worse
    than no preview.

    Binary entries are skipped: they decode to base64 here, and a diff of two
    base64 strings tells a person nothing.
    """
    out: "list[tuple[Path, str, str]]" = []
    for entry in _load():
        path = Path(str(entry.get("path") or ""))
        before = entry.get("content")
        if not isinstance(before, str) or not str(path):
            continue
        try:
            after = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        if after == before:
            continue                       # undone already, or never changed
        out.append((path, before, after))
    return out


#: The environment variable `tools.execute_tool` sets on the skill child so it
#: knows which turn asked it to write. **The only channel there is**: a skill
#: runs as `python3 -c`, so anything the app holds in memory is a different
#: process's memory.
TURN_ENV = undo_ring.TURN_ENV

MAX_TURN_CHARS = undo_ring.MAX_TURN_CHARS


def current_turn() -> str:
    """The turn this skill is running inside, or "" when none was set."""
    return undo_ring.current_turn()


def changes_for_turn(turn: str) -> "list[str]":
    """What this turn changed, newest last, without duplicates.

    **Attribution is only ever as good as the label, so an unattributed change
    is not claimed by anybody.** Entries with no `turn` are not returned
    rather than being folded into whichever turn asked.
    """
    return undo_ring.changes_for_turn(turn)


def record_preimage(path: Path, content: bytes) -> str:
    """Store `content` as the pre-image of `path`. Returns a note for the user.

    Delegates to `undo_ring.record_file`: the journal is shared with settings
    and trigger stores now, and keeping a second copy of the ring here is how
    the two trim rules would have drifted apart.
    """
    return undo_ring.record_file(path, content)


def _load() -> list:
    return undo_ring.load()


def _save(entries: list) -> str:
    return undo_ring._save(entries)


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
            "Put a file or a Chronoa setting back the way Chronoa last found "
            "it, by restoring the content or value recorded just before "
            "Chronoa's own last change to it. Undoes Chronoa's recent writes "
            "only - NOT git history, NOT changes you made in an editor or a "
            "shell, and only while a recorded pre-image is still held. "
            "Requires the 'file-edit-enabled' consent key."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "path": {
                    "type": "string",
                    "description": "The file to restore.",
                },
                "list": {
                    "type": "boolean",
                    "description": "List the recorded undo points (for path, setting, or every change) instead of restoring.",
                },
                "setting": {
                    "type": "string",
                    "description": (
                        "Restore this Chronoa setting to the value recorded "
                        "before Chronoa's own last change to it, instead of "
                        "restoring a file."
                    ),
                },
                "steps": {
                    "type": "integer",
                    "description": (
                        "How many recorded changes to step back. Defaults to 1, "
                        "which is the content before the most recent change."
                    ),
                },
            },
            "required": [],
        },
    },
}


def _list(arguments: dict) -> str:
    """The undo points held, newest first - the checkpoint list cline/gemini show, for Chronoa's own writes."""
    entries = [e for e in _load() if isinstance(e, dict)]
    raw = (arguments.get("path") or "").strip()
    setting = (arguments.get("setting") or "").strip()
    if setting:
        entries = [e for e in entries
                   if (e.get("kind") or "file") == "setting" and e.get("key") == setting]
    elif raw:
        try:
            target = files.resolve_in_home(raw)
        except files.PathProblem as exc:
            return str(exc)
        entries = [e for e in entries if e.get("path") == str(target)]
    if not entries:
        return "No undo points are held" + (
            f" for {setting}." if setting else f" for {raw}." if raw else ".")
    lines = [f"{len(entries)} undo point(s), newest first (steps=1 is the newest for each file or setting):"]
    seen: dict = {}
    for e in reversed(entries):
        when = time.strftime("%Y-%m-%d %H:%M", time.localtime(e.get("at") or 0))
        if (e.get("kind") or "file") == "setting":
            label = str(e.get("key"))
            seen[label] = seen.get(label, 0) + 1
            lines.append(f"- {when}  setting {label}: {e.get('before')!r} -> {e.get('after')!r}  (steps={seen[label]})")
            continue
        label = str(e.get("path"))
        seen[label] = seen.get(label, 0) + 1
        size = len(e.get("content") or "") or len(e.get("content_b64") or "") * 3 // 4
        lines.append(f"- {when}  {label}  (steps={seen[label]}, {files.human_size(size)} before)")
    return "\n".join(lines)


def _restore_setting(arguments: dict) -> str:
    """Put one setting back to the value recorded before Chronoa changed it."""
    key = (arguments.get("setting") or "").strip()
    entries = [e for e in _load() if isinstance(e, dict)
               and (e.get("kind") or "file") == "setting" and e.get("key") == key]
    if not entries:
        return (f"No recorded change to the {key!r} setting, so nothing was "
                f"restored. Only changes Chronoa itself made and recorded can "
                f"be undone this way.")
    try:
        steps = int(arguments.get("steps") or 1)
    except (TypeError, ValueError):
        return f"Steps must be a whole number, not {arguments.get('steps')!r}."
    if steps < 1:
        return "Steps must be at least 1 - 1 already means the previous value."
    if steps > len(entries):
        return (f"Asked to step back {steps} change(s) but only "
                f"{len(entries)} {'is' if len(entries) == 1 else 'are'} recorded "
                f"for {key!r}. Nothing was restored.")
    chosen = entries[-steps]
    before = chosen.get("before")
    if not isinstance(before, str):
        return f"The recorded change for {key!r} is unreadable, so nothing was restored."
    config = ChronoaConfig()
    # record=False: a restore is not a new change, or the ring grows by one
    # entry for every entry it spends.
    config.set(key, before, record=False)
    if _consume(chosen):
        note = " That recorded state has now been used and will not be offered again."
    else:
        note = ""
    when = time.strftime("%Y-%m-%d %H:%M", time.localtime(chosen.get("at") or 0))
    return (f"Restored the {key!r} setting to {before!r}, the value recorded "
            f"at {when} ({steps} step(s) back).{note}")


def _run(arguments: dict) -> str:
    allowed, reason = _consent(ChronoaConfig())
    if not allowed:
        return f"Refusing to restore: {reason}"
    if arguments.get("list"):
        return _list(arguments)
    if (arguments.get("setting") or "").strip():
        return _restore_setting(arguments)

    try:
        target = files.resolve_in_home(arguments.get("path") or "")
    except files.PathProblem as exc:
        return str(exc)
    try:
        files.refuse_catalogue(target, "restore")
        files.refuse_sensitive(target, "restore")
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
    if isinstance(chosen.get("content_b64"), str):
        try:
            raw = base64.b64decode(chosen["content_b64"], validate=True)
        except ValueError:
            raw = None
    else:
        raw = content.encode("utf-8") if isinstance(content, str) else None
    if raw is None:
        return (
            f"The recorded pre-image for {target} is unreadable, so nothing "
            f"was restored."
        )
    if current == raw:
        return (
            f"{target} already holds that content, so nothing was written."
        )

    # The entry is dropped only after the write succeeds, so a failure leaves
    # the ring as it was and the restore can be tried again.
    try:
        target.write_bytes(raw)
    except OSError as exc:
        return files.describe(exc, target, "restore")
    if _consume(chosen):
        note = " That recorded state has now been used and will not be offered again."
    else:
        note = ""

    if target.read_bytes() != raw:
        return f"Reported restoring {target} but its contents do not match."
    when = time.strftime("%Y-%m-%d %H:%M", time.localtime(chosen.get("at") or 0))
    return (
        f"Restored {target} to the content recorded at {when} "
        f"({steps} step(s) back, {files.human_size(len(raw))}).{note}"
    )


def _raw(entry: dict) -> "bytes | None":
    """The bytes a ring entry recorded, or None when it cannot be read back."""
    if isinstance(entry.get("content_b64"), str):
        try:
            return base64.b64decode(entry["content_b64"], validate=True)
        except ValueError:
            return None
    content = entry.get("content")
    return content.encode("utf-8") if isinstance(content, str) else None


def restore_content(path: "Path | str", raw: bytes) -> str:
    """Write `raw` back to `path`, recording what it replaces first.

    The one write path for every "put it back" control in the window (a turn's
    Revert, a Diff row's Revert): the current content becomes a fresh
    pre-image, so the restore is itself on the ring and can be undone.
    Returns "" on success, else the reason.
    """
    target = Path(path)
    try:
        current = target.read_bytes() if target.exists() else b""
        if current == raw:
            return "already as it was"
        record_preimage(target, current)
        target.write_bytes(raw)
    except OSError as exc:
        return exc.strerror or str(exc)
    return ""


def revert_turn(turn: str) -> "tuple[list[str], list[str]]":
    """Put every file one turn changed back as it was **before that turn**.

    Returns `(restored, skipped)` - paths, and "path: why" for each that was
    not. Per file it is the turn's *earliest* pre-image, so a turn that edited
    one file three times goes back past all three, not one step.

    **Itself undoable.** Each file's current content is recorded as a fresh
    pre-image (attributed to no turn) before it is overwritten, so `/undo` or
    the Undo button can take the revert back - a one-click revert that could
    not be reversed would be the destructive control this ring exists to
    prevent.

    Called by the window's "Revert this turn" button, in the app process: the
    person pressed it, about changes Chronoa made, so it is not a model's
    request and needs no consent prompt of its own.
    """
    want = " ".join(str(turn or "").split())[:MAX_TURN_CHARS]
    if not want:
        return [], []
    earliest: "dict[str, dict]" = {}
    for entry in _load():
        if not isinstance(entry, dict) or str(entry.get("turn") or "") != want:
            continue
        path = str(entry.get("path") or "")
        if path and path not in earliest:
            earliest[path] = entry
    restored, skipped = [], []
    for path, entry in earliest.items():
        target = Path(path)
        raw = _raw(entry)
        if raw is None:
            skipped.append(f"{path}: its recorded content is unreadable")
            continue
        try:
            current = target.read_bytes() if target.exists() else b""
            if current == raw:
                skipped.append(f"{path}: already as it was")
                continue
            record_preimage(target, current)
            target.write_bytes(raw)
        except OSError as exc:
            skipped.append(f"{path}: {exc.strerror or exc}")
            continue
        restored.append(path)
    return restored, skipped


SKILLS = [Skill(name="undo_last_change", schema=SCHEMA, run=_run)]
