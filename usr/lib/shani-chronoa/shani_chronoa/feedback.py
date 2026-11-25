"""A person's judgement on an answer, kept as a record rather than an opinion.

"Wrong" and "the right answer is X" are two things a person can say after a
reply, and both are signals the rest of this project has no way to see: the call
log records what the assistant *did*, the transcript records what it *said*, and
neither records that a person rejected either one. This module is that missing
half, and it deliberately does only two things:

- persist the judgement beside the conversations and logs, under the same
  private per-user data directory everything else uses; and
- write it back onto the most recent tool-call record, so an audit trail read
  later can tell "Chronoa did this and it was accepted" from "Chronoa did this
  and the person said no".

It never changes what the model is told and never grants or refuses a tool.
A correction reaches the model only as the next turn the person sends; this is
a *record*, in the sense ``ARCHITECTURE-TARGET.md`` uses for the feedback store.
Consent, approvals and the fixed whitelist stay where they were.
"""

from __future__ import annotations

import json
import logging
import os
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from shani_chronoa import files

logger = logging.getLogger(__name__)

#: The two judgements a person can make. Kept as a closed vocabulary: a free
#: form "verdict" would let a caller write "maybe" and have the learning layer
#: guess what it meant. A free-form *correction* is fine, because it is data
#: attached to one of these two, not a claim about the verdict.
WRONG = "wrong"
CORRECTION = "correction"
VERDICTS = (WRONG, CORRECTION)


def feedback_path() -> Path:
    """Where feedback lives, resolved on every call like the logs themselves.

    A module-level constant would be built before a test redirected
    ``XDG_DATA_HOME`` and write into the real user's home - the exact failure
    `tool_tracking` fixed for its log. Asking where the store is does not make
    it; only `record_answer` writes.
    """
    return files.data_home() / "shani-chronoa" / "feedback.jsonl"


@dataclass
class Feedback:
    """One judgement, with enough provenance to say which answer it was about."""

    verdict: str
    correction: str
    session_id: str = ""
    message_index: int = -1
    created: float = 0.0

    def to_dict(self) -> dict:
        return {
            "verdict": self.verdict,
            "correction": self.correction,
            "session_id": self.session_id,
            "message_index": self.message_index,
            "created": self.created,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "Feedback":
        return cls(
            verdict=str(data.get("verdict", "")),
            correction=str(data.get("correction", "")),
            session_id=str(data.get("session_id", "")),
            message_index=int(data.get("message_index", -1) or -1),
            created=float(data.get("created", 0.0) or 0.0),
        )


def _valid(verdict: str) -> bool:
    return isinstance(verdict, str) and verdict.strip().lower() in VERDICTS


def record_answer(
    verdict: str,
    correction: str = "",
    session_id: str = "",
    message_index: int = -1,
    tracker: Optional[object] = None,
    path: Optional[Path] = None,
) -> Feedback:
    """Persist one judgement and write it back to the call record.

    `verdict` is `wrong` or `correction`; `correction` is the person's own words
    and may be empty for a bare "that was wrong". Returns the record written.
    An invalid verdict is refused with `ValueError` rather than normalised: a
    caller that spells "maybe" has not made a judgement, and recording one on
    its behalf would invent a signal.

    The call-log write-back is best effort. Losing the annotation must not lose
    the judgement - the feedback file is the durable record - so a log that is
    absent or unwritable is logged and the function still returns the record.
    """
    value = verdict.strip().lower() if isinstance(verdict, str) else ""
    if not _valid(value):
        raise ValueError(f"feedback must be 'wrong' or 'correction', not {verdict!r}")
    words = correction.strip() if isinstance(correction, str) else ""
    if value == CORRECTION and not words:
        # A "correction" with no corrected answer is indistinguishable from
        # "wrong", and two names for one fact is a second answer to one question.
        value = WRONG
    feedback = Feedback(
        verdict=value,
        correction=words,
        session_id=str(session_id or ""),
        message_index=int(message_index),
        created=time.time(),
    )
    target = Path(path) if path is not None else feedback_path()
    target.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    line = json.dumps(feedback.to_dict(), ensure_ascii=False) + "\n"
    fd = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
    with os.fdopen(fd, "a") as handle:
        handle.write(line)
    files.restrict_file(target)
    try:
        from shani_chronoa import tool_tracking

        tool_tracking.annotate_last_feedback(
            words or value,
            value,
            tracker=tracker,
            log_dir=None if tracker is None else tracker.log_dir,
        )
    except Exception:  # noqa: BLE001 - the feedback file is the durable record
        logger.warning(
            "feedback was saved but the call log could not be annotated", exc_info=True
        )
    return feedback


def load(limit: int = 100, path: Optional[Path] = None) -> list[Feedback]:
    """Recent judgements, oldest first. A missing or broken file is no feedback."""
    target = Path(path) if path is not None else feedback_path()
    out: list[Feedback] = []
    try:
        lines = target.read_text(encoding="utf-8").splitlines()
    except OSError:
        return out
    for line in lines[-max(1, int(limit)) :]:
        try:
            data = json.loads(line)
        except ValueError:
            continue
        if isinstance(data, dict):
            out.append(Feedback.from_dict(data))
    return out
