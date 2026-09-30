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
import time
from typing import Callable, Optional, Sequence

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
        consent: Optional[Callable[[str], bool]] = None,
    ) -> None:
        self.budget_chars = budget_chars
        self.max_percepts = max_percepts
        self.include_sensitivity_labels = include_sensitivity_labels
        self.consent = consent

    def _permitted(self, percepts: Sequence[Percept]) -> list:
        """Drop anything whose sense is no longer allowed to be *sent*.

        Consent was only ever checked when a percept was recorded, so a fact
        captured while `memory-sense-enabled` was true kept being injected into
        every prompt after the user set it to false - verified by running it,
        not by reading it. Perception and disclosure are different acts, and
        only the first one was gated.

        This withholds rather than deletes: the percept stays in the local
        store and reappears if consent is granted again, so withdrawing a
        permission costs the user nothing they did not ask to lose.

        `consent` is `None` for callers with no configuration to consult, which
        keeps this a pure renderer for the CLI and for tests. Every path that
        actually sends to a model must supply one - `app.py` does, and a wiring
        test asserts it, because a forgotten argument here would silently
        restore the old behaviour and nothing else would notice.
        """
        if self.consent is None:
            return list(percepts)
        allowed, withheld = [], 0
        for percept in percepts:
            if self.consent(percept.sense):
                allowed.append(percept)
            else:
                withheld += 1
        if withheld:
            logger.debug(
                "Withheld %d percept(s) whose sense is no longer permitted",
                withheld)
        return allowed

    def _rank(self, percept: Percept) -> "tuple[int, float, float]":
        """Order percepts for inclusion: least private, then most confident, then freshest.

        Returned as a sort key ascending, so private percepts are considered
        first and therefore dropped first when the budget is exceeded.

        `confidence` sits between sensitivity and freshness rather than last
        because it is the only one of the three that says anything about the
        percept's *content*: a fact the user stated verbatim should not lose a
        tie to one a regular expression guessed. It is a tie-break and nothing
        more, so it cannot promote a less relevant percept over a more relevant
        one - and because `Percept.confidence` defaults to a single uniform
        midpoint, every percept that predates the field keeps exactly the
        ordering it had, which is what stops this from silently re-budgeting
        every existing turn.
        """
        rank = _SENSITIVITY_RANK.get(percept.sensitivity, 2)
        return (rank, -percept.confidence, -percept.created_at)

    def render(self, percepts: Sequence[Percept]) -> str:
        """Render percepts to the text block injected into context.

        `budget_chars` bounds the *content lines only*; the fixed header is
        not counted against it. Counting the header instead meant a budget
        near its length rendered an empty block, silently dropping all
        perception rather than degrading to fewer percepts.

        Expired percepts are dropped here as well as in `store.active()`,
        because a stale fact quoted into a prompt is the one failure this
        module cannot have: the model reads it as current, has no way to know
        its window closed, and states it back. The redundancy is deliberate -
        `render()` is handed a list by callers other than the store's own
        `active()`, and a stale percept is a privacy-adjacent correctness
        problem rather than a rendering preference.

        Returns "" only when there is genuinely nothing to inject, so the
        caller can skip the message instead of sending an empty system turn.
        """
        if not percepts:
            return ""

        current = time.time()
        fresh = [p for p in percepts if not p.is_expired(current)]
        if len(fresh) != len(percepts):
            logger.debug(
                "Dropped %d percept(s) past their ttl or valid_until before rendering",
                len(percepts) - len(fresh),
            )

        permitted = self._permitted(fresh)
        if not permitted:
            return ""

        ordered = sorted(permitted, key=self._rank)[: self.max_percepts]

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

        Expired percepts are listed and marked here even though `render()`
        drops them, and that asymmetry is the point: the model must not be
        told a stale fact, but the *user* has to be able to see that one is
        sitting in the store, so that "the assistant no longer mentions my
        standup room" is answerable without reading the code. A fact that
        vanished and a fact that was never there have to look different.
        """
        if not percepts:
            return "(no live percepts)"
        now = time.time()
        parts = []
        for p in sorted(percepts, key=self._rank):
            life = f"ttl={p.ttl_seconds}"
            if p.valid_until is not None:
                life += f" valid_until={p.valid_until:.0f}"
            if p.is_expired(now):
                parts.append(f"{p.sense}/{p.kind} age={p.age_seconds():.0f}s {life} EXPIRED")
            else:
                parts.append(f"{p.sense}/{p.kind} age={p.age_seconds():.0f}s {life}")
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

    **The rebuild below lists every field explicitly, and a field added to
    `Percept` and forgotten here is dropped silently.** Not raised, not
    warned about - the dataclass happily applies its default, so a
    `valid_until` written to disk unredacted-forever, or a `confidence` reset
    to the midpoint, is indistinguishable from one that was never set. That is
    the reason
    `tests/test_percept_schema_extension.py::test_the_redaction_rebuild_preserves_every_field`
    exists and why it compares against `dataclasses.fields(Percept)` rather
    than against a list typed out here: a test that enumerated the fields
    would need the same edit and would be wrong in the same way.
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
                valid_until=percept.valid_until,
                confidence=percept.confidence,
                last_accessed_at=percept.last_accessed_at,
            )
        )
    return redacted
