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


def _elide(content: str) -> str:
    """A deterministic, honest stand-in for a long tool result.

    Says what was removed and how much, because a model reasoning from this
    needs to know the rest of the answer exists and is not being hidden from
    it. Truncation that looks like completeness is how a model concludes a file
    was 600 lines long because it was.
    """
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
        if (index < cutoff
                and message.get("role") == "tool"
                and isinstance(content, str)
                and len(content) > COMPRESS_THRESHOLD_CHARS):
            out.append({**message, "content": _elide(content)})
            changed = True
        else:
            out.append(message)

    if not changed:
        return list(messages)
    logger.info("Elided oversized tool output from %d earlier message(s)", cutoff)
    return out


def context_size(messages: list[dict]) -> int:
    """Total characters of content across `messages`, for measuring the effect."""
    total = 0
    for message in messages:
        content = message.get("content")
        if isinstance(content, str):
            total += len(content)
    return total
