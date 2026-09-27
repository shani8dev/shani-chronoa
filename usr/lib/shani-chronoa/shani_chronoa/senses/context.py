"""Assembles live percepts into the LLM's context for a single turn.

The central rule of this module: **percepts never enter
`Assistant._history`.** `ContextBuilder` returns a *new* message list for one
call; `_history` is untouched. That is deliberate, and it avoids three
concrete failures that the obvious alternatives walk into:

- `_trim_history()` groups messages into turns and preserves only
  `_history[0]`. A percept injected as `_history[1]` would be silently
  dropped by the trimmer, and special-casing the slice to `[0:2]` couples
  perception to conversation bookkeeping - a later change to either breaks
  the other invisibly.
- `MAX_HISTORY_MESSAGES` is 40, and `assistant.py` documents that Ollama
  *drops* rather than errors on context overflow. A single screen OCR dump
  can consume a large share of that budget, so folding percepts into
  `_history` risks a silent, hard-to-diagnose truncation.
- Percepts have their own lifetimes. A 30-second-old screen percept is stale;
  the trimmer has no notion of staleness and would keep it as long as the
  turn it arrived in survived.

Budgeting is by character count rather than tokens because no tokenizer is
available in-process and an approximate bound is the right tool for
preventing runaway context, not an exact one. When the budget is tight,
percepts are dropped most-private-first: `SENSITIVITY_PRIVATE` percepts are
the ones least appropriate to send anywhere, least valuable as context, and
the ones a user would most want retained locally - so they are the correct
thing to shed first.
"""

import logging
from typing import Optional, Sequence

from shani_chronoa.senses import (
    SENSITIVITY_PRIVATE,
    SENSITIVITY_PUBLIC,
    Percept,
)

logger = logging.getLogger(__name__)

# Per-turn character budget for the percept context block. Sized to leave
# the conversation itself the majority of a typical `num_ctx` while still
# allowing a meaningful amount of recent perception.
DEFAULT_BUDGET_CHARS = 4000

# Ranked by *inclusion* order, not drop order: lowest rank is considered
# first, so budget exhaustion and the max_percepts cap both shed the
# highest rank. Ranking "private" high looks wrong and is not - reversing it
# silently retains the percepts meant to be dropped.
_SENSITIVITY_RANK = {
    SENSITIVITY_PUBLIC: 0,
    "personal": 1,
    SENSITIVITY_PRIVATE: 2,
}

_HEADER = "What you currently perceive (local sensors, may be stale):"


class ContextBuilder:
    """Turns a set of live percepts into one transient system message."""

    def __init__(
        self,
        budget_chars: int = DEFAULT_BUDGET_CHARS,
        max_percepts: int = 24,
        include_sensitivity_labels: bool = True,
    ) -> None:
        self.budget_chars = budget_chars
        self.max_percepts = max_percepts
        self.include_sensitivity_labels = include_sensitivity_labels

    def _rank(self, percept: Percept) -> "tuple[int, float]":
        """Order percepts for inclusion: least private first, then freshest.

        Returned as a sort key ascending, so private percepts are considered
        first and therefore dropped first when the budget is exceeded.
        """
        rank = _SENSITIVITY_RANK.get(percept.sensitivity, 2)
        return (rank, -percept.created_at)

    def render(self, percepts: Sequence[Percept]) -> str:
        """Render percepts to the text block injected into context.

        `budget_chars` bounds the *content lines only*; the fixed header is
        not counted against it. Counting the header instead meant a budget
        near its length rendered an empty block, silently dropping all
        perception rather than degrading to fewer percepts.

        Returns "" only when there is genuinely nothing to inject, so the
        caller can skip the message instead of sending an empty system turn.
        """
        if not percepts:
            return ""

        ordered = sorted(percepts, key=self._rank)[: self.max_percepts]

        lines: list[str] = []
        used = 0
        for percept in ordered:
            if self.include_sensitivity_labels:
                line = f"- [{percept.sense}/{percept.kind}] {percept.content}"
            else:
                line = f"- {percept.content}"
            # Separator newline costs nothing after the last line; charging
            # it anyway rejected a line that fit the budget exactly.
            cost = len(line) + (1 if lines else 0)
            if used + cost > self.budget_chars:
                logger.debug(
                    "Percept context budget reached; dropping %s/%s",
                    percept.sense,
                    percept.kind,
                )
                break
            lines.append(line)
            used += cost

        if not lines:
            return ""
        return _HEADER + "\n" + "\n".join(lines)

    def build_messages(
        self,
        history: Sequence[dict],
        percepts: Sequence[Percept] = (),
    ) -> list[dict]:
        """Return the message list for one turn, leaving `history` untouched.

        The percept block is inserted immediately after the system prompt so
        it frames the conversation rather than reading as something the user
        said. The caller's `_history` is never mutated and never receives
        the block, which is what makes the block's contents correctly
        expire with their TTL instead of persisting for the session.
        """
        messages = list(history)
        block = self.render(percepts)
        if not block or not messages:
            return messages
        return [messages[0], {"role": "system", "content": block}] + messages[1:]

    def render_for_log(self, percepts: Sequence[Percept]) -> str:
        """A short human-readable summary of what is being perceived.

        Used by the headless CLI and by debug logging. Deliberately shows
        the age of every percept, because a stale percept presented as
        current is the failure mode most likely to mislead.
        """
        if not percepts:
            return "(no live percepts)"
        parts = [
            f"{p.sense}/{p.kind} age={p.age_seconds():.0f}s ttl={p.ttl_seconds}"
            for p in sorted(percepts, key=self._rank)
        ]
        return "\n".join(parts)


def sanitize_percepts(
    percepts: Sequence[Percept], sanitizer: Optional[object] = None
) -> list[Percept]:
    """Redact registered secrets from percept content before it is persisted.

    `sanitizer` is `secrets_manager.sanitize_text_for_llm`. It is passed in
    rather than imported so this module stays importable without the secrets
    machinery, and so tests can substitute a recorder.

    This matters more for durable percepts than for anything else in the
    codebase: memory content is written to disk and re-read on later turns,
    so a secret that was never redacted on the way in would be re-emitted to
    a provider on the way out. The existing redaction only runs on the path
    *to* an LLM, which persistence sits upstream of.
    """
    if sanitizer is None or not callable(sanitizer):
        return list(percepts)

    redacted: list[Percept] = []
    for percept in percepts:
        try:
            content = sanitizer(percept.content)
        except Exception as e:  # noqa: BLE001 - redaction must never be fatal
            logger.error("Failed to sanitize percept %s/%s: %s", percept.sense, percept.kind, e)
            redacted.append(percept)
            continue
        if content == percept.content:
            redacted.append(percept)
            continue
        redacted.append(
            Percept(
                sense=percept.sense,
                kind=percept.kind,
                content=content,
                created_at=percept.created_at,
                ttl_seconds=percept.ttl_seconds,
                source=percept.source,
                sensitivity=percept.sensitivity,
                metadata=percept.metadata,
            )
        )
    return redacted
