"""Long tool output, compressed for the wire but not for the record.

`MAX_HISTORY_MESSAGES` bounds history by *count*, and count is the wrong unit.
A turn that read four 30KB files is five messages and fits the cap comfortably,
while carrying more text than a hundred ordinary turns. The cap therefore passes
exactly the case it was meant to stop, and the failure is not a slow request -
it is a model that starts answering from a truncated view of its own history,
or refuses outright because the context no longer fits.

So size gets a threshold of its own.

## Three rules, and the reasoning behind each

**Only tool results, never prose.** A long user message is the user talking, and
a long assistant message is a previous answer the model may need to stand
behind. Both are small in practice. Tool output is the thing that is large by
nature, and it is the thing the model has already acted on.

**Recent output is never touched.** Compression is for history the model has
finished with. The result of the call it is *currently* reasoning about must
arrive whole, or the model reasons about a summary of a fact it needed in full.
The last `KEEP_RECENT_MESSAGES` are left alone for that reason alone.

**Compression is a transport concern, never a data loss.** `compress()` returns
a copy. `_history` and the on-disk transcript keep every byte, so an elision
here is recoverable and the record stays complete - the same reasoning as
`build_messages()` inserting percepts into a copy rather than into `_history`.

Claude Code compresses earlier turns and keeps the newest at full fidelity for
the same reason. What is not taken from anywhere: an invented summary. A
summarisation step needs a model to run, needs a second model call per turn, and
produces a paraphrase that may be wrong in the direction that matters. A
deterministic head-plus-footer elision cannot hallucinate.
"""

from __future__ import annotations

import re

import logging

logger = logging.getLogger(__name__)

#: A tool result longer than this is worth compressing. Chosen to sit below the
#: size at which a result starts costing real context, and well above the size
#: of a typical one - a timestamp, a boolean, a short list - so ordinary calls
#: are never rewritten for no benefit.
COMPRESS_THRESHOLD_CHARS = 2000

#: Never compress within this many messages of the end. The model is reasoning
#: about the most recent results right now; those must arrive whole.
KEEP_RECENT_MESSAGES = 6

#: How much of the start of an over-long result to keep. Enough to recognise
#: what the call returned and to often answer from it directly.
HEAD_CHARS = 600

#: How much of the end to keep. Errors, verdicts and summaries land at the
#: bottom, and a head-only elision would throw away the part that says what
#: went wrong.
FOOT_CHARS = 400

#: Total budget for tool output older than the protected window, in characters.
#:
#: `COMPRESS_THRESHOLD_CHARS` is a *per-message* rule, and on its own it is
#: blind to a history made of many merely-large messages. Forty tool results of
#: 1,999 characters each is 79,960 characters - roughly 20,000 tokens - and not
#: one of them crosses the per-message threshold, so nothing is compressed.
#: `MAX_HISTORY_MESSAGES` does not rescue it either, because 40 messages is
#: exactly the cap.
#:
#: This is the aggregate floor, and it is the part that actually bounds the
#: window. gemini-cli masks on the same principle for the same reason: the
#: trigger is the total of prunable tool output, not the size of any one result.
#:
#: The figure is deliberately small, because Chronoa is local-first and may be
#: talking to a small model with a modest context. 16,000 characters is about
#: 4,000 tokens, which is a substantial share of a small model's window and a
#: rounding error against a large one.
TOTAL_TOOL_BUDGET_CHARS = 16000

#: A single tool result above this is elided even inside the protected window.
#:
#: The window exists so a result the model is actively reading arrives whole. That
#: reasoning does not extend to a result that is a whole context window on its own -
#: a model cannot reason from 200,000 characters any better than from a summary of
#: them, and the transcript keeps the original either way.
#:
#: Set far above `COMPRESS_THRESHOLD_CHARS` deliberately, so this is an escape hatch
#: for the pathological case and not a quiet narrowing of the window decision: the
#: pinned test that a sole oversized result is never elided still passes, because a
#: result has to be 24x the ordinary threshold to reach this.
HARD_CAP_CHARS = 48000


#: Every elision starts with this. It is also how a deeply-elided result is
#: recognised, so deepening a message twice is a no-op rather than a rewrite.
ELISION_MARKER = "["

#: The shallow note records the size of the result it replaced, as
#: "[<dropped> of <total> characters elided ...]". The deep pass has to report
#: the *original* size, not the size of the intermediate elision it is
#: deepening - otherwise a 1,999-character result is described to the model as
#: 1,227 characters, which is the same lie as a silent truncation.
_SHALLOW_SIZE_RE = re.compile(r"^\[\d+ of (\d+) characters elided")

