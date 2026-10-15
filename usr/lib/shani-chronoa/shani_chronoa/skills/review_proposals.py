"""Skill: read and answer the facts the sleep pass noticed in a conversation.

`consolidation.sleep_now()` reads a finished conversation overnight and writes
a digest of `Candidate`s - sentences it noticed and **nobody has agreed to**.
That boundary is right: a background pass must not decide what the assistant
believes about somebody. What it lacked was a door, so the daemon wrote
digests to `sleep/` and every night's work proposed facts to nobody.

This is one of the two doors. The other is the Memory panel's "Proposed"
section, and **both call `senses.memory.decide_candidate`** - one function
behind both, because two implementations of "did the person agree to this?" is
exactly the pair that drifts, and this one writes to durable memory.

## Why one key and not two

`memory-sense-enabled` answers "may Chronoa remember anything?", and keeping a
proposal *is* remembering something. A separate switch for this tool would be a
second answer to one question, which is the shape `heard-sound` /
`sound-sense-enabled` records as needing two keys for two genuinely different
acts (ask-and-listen versus an automatic rule listening). Here there is one act.

So listing is behind the same gate as answering. The cost is one approval
dialog per session, and the repayment is that a person never has to work out
which of two switches governs whether a sentence from last night becomes a
permanent fact about them.

## What the answer has to carry

A skill is a subprocess; its return string is the only channel back. So the
outcome word goes into the sentence verbatim - `kept`, `kept-unrecorded`,
`declined`, `already-decided`, `refused` - because "I agreed to it" and "I
agreed to it and it is now in your memory" are different facts and a caller
that cannot tell them apart reports a memory that does not exist.
"""

from __future__ import annotations

from shani_chronoa.config import ChronoaConfig
from shani_chronoa.skills import Skill

SCHEMA = {
    "type": "function",
    "function": {
        "name": "review_proposals",
        "description": (
            "Facts Chronoa noticed in a finished conversation and has NOT "
            "saved, listed for the user to keep or discard. Nothing here is "
            "remembered until it is kept, and a discarded fact is never offered "
            "again. Use this when the user asks what Chronoa thinks it learned, "
            "or asks to remember or forget something it noticed last night."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "action": {
                    "type": "string",
                    "enum": ["list", "keep", "discard"],
                    "description": (
                        "list (default) shows what is waiting; keep saves a "
                        "proposal; discard drops it for good."
                    ),
                },
                "session_id": {
                    "type": "string",
                    "description": "Which conversation's proposals. From the list.",
                },
                "number": {
                    "type": "integer",
                    "description": (
                        "Which proposal, counting from 1 as the list shows it. "
                        "Required for keep and discard."
                    ),
                },
            },
        },
    },
}

#: `action` to the boolean `decide_candidate` takes. Spelled out rather than
#: derived with `action == "keep"`, because a derived one would read anything
#: that is not `keep` - including `discard`, and including an `action` a model
#: invented - as a decision to keep.
_ANSWER = {"keep": True, "discard": False}


def _consent(config: ChronoaConfig) -> tuple[bool, str]:
    """Return (allowed, reason). Fail-closed: no reason means no permission.

    **Consulted here and not only in `decide_candidate`, deliberately.** An
    entry-point gate is not a gate on the function: a caller who reaches the
    acceptor by another route gets no check from this module at all, and
    `test_no_declared_gate_is_advertised_without_a_check` exists because a
    skill that *advertises* a gate and never consults one is a refusal wearing
    a promise. The sense's own check stays as well - defence in depth, and the
    panel reaches the acceptor directly rather than through here.

    Listing is behind the same gate as answering, which is the one cost of
    riding one switch: a person is asked before they can *look* at what is
    waiting. The repayment is that they never have to work out which of two
    switches governs whether a sentence from last night becomes a permanent
    fact about them.

    **The signature is the house one, and it is load-bearing.** This was
    `_consent() -> str` - the reason, or `""` - which is the shape of exactly
    one gate in 82, and the consent sweep in `tests/test_question_presenter.py`
    calls `gate(config)` and unpacks `(allowed, reason)` for every entry in
    `capabilities.GATED`. It raised `TypeError: _consent() takes 0 positional
    arguments but 1 was given`, which aborted the sweep **on the line after
    this one**, so the file reported two failures that had nothing to do with
    it and the gate itself was never reached.

    Taking `config` rather than building one is the other half: a gate that
    reads its own `ChronoaConfig()` cannot be driven by the caller that is
    trying to shut the key, so the sweep's `config.set(key, "false")` would
    not be what the gate consulted. `resolve_conflict.py` carries the same
    note for the same reason.
    """
    reason = config.sense_allowed_reason("memory")
    return not reason, reason


def _listing(rows) -> str:
    if not rows:
        return (
            "Nothing is waiting. No conversation has left a sentence Chronoa "
            "thought was worth keeping without asking first - which is the "
            "point of asking."
        )
    lines = [f"{len(rows)} proposed fact(s), none of them remembered:", ""]
    for number, row in enumerate(rows, start=1):
        where = f"said in turn {row.source_index + 1}" if row.source_role else ""
        lines.append(f"  {number}. {row.text}")
        lines.append(f"     ({row.session_id}, {where})")
    lines.append("")
    lines.append(
        "Say which number to keep or to discard, or ask to keep all of them. "
        "Nothing is stored until you say so."
    )
    return "\n".join(lines)


def _run(arguments: dict) -> str:
    from shani_chronoa.senses import memory

    action = str(arguments.get("action") or "list").strip().lower()
    allowed, reason = _consent(ChronoaConfig())
    if not allowed:
        return (f"{reason}. Nothing was changed, and nothing is remembered - "
                f"this is what Chronoa noticed last night and did not save.")
    try:
        rows = memory.pending_proposals()
    except Exception as exc:  # noqa: BLE001 - the sense may not be importable
        return f"The sleep pass that proposes facts is not available: {exc}"

    if action == "list":
        return _listing(rows)

    if action not in _ANSWER:
        return (f"'{action}' is not one of list, keep or discard. Nothing was "
                f"changed.")

    raw = arguments.get("number")
    if raw is None:
        return ("Which proposal? Pass number=... counting from 1 as the list "
                "showed them. Nothing was changed.")
    try:
        wanted = int(raw)
    except (TypeError, ValueError):
        return f"{raw!r} is not a number in a list of proposals. Nothing was changed."
    if not 1 <= wanted <= len(rows):
        return (f"There is no proposal {wanted} - the list has "
                f"{len(rows)}. Nothing was changed.")

    row = rows[wanted - 1]
    outcome, reason = memory.decide_candidate(
        row.session_id, row.index, _ANSWER[action])
    if outcome == "kept":
        return f"Remembered: {row.text}"
    if outcome == "kept-unrecorded":
        return (f"You agreed to it and it is recorded as agreed, but it is not "
                f"in memory: {reason}")
    if outcome == "declined":
        return f"Dropped: {row.text}. It will not be offered again."
    if outcome == "already-decided":
        return f"{row.text} was already {reason}."
    return reason


SKILLS = [Skill(name="review_proposals", schema=SCHEMA, run=_run)]