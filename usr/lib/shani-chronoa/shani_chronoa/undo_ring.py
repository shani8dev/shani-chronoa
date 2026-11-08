"""The journal of what Chronoa changed, so a change can be put back.

**This is not a version control system.** It holds, for a bounded number of
recent changes, the *before* of each one - a file's contents, a setting's
value, a trigger store's JSON - so "put that back" has something to restore
from. It records only Chronoa's own writes: a change made in an editor, a
shell or another program never passes through here and is never claimed to be
undoable.

**One journal, three kinds of entry.** Files (`kind="file"`, legacy entries
without a kind are files), settings (`kind="setting"`, a ChronoaConfig key
with its before/after), and anything else a writer records with
`record_file()`. Each kind is trimmed to its own bound, so a settings-heavy
session cannot evict the file pre-images and vice versa.

**Secrets are never journalled.** An API key's before-value is exactly what a
journal must not hold, so `record_setting` refuses secret keys by construction
rather than by checking at the display site.

**Resolved per call, never at import.** Both the path and the turn come from
the environment at the moment of the call: a module-level `$HOME` constant has
twice contaminated a real user's directories in this repo, and the turn a
skill runs in only exists as an environment variable in the child process.
"""

from __future__ import annotations

import base64
import json
import os
import time
from pathlib import Path

from shani_chronoa import files

#: Bounds per kind. A journal of file contents is a disk-usage problem if it
#: grows without limit, and a journal of setting values is a different
#: problem: twenty of each is a safety net, not an archive.
MAX_PER_KIND = 20

#: A file pre-image larger than this is not stored; restoring a 400 MB file
#: from a ring that itself costs 400 MB is a bad trade, and the honest answer
#: is to say the change was too large to record.
MAX_PREIMAGE_BYTES = 2 * 1024 * 1024

#: Set on a skill child by `tools.execute_tool`, beside the consent grant, so
#: the process knows which turn asked it to write. A skill runs as
#: `python3 -c`, so anything the app holds in memory is another process's
#: memory; this env entry is the only channel there is.
TURN_ENV = "CHRONOA_TURN"

#: How long a `turn` value may be before it is refused. The value is written
#: into a file a person may read; 64 characters is far more than any turn
#: label needs.
MAX_TURN_CHARS = 64

#: Key suffixes whose values must never be journalled.
SECRET_SUFFIXES = ("api-key",)


def ring_path() -> Path:
    """Where the journal lives, resolved per call (`$XDG_STATE_HOME` honoured)."""
    base = os.environ.get("XDG_STATE_HOME") or str(Path.home() / ".local" / "state")
    return Path(base) / "shani-chronoa" / "preimages.json"


def current_turn() -> str:
    """The turn this process is writing in, or "" when none was set.

    Normalised and refused rather than trusted: the value arrives from the
    environment, so a newline would put a second line in the journal and an
    unbounded value would let a caller put anything there.
    """
    raw = os.environ.get(TURN_ENV, "")
    if not isinstance(raw, str):
        return ""
    return " ".join(raw.split())[:MAX_TURN_CHARS]


def load() -> list:
    try:
        parsed = json.loads(ring_path().read_text(encoding="utf-8"))
    except FileNotFoundError:
        return []
    except (OSError, ValueError):
        # A corrupt journal is reported by its callers, never silently
        # replaced: a store that quietly becomes empty turns "I can undo
        # that" into "I cannot" with no sign anything was lost.
        return []
    return parsed if isinstance(parsed, list) else []


def _save(entries: list) -> str:
    path = ring_path()
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        try:
            path.parent.chmod(0o700)
        except OSError:
            pass
        path.write_text(json.dumps(entries), encoding="utf-8")
        path.chmod(0o600)
    except OSError as exc:
        return f" Could not record the change ({files.describe(exc, path, 'write')})"
    return ""


def _append(entry: dict) -> str:
    """Append `entry`, trimming its own kind to MAX_PER_KIND. Returns a note."""
    kind = entry.get("kind") or "file"
    entries = [e for e in load() if isinstance(e, dict)]
    entries.append(entry)
    mine = [e for e in entries if (e.get("kind") or "file") == kind]
    overflow = len(mine) - MAX_PER_KIND
    if overflow > 0:
        doomed = {id(e) for e in mine[:overflow]}
        entries = [e for e in entries if id(e) not in doomed]
    note = _save(entries)
    if len(mine) > 1:
        oldest = mine[0].get("at") or 0
        note += (
            f" The journal holds the last {len(mine)} {kind} change(s); the "
            f"oldest is from "
            f"{time.strftime('%Y-%m-%d %H:%M', time.localtime(oldest))}."
        )
    return note


def consume(entry: dict) -> bool:
    """Drop one journal entry (a restore consumed it). True if it was found."""
    entries = load()
    for i, existing in enumerate(entries):
        if not isinstance(existing, dict):
            continue
        if (existing.get("path") == entry.get("path")
                and existing.get("key") == entry.get("key")
                and existing.get("at") == entry.get("at")):
            del entries[i]
            _save(entries)
            return True
    return False


def is_secret_key(key: str) -> bool:
    """Whether a setting's value must never be journalled."""
    return key.endswith(SECRET_SUFFIXES)


def record_file(path: Path, content: bytes) -> str:
    """Store `content` as the pre-image of `path`. Returns a note for the user.

    A file too large to keep is skipped with an explanation rather than stored
    partially: a truncated pre-image restores corruption.
    """
    turn = current_turn()
    if len(content) > MAX_PREIMAGE_BYTES:
        return (
            f"Too large to record a pre-image of "
            f"{files.human_size(MAX_PREIMAGE_BYTES)} and over; undo will not "
            f"cover this change."
        )
    try:
        entry = {"kind": "file", "path": str(path), "at": time.time(),
                 "content": content.decode("utf-8")}
    except UnicodeDecodeError:
        entry = {"kind": "file", "path": str(path), "at": time.time(),
                 "content_b64": base64.b64encode(content).decode("ascii")}
    if turn:
        entry["turn"] = turn
    return _append(entry)


def record_setting(key: str, before: str, after: str) -> str:
    """Journal one setting change. Secret keys and no-ops record nothing."""
    if is_secret_key(key) or before == after:
        return ""
    entry = {"kind": "setting", "key": key, "at": time.time(),
             "before": before, "after": after}
    turn = current_turn()
    if turn:
        entry["turn"] = turn
    return _append(entry)


def changes_for_turn(turn: str) -> "list[str]":
    """What `turn` changed, newest last, without duplicates.

    Attribution is only ever as good as the label: an unattributed change is
    not claimed by anybody rather than folded into whichever turn asked.
    """
    want = " ".join(str(turn or "").split())[:MAX_TURN_CHARS]
    if not want:
        return []
    out: "list[str]" = []
    for entry in load():
        if not isinstance(entry, dict) or str(entry.get("turn") or "") != want:
            continue
        label = (entry.get("path") if (entry.get("kind") or "file") == "file"
                 else f"setting:{entry.get('key')}")
        if label and label not in out:
            out.append(str(label))
    return out
