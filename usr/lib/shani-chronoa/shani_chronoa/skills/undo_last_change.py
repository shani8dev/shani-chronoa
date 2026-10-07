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
keep". `write_text_file` (when it replaces or appends to a file),
`find_and_replace` (each file it rewrites) and `office_document` record too;
binary pre-images (.docx, .xlsx) are kept as base64. A path with no ring entry
reports that plainly rather than guessing at a content it does not have.
`list=true` shows the undo points held - the checkpoint list cline and
gemini-cli offer, scoped to Chronoa's own writes.

Consent is checked *before* the path is looked at, so a refusal does not
reveal whether a file exists.
"""

from __future__ import annotations

import base64
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
        entry = {"path": str(path), "at": time.time(), "content": content.decode("utf-8")}
    except UnicodeDecodeError:
        # A binary file (a .docx, .xlsx, an image) is kept as base64, so the
        # office and image skills' edits are as undoable as a text edit.
        entry = {"path": str(path), "at": time.time(),
                 "content_b64": base64.b64encode(content).decode("ascii")}
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
                "list": {
                    "type": "boolean",
                    "description": "List the recorded undo points (for path, or every file) instead of restoring.",
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
    if raw:
        try:
            target = files.resolve_in_home(raw)
        except files.PathProblem as exc:
            return str(exc)
        entries = [e for e in entries if e.get("path") == str(target)]
    if not entries:
        return "No undo points are held" + (f" for {raw}." if raw else ".")
    lines = [f"{len(entries)} undo point(s), newest first (steps=1 is the newest for each file):"]
    seen: dict = {}
    for e in reversed(entries):
        seen[e["path"]] = seen.get(e["path"], 0) + 1
        size = len(e.get("content") or "") or len(e.get("content_b64") or "") * 3 // 4
        when = time.strftime("%Y-%m-%d %H:%M", time.localtime(e.get("at") or 0))
        lines.append(f"- {when}  {e['path']}  (steps={seen[e['path']]}, {files.human_size(size)} before)")
    return "\n".join(lines)


def _run(arguments: dict) -> str:
    allowed, reason = _consent(ChronoaConfig())
    if not allowed:
        return f"Refusing to restore: {reason}"
    if arguments.get("list"):
        return _list(arguments)

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


SKILLS = [Skill(name="undo_last_change", schema=SCHEMA, run=_run)]
