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


def draft_path() -> Path:
    """Where the draft lives.

    Next to the sessions, resolved now and never captured at import - the same
    rule `conversation_store.session_dir()` states, and for the same reason: a
    test that moves `XDG_DATA_HOME` must not write into the previous home's
    draft.
    """
    from shani_chronoa import conversation_store

    return conversation_store.session_dir() / "composer.draft"


def save(text: str, path: Optional[Path] = None) -> bool:
    """Record `text` as the current draft. Empty clears it. Returns success."""
    target = Path(path) if path is not None else draft_path()
    text = text or ""
    if len(text) > MAX_DRAFT_CHARS:
        text = text[:MAX_DRAFT_CHARS]
    try:
        target.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        fd, temp_name = tempfile.mkstemp(dir=str(target.parent),
                                         prefix=".composer-", suffix=".draft")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                json.dump({"text": text}, handle, ensure_ascii=False)
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