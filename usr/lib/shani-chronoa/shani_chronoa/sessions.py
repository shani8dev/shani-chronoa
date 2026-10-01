"""The conversation transcript, on disk, so a restart is not a total loss.

Until this existed the assistant's history was a list in one process. Closing
the window, or a crash, or a reboot discarded the whole conversation with no
record - which for something people talk to several times a day is the
difference between a tool and a toy.

Append-only JSONL, one message per line, written after every turn rather than
at the end. The end is never reached in practice: the interesting failure is the
crash, and a transcript saved on clean exit is empty precisely when it was most
worth having. mini-swe-agent puts its `save()` in a `finally` for the same
reason.

Deliberately **not** a privacy-mode key. `privacy-mode` is documented as "all
processing happens locally, no data is sent to external servers" - it governs
egress. Writing a local file is not egress, and tying the two would mean a
setting labelled "local-only" silently also deleted transcripts, which is a
different promise than the one the user agreed to.

What this does do about the content:

- Mode 0600, in the app's own data directory, alongside the percepts and logs
  that are already there. Nothing new leaves the machine.
- **Percepts are never written.** They are deliberately not part of
  `_history` - they are re-read from the store on every request so one that
  expires mid-turn stops appearing - so a snapshot of the conversation cannot
  capture a machine reading the user has since revoked consent for.
- A corrupt or half-written line is skipped rather than failing the load. A
  crash mid-append is the expected case, not an exceptional one.
"""

from __future__ import annotations

import json
import logging
import os
from pathlib import Path
from typing import Iterator, List, Optional

from shani_chronoa import files

logger = logging.getLogger(__name__)

SESSION_DIR = files.data_home() / "shani-chronoa" / "sessions"
#: One file, holding the most recent conversation. A single assistant on one
#: machine is one conversation; a per-id scheme would be scaffolding for a
#: multi-session feature that does not exist yet.
#:
#: Named here, but **never used as a default**. Every function in this module
#: takes the path explicitly, because a default would mean that any
#: `Assistant()` constructed without one - a test, a script, an embedder -
#: silently reads and writes the real user's conversation file. That was the
#: first version, and it is exactly the kind of default nobody reviews.
TRANSCRIPT = SESSION_DIR / "current.jsonl"

#: How much history is worth reading back. The in-memory cap is 40 messages;
#: this is deliberately larger, because the transcript is a record rather than a
#: working set - a month-old turn is not going into a prompt, but deleting it
#: would mean the user cannot see what they were told.
MAX_LOADED = 500

#: Roles worth keeping. Anything else is a shape this module does not
#: understand, and writing it back out would just reproduce the surprise.
_KNOWN_ROLES = frozenset({"system", "user", "assistant", "tool"})


def _read_lines(path: Path) -> Iterator[str]:
    try:
        with path.open("r", encoding="utf-8") as handle:
            for line in handle:
                yield line
    except OSError as exc:
        logger.debug("No readable transcript at %s: %s", path, exc)
        return


def load(path: Path) -> List[dict]:
    """The saved messages, oldest first, skipping anything unreadable.

    A truncated final line is the signature of a crash during an append, and it
    is expected rather than exceptional - so it is dropped, not raised. The
    alternative is refusing to start because the last write was interrupted.
    """
    target = Path(path)
    messages: List[dict] = []
    for line in _read_lines(target):
        line = line.strip()
        if not line:
            continue
        try:
            record = json.loads(line)
        except ValueError:
            # A partial write, or a line something else put there. Either way
            # the rest of the conversation is still good.
            logger.debug("Skipping an unreadable transcript line")
            continue
        if not isinstance(record, dict) or record.get("role") not in _KNOWN_ROLES:
            continue
        messages.append(record)
    if len(messages) > MAX_LOADED:
        messages = messages[-MAX_LOADED:]
    return messages


def append(message: dict, path: Path) -> bool:
    """Append one message. Returns whether it was written.

    Never raises: a transcript that cannot be written is a lost convenience, and
    losing it must not take the conversation with it.
    """
    if not isinstance(message, dict) or message.get("role") not in _KNOWN_ROLES:
        return False
    target = Path(path)
    try:
        target.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        line = json.dumps(message, ensure_ascii=False)
        # Opened per append rather than held open, so the mode is applied on
        # every write and a file created by an older build still gets tightened.
        fd = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
        try:
            os.write(fd, (line + "\n").encode("utf-8"))
        finally:
            os.close(fd)
        return True
    except (OSError, TypeError, ValueError) as exc:
        logger.debug("Could not append to the transcript at %s: %s", target, exc)
        return False


def clear(path: Path) -> bool:
    """Delete the transcript. Used by `Assistant.reset()` and by the user."""
    target = Path(path)
    try:
        target.unlink()
        return True
    except FileNotFoundError:
        return True
    except OSError as exc:
        logger.debug("Could not clear the transcript at %s: %s", target, exc)
        return False