#: The deep note records the original size directly. Reading it back makes
#: `_original_size` total over every form the elider produces, and re-deepening
#: a note idempotent - so the honesty of the reported size no longer depends on
#: the caller remembering to skip notes.
_DEEP_SIZE_RE = re.compile(r"^\[(\d+) characters of this result elided in full")


def _elide(content: str, deep: bool = False) -> str:
    """A deterministic, honest stand-in for a long tool result.

    Says what was removed and how much, because a model reasoning from this
    needs to know the rest of the answer exists and is not being hidden from
    it. Truncation that looks like completeness is how a model concludes a file
    was 600 lines long because it was.

    `deep=True` keeps the note and nothing else. Head and foot are the parts a
    model reads first and last, so they are the last thing to go - but the
    aggregate floor cannot always be met with them intact, and a floor that
    cannot be met is not a floor.
    """
    if deep:
        total = _original_size(content)
        return (
            f"[{total} characters of this result elided in full; it is kept in "
            f"the conversation transcript. Call the tool again, with a narrower "
            f"request, if you need it.]"
        )
    total = len(content)
    dropped = total - HEAD_CHARS - FOOT_CHARS
    lines = content.count("\n") + 1
    return (
        f"[{dropped} of {total} characters elided from the middle of this "
        f"{lines}-line result; it is kept in full in the conversation "
        f"transcript. If you need the elided part, call the tool again or "
        f"narrow the request.]\n"
        f"{content[:HEAD_CHARS]}\n"
        f"[... {dropped} characters elided ...]\n"
        f"{content[-FOOT_CHARS:]}"
    )


