"""The text you have typed and not yet sent.

Chronoa had no draft persistence. You compose a long question, the daemon
restarts or the window is closed, and it is gone - the most annoying missing
thing in something meant to be open all day, and the cheapest to fix.

**One file, replaced whole, `0600`, written atomically.** It holds whatever you
were typing, so it is as private as the conversation it belongs to: same
directory as the sessions, same mode as a transcript. Written
temp-then-`os.replace` so a crash mid-write leaves the *previous* draft rather
than a half-written one - the asymmetry `conversation_store.append()`
deliberately does not need, because an append cannot lose the whole file but a
replace can.

**Never raises and never blocks the composer.** A draft that cannot be saved is
a lost convenience; losing it must not take the keystroke with it.

**One file per conversation, because a draft belongs to a conversation.** This
was a single global file, and the composer is a single widget, so a half-typed
question followed the person from one conversation into another: switch to an
existing conversation and the box still held what you were writing to the
previous one, and Enter sent it into the new one. `Ctrl+N` had already been
fixed for exactly this misfire - `conversation._reset_conversation` clears the
composer, with a comment describing it - so the hole was only in *switching*,
which nobody had looked at, and the fix above hid the shape of it.

The files go in the same record as the text, for the same reason the module
already keeps one file rather than two: one atomic replace means the sentence
and its attachments can never be restored out of step with each other. Half a
restore is worse than none - the person sees their question in the box and
reasonably concludes the files came back too.
"""

from __future__ import annotations

import json
import logging
import os
import tempfile
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

#: Longer than this and it is not a draft, it is a document someone should have
#: saved. The cap keeps a runaway paste from turning every keystroke into a
#: multi-megabyte write.
MAX_DRAFT_CHARS = 100_000


def draft_path(key: Optional[str] = None) -> Path:
    """Where the draft for `key` lives.

    Next to the sessions, resolved now and never captured at import - the same
    rule `conversation_store.session_dir()` states, and for the same reason: a
    test that moves `XDG_DATA_HOME` must not write into the previous home's
    draft.

    **No key is the old single global file**, unchanged, so a draft saved before
    this existed is still found and no existing behaviour moves. A key gets its
    own file next to it.

    **The key is reduced to something safe for a filename.** It arrives from a
    session reference, and a reference carrying a slash would otherwise write
    outside the session directory - the one place this module's `0600` and
    `0o700` guarantees are load-bearing. Anything outside `[A-Za-z0-9._-]`
    becomes `_`, which cannot collide with a different conversation because two
    different keys cannot reduce to the same string unless they differed only in
    characters this rejects, and those are not session references.
    """
    from shani_chronoa import conversation_store

    directory = conversation_store.session_dir()
    if not key:
        return directory / "composer.draft"
    safe = "".join(c if (c.isalnum() or c in "._-") else "_" for c in str(key))
    return directory / f"composer-{safe}.draft"


def save(text: str, path: Optional[Path] = None,
         files: Optional[list] = None) -> bool:
    """Record `text` and its `files` as the current draft. Returns success.

    Empty text clears it, **files included** - a draft with no sentence but three
    attachments is not a draft, and leaving the files behind would put chips in
    the composer of a conversation with nothing typed in it.
    """
    target = Path(path) if path is not None else draft_path()
    text = text or ""
    if len(text) > MAX_DRAFT_CHARS:
        text = text[:MAX_DRAFT_CHARS]
    names = [str(f) for f in (files or [])] if text else []
    try:
        target.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        fd, temp_name = tempfile.mkstemp(dir=str(target.parent),
                                         prefix=".composer-", suffix=".draft")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                json.dump({"text": text, "files": names}, handle,
                          ensure_ascii=False)
                handle.flush()
                os.fsync(handle.fileno())
            os.chmod(temp_name, 0o600)
            os.replace(temp_name, target)     # atomic: readers see old or new
        except BaseException:
            Path(temp_name).unlink(missing_ok=True)
            raise
        return True
    except (OSError, TypeError, ValueError) as exc:
        logger.debug("Could not save the composer draft to %s: %s", target, exc)
        return False


def load(path: Optional[Path] = None) -> str:
    """The saved draft, or "" when there is none or it cannot be read.

    **Tolerant of a corrupt file on purpose.** A draft is not worth failing
    startup over; an unreadable one is a lost draft and nothing more, so this
    returns empty and says why in the log rather than raising into a window
    constructor.
    """
    target = Path(path) if path is not None else draft_path()
    try:
        raw = target.read_text(encoding="utf-8")
    except FileNotFoundError:
        return ""
    except OSError as exc:
        logger.debug("Could not read the composer draft at %s: %s", target, exc)
        return ""
    if not raw.strip():
        return ""
    try:
        payload = json.loads(raw)
    except ValueError as exc:
        logger.debug("Composer draft at %s is not JSON: %s", target, exc)
        return ""
    if not isinstance(payload, dict):
        return ""
    text = payload.get("text")
    return text if isinstance(text, str) else ""


def clear(path: Optional[Path] = None) -> bool:
    """Forget the draft."""
    target = Path(path) if path is not None else draft_path()
    try:
        target.unlink(missing_ok=True)
        return True
    except OSError as exc:
        logger.debug("Could not clear the composer draft at %s: %s", target, exc)
        return False


def has_draft(path: Optional[Path] = None) -> bool:
    """Whether there is a draft worth restoring, without reading it twice."""
    return bool(load(path))


def load_entry(path: Optional[Path] = None) -> tuple:
    """`(text, files)` for one draft - both halves, or neither.

    **Separate from `load` because the two halves must arrive together.** A
    caller restoring a conversation's composer needs the attachments that went
    with that sentence, and reading the text with `load` and then the files by
    some second route is how they end up out of step - the partial restore this
    module's docstring calls worse than none. `load` stays text-only for the
    callers that only ever wanted the sentence.

    A file recorded by an older build has no `files` key at all, and that is not
    an error: it restores its text and no attachments, which is exactly what it
    held.
    """
    target = Path(path) if path is not None else draft_path()
    try:
        raw = target.read_text(encoding="utf-8")
    except (FileNotFoundError, OSError) as exc:
        if not isinstance(exc, FileNotFoundError):
            logger.debug("Could not read the draft at %s: %s", target, exc)
        return "", []
    if not raw.strip():
        return "", []
    try:
        payload = json.loads(raw)
    except ValueError as exc:
        logger.debug("Composer draft at %s is not JSON: %s", target, exc)
        return "", []
    if not isinstance(payload, dict):
        return "", []
    text = payload.get("text")
    files = payload.get("files")
    return (text if isinstance(text, str) else "",
            [f for f in files if isinstance(f, str)] if isinstance(files, list)
            else [])