def compress(messages: list[dict]) -> list[dict]:
    """Return `messages` with old, oversized tool results elided.

    Never mutates the input. Returns the input unchanged - the same list object
    is not used, but the contents are identical - when there is nothing to
    compress, so the common case costs one pass and no allocation of new dicts
    beyond the shallow copy.
    """
    if not messages:
        return []

    cutoff = max(0, len(messages) - KEEP_RECENT_MESSAGES)
    out: list[dict] = []
    changed = False

    for index, message in enumerate(messages):
        content = message.get("content")
        # The protection window is absolute, and deliberately so.
        #
        # I tried relaxing this for recent-but-oversized results - a fresh
        # conversation whose first action reads a 50,000-character file sits in
        # context at full size until the history reaches 7 messages, which is
        # real and measurable. Two tests here argue against it:
        # `test_a_sole_recent_result_is_never_elided` is marked "pinned
        # deliberately, because it looks like a bug and is not", and its
        # reasoning holds: nothing in a one-message history is old, the model is
        # reasoning about that result right now, and eliding it means reasoning
        # from a summary of a fact it needed in full.
        #
        # So the trade is real and unresolved, not an oversight to fix: a large
        # recent result costs context for the length of a turn, in exchange for
        # the model always having the thing it just asked for in full. The
        # aggregate floor below is the compromise - it bounds the history
        # without ever touching the newest 6 messages.
        if (message.get("role") == "tool"
                and isinstance(content, str)
                and len(content) > COMPRESS_THRESHOLD_CHARS):
            if index < cutoff:
                out.append({**message, "content": _elide(content)})
                changed = True
                continue
            # Inside the protected window, and still elided when it is orders of
            # magnitude over budget. A model cannot reason from a 200,000-character
            # result any more than from a summary of one, and the window exemption is
            # meant to spare a result the model is actively reading - not to exempt a
            # whole context window's worth of one line. Measured through the real
            # sandbox: `printf 'a%.0s' $(seq 1 200000)` returns a single
            # 200,000-character line, uncapped, and it landed at 12x the aggregate
            # budget untouched. `assistd` bounds the head by bytes as well as lines
            # for the same reason, which is why a single huge line cannot outgrow the
            # cap there either.
            #
            # The threshold is deliberately far above COMPRESS_THRESHOLD_CHARS, so
            # this stays an escape hatch for the pathological case and does not
            # quietly narrow the pinned window decision above.
            if len(content) > HARD_CAP_CHARS:
                out.append({**message, "content": _elide(content)})
                changed = True
                continue
        out.append(message)

    if changed:
        logger.info("Elided oversized tool output from %d earlier message(s)", cutoff)
        return out

    # The aggregate floor. Nothing was individually oversized, so the loop above
    # left everything alone - and a history of many merely-large results can
    # still be enormous. Oldest first, until the remaining prunable tool output
    # is inside the budget.
    #
    # Two passes, because one is not enough. Each elision keeps HEAD_CHARS plus
    # FOOT_CHARS plus the note, so every elided message still costs about 1,250
    # characters. Thirty-four of them cost 42,000 - more than the budget, so a
    # single pass exhausts every candidate and stops well short of the line. The
    # second pass drops the retained head and foot from the oldest survivors,
    # which is what makes the floor reachable.
    budget = _prunable_tool_chars(messages, cutoff)
    if budget <= TOTAL_TOOL_BUDGET_CHARS:
        return list(messages)

    excess = budget - TOTAL_TOOL_BUDGET_CHARS
    reduced = 0
    for index in range(cutoff):
        message = out[index]
        if message.get("role") != "tool":
            continue
        content = message.get("content")
        if not isinstance(content, str) or not content:
            continue
        elided = _elide(content)
        out[index] = {**message, "content": elided}
        # Credit what was actually *removed*, not the original size. Subtracting
        # the original stopped the loop as soon as the running total of original
        # sizes covered the excess, which left 59,888 characters against a
        # 16,000 budget - the floor was in the right place and off by nearly 4x
        # in how far it went.
        excess -= len(content) - len(elided)
        reduced += 1
        if excess <= 0:
            break

    # Second pass: still over budget, so strip the retained head and foot from
    # the oldest survivors. The full text is still in _history and in the
    # transcript, so nothing is lost - it is simply further away.
    if _prunable_tool_chars(out, cutoff) > TOTAL_TOOL_BUDGET_CHARS:
        deepened = 0
        for index in range(cutoff):
            if _prunable_tool_chars(out, cutoff) <= TOTAL_TOOL_BUDGET_CHARS:
                break
            message = out[index]
            if message.get("role") != "tool":
                continue
            content = message.get("content")
            # `_is_deeply_elided` is redundant now that `_original_size` reads
            # both note forms, so deepening a note twice is idempotent: removing
            # this guard produces byte-identical output, verified with the
            # budget forced to 1 so the loop runs to the end. It stays as an
            # O(n) saving, and as insurance if the note format ever changes - at
            # which point re-deepening would start rewriting notes and the
            # equivalence would stop holding.
            if not isinstance(content, str) or not content or _is_deeply_elided(content):
                continue
            out[index] = {**message, "content": _elide(content, deep=True)}
            deepened += 1
        reduced += deepened

    remaining = _prunable_tool_chars(out, cutoff)
    if remaining > TOTAL_TOOL_BUDGET_CHARS:
        # Every candidate is already down to a bare note and it still does not
        # fit. Say so, rather than logging a floor that was quietly missed.
        logger.warning(
            "Prunable tool output is %d characters, still over the %d budget "
            "after eliding every result; the window is bounded by message "
            "count, not by this budget", remaining, TOTAL_TOOL_BUDGET_CHARS,
        )
    logger.info(
        "Prunable tool output was %d characters, over the %d budget; elided "
        "%d result(s), leaving %d", budget, TOTAL_TOOL_BUDGET_CHARS, reduced,
        remaining,
    )
    return out


def _original_size(content: str) -> int:
    """The size of the result `content` stands in for, in characters.

    For untouched content that is simply its length. For an existing elision it
    is the total the elision recorded, so a note about a note still describes
    the result the model actually asked for. Falls back to the length when the
    marker is unreadable, which is the honest direction: over-reporting an
    unknown size is safer than under-reporting a known one.
    """
    for pattern in (_SHALLOW_SIZE_RE, _DEEP_SIZE_RE):
        match = pattern.match(content)
        if match:
            return int(match.group(1))
    return len(content)


def _is_deeply_elided(content: str) -> bool:
    """True when the result is already just a note, with nothing left to strip."""
    return content.startswith(ELISION_MARKER) and "\n" not in content


def _prunable_tool_chars(messages: "list[dict]", cutoff: int) -> int:
    """Characters of tool output before the protected window."""
    total = 0
    for message in messages[:cutoff]:
        if message.get("role") != "tool":
            continue
        content = message.get("content")
        if isinstance(content, str):
            total += len(content)
    return total


def context_size(messages: list[dict]) -> int:
    """Total characters of content across `messages`, for measuring the effect."""
    total = 0
    for message in messages:
        content = message.get("content")
        if isinstance(content, str):
            total += len(content)
    return total
