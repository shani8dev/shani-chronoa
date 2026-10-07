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
the same reason.

## What was added, and what was deliberately not

The elision above is deterministic and therefore cannot hallucinate. Everything
below it is optional and off unless a caller supplies a summarizer, for the
same reason: a summary needs a model to run, needs a model call per turn, and
can be wrong in the direction that matters. So the default path through
`compress()` is unchanged, and each addition can be switched on independently.

- **Per-line byte cap.** `MAX_LINE_CHARS`, from codex's `tool_output.rs:19`,
  which caps a preview *line* at 16 KiB and applies the byte cap *before*
  wrapping, because "a single tool-result line can be megabytes long".
  Wrapping first is a stall and an OOM; here the equivalent hazard is the
  head-plus-footer assembly, so the cap is applied to the pieces before they
  are joined. Measured reason this is not theoretical: `printf 'a%.0s' $(seq
  1 200000)` is one 200,000-character line.
- **Spill to file.** From goose's `streaming_buffer.rs:69`, which keeps 20
  lines and spills the rest to a temp file, printing the path. The note the
  model reads used to say "call the tool again or narrow the request", which
  is advice that cannot work for a deterministic result: `read_text_file` on the
  same path returns the same bytes and gets elided identically, forever. Now the
  untruncated text is on disk and the path is in the result.
- **Omitted-line count.** Both goose (`... (N more lines)`) and codex (an
  `omitted` counter that counts a partially-displayed line as hidden) report
  the elided count rather than dropping it silently. Character counts alone do
  not convey it: a 200,000-character result that is *one* line reads as though
  nothing much was lost.
- **`chronoa_compression` parallel field.** From agno's
  `compression/manager.py:161`, which writes `tool_msg.compressed_content`
  while `content` stays intact - lossless, inspectable, reversible. Chronoa's
  message dicts are forwarded to the provider verbatim (`ollama_llm.py:56` does
  `{**msg, ...}`), so a field the consumer chooses between is not available
  here; the equivalent that *is* available is a small record beside the reduced
  text carrying the original size, the original line count, a SHA-256 of the
  original bytes, and the spill path. That makes a compressed result
  self-describing and verifiable: given the spill file you can prove you are
  looking at the same bytes the elision replaced, which is the property agno's
  parallel field buys.
- **`summarize_history`.** Depth-bounded recursive summarization from aider's
  `history.py:33-96`, with a budget that is a ratio of the model's input window
  rather than a magic number. Elision alone is not budget-convergent - it can
  report that it missed the floor. Recursion is.
"""

from __future__ import annotations

import hashlib
import os
import re
from pathlib import Path
from typing import NamedTuple

import logging

from shani_chronoa import files

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

#: The longest single line that may be placed into a compressed result.
#:
#: codex caps a preview *line* rather than a preview *result*
#: (`codex-rs/tui/src/tool_output.rs:19`, `MAX_PREVIEW_LINE_BYTES = 16 * 1024`)
#: and applies the byte cap **before** wrapping, with the reason in the source:
#: "a single tool-result line can be megabytes long". Wrapping first is a
#: classic stall and OOM; Chronoa's equivalent expensive transform is the
#: head-plus-footer assembly below, so the cap is applied to the pieces before
#: they are joined, never after.
#:
#: Measured reason this is not theoretical: `printf 'a%.0s' $(seq 1 200000)`
#: returns one 200,000-character line, and `HEAD_CHARS`/`FOOT_CHARS` bound only
#: how much of it is kept - never how long the kept part may be.
MAX_LINE_CHARS = 16 * 1024

#: Name of the parallel record carried beside a reduced result. See the module
#: docstring: the message dicts are forwarded to the provider verbatim, so the
#: agno-style choice-of-two-fields is not available and this is the nearest
#: equivalent that makes a compressed result self-describing.
COMPRESSION_FIELD = "chronoa_compression"

#: Whether the model has a delegation tool (a subagent it can hand a task to).
#: Kilo's `truncate.ts` changes its hint when one exists - "delegate to save
#: context" beats "call the tool again", because a subagent spends *its*
#: context, not this conversation's. Chronoa has no subagent tool today, so
#: the default is False and the note is byte-identical to what it was; the
#: parameter is what keeps the default honest rather than a silent behaviour
#: change.
HAS_DELEGATION_TOOL = False

#: Prune constants (kilo's compaction.ts prune) — a backward pass that clears
#: old tool results in place, replacing their content with a marker, until the
#: prunable budget is met. This is distinct from elision (which keeps head+foot);
#: prune removes the content entirely from the wire, while the transcript keeps
#: the original.
PRUNE_PROTECT_CHARS = 40000   # never prune within this many chars of the end
PRUNE_MINIMUM_CHARS = 20000   # stop pruning when remaining drops to this
PRUNE_MARKER = "[Old tool result content cleared]"
# Skills are protected from pruning - their output may contain state the model
# needs to continue. See kilo's `protectedTools: ["skill"]`.
PROTECTED_TOOL_ROLES = frozenset(["skill"])

#: Subdirectory of the app's state directory that receives spilled tool output.
SPILL_SUBDIR = "tool-output"

#: A result larger than this is not spilled. The spill exists so an elision is
#: recoverable, and an 8 MiB result is not one a context window would ever hold;
#: writing it would trade a bounded context problem for an unbounded disk one.
#: Exceeding it degrades to "no path in the note", which is still honest.
MAX_SPILL_CHARS = 8 * 1024 * 1024


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


def _spill_dir() -> Path:
    """Where an elided result's untruncated text is written.

    Resolved per call and never at import time, and `$XDG_STATE_HOME` is
    honoured with the `~/.local/state` fallback. Both halves matter here and
    both are this repo's own recorded lesson: `skills/timer.py` resolves its
    store the same way, and a module-level constant captured from `$HOME` at
    import has already twice contaminated a real user's directories
    (`tests/conftest.py` carries an autouse fixture per store for exactly this,
    and `PerceptStore.DURABLE_FILE` is named in `AGENTS.md` as having done it).
    Resolving late is also what lets the whole suite run contained by pointing
    `XDG_STATE_HOME` at a temp dir.

    The name is not `tool_tracking.py`'s or `senses/store.py`'s
    (`~/.local/share/shani-chronoa/...`) but `skills/timer.py`'s, because this
    is genuinely state - derived text, safe to lose, not a record - and because
    the data-dir form does not move with `XDG_STATE_HOME`.
    """
    base = os.environ.get("XDG_STATE_HOME") or str(Path.home() / ".local" / "state")
    return Path(base) / "shani-chronoa" / SPILL_SUBDIR


def _spill(content: str) -> "str | None":
    """Write `content` out in full and return its path, or None.

    The fix for an elision the model cannot act on. The note used to say "call
    the tool again or narrow the request"; for a deterministic result that is
    advice which cannot work, because the same call returns the same bytes and
    is elided identically every time. goose spills to a temp file and prints
    the path for the same reason (`streaming_buffer.rs:69`).

    Content-addressed, so the same result elided on every turn of a long
    conversation is written once rather than once per turn. Written to a temp
    name and renamed into place, because a reader - the model, on the next
    request - must never see half a file; that is the same trap
    `PerceptStore` publishes `live.json` around.

    Never raises. A failed spill means the note describes the elision without a
    path, which is strictly better than `compress()` taking down the turn.
    """
    if len(content) > MAX_SPILL_CHARS:
        logger.warning("Not spilling a %d-character result: over the %d limit",
                       len(content), MAX_SPILL_CHARS)
        return None
    try:
        directory = _spill_dir()
        files.ensure_private_dir(directory)
        digest = hashlib.sha256(content.encode("utf-8", "replace")).hexdigest()[:16]
        target = directory / f"{digest}-{len(content)}.txt"
        if not target.exists():
            tmp = target.with_name(f".{target.name}.{os.getpid()}.tmp")
            tmp.write_text(content, encoding="utf-8")
            files.restrict_file(tmp)
            os.replace(tmp, target)
            files.restrict_file(target)
        return str(target)
    except OSError as e:
        logger.warning("Could not spill a %d-character result to disk: %s",
                       len(content), e)
        return None


def _cap_lines(text: str, limit: int = MAX_LINE_CHARS) -> "tuple[str, int]":
    """Each line of `text` cut to `limit` characters. Returns `(text, hidden)`.

    `hidden` counts *lines*, and a line cut even slightly counts as one - codex's
    rule, and the one that makes the count mean anything: a line shown in part
    is a line the model has not been given.

    Applied to the retained head and foot separately, before they are joined.
    Applying it to the joined result would be applying it after the transform
    it exists to bound, which is the ordering both upstream implementations get
    right and the one a `content[:HEAD_CHARS]` shortcut gets wrong.
    """
    lines = text.split("\n")
    if max((len(line) for line in lines), default=0) <= limit:
        return text, 0
    kept = []
    hidden = 0
    for line in lines:
        if len(line) <= limit:
            kept.append(line)
            continue
        kept.append(f"{line[:limit]}[... {len(line) - limit} more characters on this line]")
        hidden += 1
    return "\n".join(kept), hidden


def _omitted_line_count(content: str, head: str, foot: str) -> int:
    """How many of `content`'s lines the result does not carry in full.

    Counts the lines dropped outright plus the lines shown only in part - the
    second half being codex's rule, and the one that makes the count mean
    anything: a line shown in part is a line the model has not been given.

    The two halves must not be counted independently. A result shorter than
    `HEAD_CHARS + FOOT_CHARS` has its head and foot cut out of the *same*
    logical line, so counting both cut points reports "2 lines not shown" for a
    one-line result - the same class of lie as reporting none, in the opposite
    direction. Clamped to the real line count for exactly that reason.
    """
    total = content.count("\n") + 1
    length = len(content)
    partial = 0
    spanned = 0
    for start, end in ((0, min(HEAD_CHARS, length)), (max(0, length - FOOT_CHARS), length)):
        if start >= end:
            continue
        if start > 0 and content[start - 1] != "\n":
            partial += 1
        if end < length and content[end - 1] != "\n":
            partial += 1
        spanned += content.count("\n", start, end) + 1
    return min(total, max(0, total - spanned) + partial)


def _describe(kind: str, original: str, *, spill: "str | None" = None,
              omitted_lines: int = 0, summary: str = "") -> dict:
    """The `COMPRESSION_FIELD` record carried beside a reduced result.

    agno's `compressed_content` is lossless, inspectable and reversible
    because `content` is left intact beside it. Chronoa's dicts go to the
    provider verbatim (`ollama_llm.py:56` is `{**msg, ...}`), so keeping the original
    on the wire would defeat the entire saving and shipping a *second* copy of
    the summary would waste what was just won. So the parallel record holds the
    provenance instead: what the original was, how big, and where the full text
    is. That is what makes the reduction reversible - open the spill file,
    check the digest, and you have the exact bytes the note describes.
    """
    record = {
        "kind": kind,
        "original_chars": len(original),
        "original_lines": original.count("\n") + 1,
        "sha256": hashlib.sha256(original.encode("utf-8", "replace")).hexdigest()[:16],
        "omitted_lines": omitted_lines,
    }
    if summary:
        record["summary_chars"] = len(summary)
    if spill:
        record["spill_path"] = spill
    return {COMPRESSION_FIELD: record}


def _elide(content: str, deep: bool = False, spill: "str | None" = None,
           has_delegation_tool: bool = False) -> str:
    """A deterministic, honest stand-in for a long tool result.

    Says what was removed and how much, because a model reasoning from this
    needs to know the rest of the answer exists and is not being hidden from
    it. Truncation that looks like completeness is how a model concludes a file
    was 600 lines long because it was.

    Three things are disclosed, and the third is the one that was missing:
    how many characters, how many *lines*, and where the untruncated text
    actually is. goose (`... (N more lines)`) and codex (an `omitted` counter
    that counts a partially-shown line as hidden) both report the line count,
    and a character count alone is not that: 199,000 of 200,000 characters
    elided out of a *one-line* result reads as though very little was lost.

    `spill` is the path goose prints. Without it the advice is "call the tool
    again", which cannot work for a deterministic result.

    `has_delegation_tool` changes the advice the way kilo's `hasTaskTool`
    does: when the model can hand the re-read to a subagent, the note says
    so, because a subagent spends its own context and not this conversation's.
    With it False (Chronoa today) the advice is the original sentence, so the
    default output is byte-identical.

    `deep=True` keeps the note and nothing else. Head and foot are the parts a
    model reads first and last, so they are the last thing to go - but the
    aggregate floor cannot always be met with them intact, and a floor that
    cannot be met is not a floor.
    """
    if deep:
        total = _original_size(content)
        return (
            f"[{total} characters of this result elided in full; it is kept in "
            f"the conversation transcript{_spill_clause(spill)}. "
            f"{_readvice(spill, has_delegation_tool)}]"
        )
    total = len(content)
    dropped = total - HEAD_CHARS - FOOT_CHARS
    lines = content.count("\n") + 1
    # The byte cap is applied to the pieces, before they are joined - see
    # `_cap_lines` on why the ordering is the whole point.
    head, head_hidden = _cap_lines(content[:HEAD_CHARS])
    foot, foot_hidden = _cap_lines(content[-FOOT_CHARS:])
    omitted = _omitted_line_count(content, head, foot) + head_hidden + foot_hidden
    plural = "" if omitted == 1 else "s"
    return (
        f"[{dropped} of {total} characters elided from the middle of this "
        f"{lines}-line result; {omitted} line{plural} not shown in full"
        f"{_spill_clause(spill)}. {_readvice(spill, has_delegation_tool)}]\n"
        f"{head}\n"
        f"[... {dropped} characters elided ...]\n"
        f"{foot}"
    )


def _readvice(spill: "str | None", has_delegation_tool: bool) -> str:
    """The sentence telling the model how to get the elided part back.

    Adapts to what the model can actually do, which is the whole of kilo's
    `hasTaskTool` change: the advice is only useful if it names a tool the
    model has. A delegation tool means the re-read costs the subagent's
    context, not this conversation's, so it is the better advice; without one
    the original two options stand.
    """
    if has_delegation_tool:
        return (
            "If you need the elided part, delegate this to a subagent to save "
            "context, or read the spilled file."
        )
    return (
        "If you need the elided part, read that file or call the tool again "
        "with a narrower request."
    )


def _spill_clause(spill: "str | None") -> str:
    """The sentence naming where the full text is, or "" when there is none."""
    return f"; the untruncated text is at {spill}" if spill else ""


def _reduce(message: dict, content: str, *, deep: bool = False,
            summarizer=None, has_delegation_tool: bool = False) -> dict:
    """The one place a tool result is reduced. Summary if asked, else elision.

    Every reduction carries `COMPRESSION_FIELD`, and every reduction spills the
    original first. Both happen here rather than at the four call sites in
    `compress()` because a spill that is only performed on some of the paths is
    a spill that is missing on whichever path nobody tested.
    """
    if summarizer is not None and not deep:
        summary = _summarize_or_none(content, summarizer)
        if summary:
            spill = _spill(content)
            return {**message, "content": summary,
                    **_describe("summary", content, spill=spill, summary=summary)}
    spill = _spill(content)
    elided = _elide(content, deep=deep, spill=spill,
                    has_delegation_tool=has_delegation_tool)
    omitted = _omitted_line_count(content, *(_cap_lines(content[:HEAD_CHARS]),
                                            _cap_lines(content[-FOOT_CHARS:])))
    return {**message, "content": elided,
            **_describe("elision", content, spill=spill, omitted_lines=omitted)}


# --------------------------------------------------------------------------
# Optional summarisation. Off unless a caller supplies a summarizer.
# --------------------------------------------------------------------------

#: agno's `DEFAULT_COMPRESSION_PROMPT` (`compression/manager.py:16-49`),
#: ported as the rubric. It is not "summarize this": the preserve/remove split
#: is the entire safety property of summarising a tool result, because a summary
#: that keeps the prose and drops the error code is worse than no summary - the
#: model cannot tell the difference between "there was no error" and "the error
#: was removed".
SUMMARIZE_PROMPT = (
    "You are compressing a tool call result to save context space while "
    "preserving critical information.\n\n"
    "ALWAYS PRESERVE:\n"
    "- Specific facts: numbers, statistics, amounts, prices, quantities, metrics\n"
    "- Temporal data: dates, times, timestamps (short format: 'Oct 21 2025')\n"
    "- Entities: people, companies, products, locations, organizations\n"
    "- Identifiers: URLs, IDs, codes, technical identifiers, versions\n"
    "- Key quotes, citations, sources\n\n"
    "COMPRESS TO ESSENTIALS:\n"
    "- Descriptions: keep only key attributes\n"
    "- Explanations: distill to core insight\n"
    "- Lists: focus on the most relevant items\n"
    "- Background: minimal context only if critical\n\n"
    "REMOVE ENTIRELY:\n"
    "- Introductions, conclusions, transitions\n"
    "- Hedging language ('might', 'possibly', 'appears to')\n"
    "- Meta-commentary ('According to', 'The results show')\n"
    "- Formatting artifacts (markdown, HTML, JSON structure)\n"
    "- Redundant or repetitive information\n"
    "- Generic background not relevant to the task\n"
    "- Promotional language, filler words\n\n"
    "Be concise while retaining all critical facts."
)

#: agno's count trigger (`manager.py:69-103`), default 3. Compressing one large
#: result out of a context of small ones buys nothing; compressing the fourth
#: is usually the point.
SUMMARIZE_COUNT_LIMIT = 3

#: aider's budget is a ratio of the model's own input window, not a constant
#: (`models.py:358`: `min(max(max_input_tokens / 16, 1024), 8192)`). A history
#: worth summarising on a 32k model is a rounding error on a 128k one, and the
#: constant that suits one is wrong for the other.
SUMMARY_BUDGET_RATIO = 16
SUMMARY_BUDGET_MIN_CHARS = 1024
SUMMARY_BUDGET_MAX_CHARS = 8192

#: aider recurses at most this deep before collapsing to one flat summary
#: (`history.py:33-96`, `depth > 3`). Past it, the head is already a summary and
#: re-splitting it produces summaries of summaries, which is where a summary
#: starts losing the numbers it was written to preserve.
MAX_SUMMARY_DEPTH = 3


def summary_budget_chars(max_input_tokens: "int | None" = None) -> int:
    """The character budget a summarized history must fit.

    The ratio, clamped, from aider. With no known window (the unit tests, and
    `compress()` called without one) the module's own aggregate floor is used -
    which is the same quantity expressed in the units this file already uses.
    """
    if not max_input_tokens:
        return TOTAL_TOOL_BUDGET_CHARS
    return min(max(max_input_tokens // SUMMARY_BUDGET_RATIO,
                   SUMMARY_BUDGET_MIN_CHARS),
               SUMMARY_BUDGET_MAX_CHARS)


def _summarize_or_none(content: str, summarizer) -> "str | None":
    """`summarizer(SUMMARIZE_PROMPT, content)`, or None if it did not work.

    Non-fatal by construction, from agno `manager.py:138-140`, which returns the
    *original* content when the compression model errors rather than propagating.
    A summarizer here is an extra model call, and an extra model call can fail -
    an unreachable Ollama, a model that has been switched mid-turn, a context
    overflow in the summarizer's own request. None of those are reasons to fail
    the turn, and degrading to no-compression is exactly the pre-existing
    behaviour, so returning None makes the failure invisible rather than fatal.
    """
    try:
        summary = summarizer(SUMMARIZE_PROMPT, content)
    except Exception as e:  # noqa: BLE001 - an extra model call must not be fatal
        logger.warning("Summarizing a %d-character result failed (%s); keeping "
                       "the deterministic elision", len(content), e)
        return None
    if not isinstance(summary, str) or not summary.strip():
        return None
    return summary


def should_summarize(messages: list[dict], *, max_input_tokens: "int | None" = None,
                     count_limit: int = SUMMARIZE_COUNT_LIMIT) -> bool:
    """Either trigger fires: the size budget, or the uncompressed-result count.

    agno runs both and returns on whichever hits first (`manager.py:69-103`),
    because they fail differently. The count trigger is what bounds a history of
    many merely-large results, which no per-message size rule would ever touch;
    the size trigger is what bounds a history of few enormous ones, which no
    count rule would touch. Either alone leaves a hole.
    """
    if context_size(messages) > summary_budget_chars(max_input_tokens):
        return True
    uncompressed = sum(
        1 for message in messages
        if message.get("role") == "tool"
        and isinstance(message.get("content"), str)
        and len(message["content"]) > COMPRESS_THRESHOLD_CHARS
        and not message.get(COMPRESSION_FIELD)
    )
    return uncompressed >= count_limit


def summarize_tool_results(messages: list[dict], summarizer,
                           *, max_input_tokens: "int | None" = None,
                           count_limit: int = SUMMARIZE_COUNT_LIMIT) -> list[dict]:
    """Summarize oversized tool results that have aged, into a parallel record.

    Returns copies; never mutates. Only results outside the protected window and
    only those already over `COMPRESS_THRESHOLD_CHARS` are eligible, so this is
    strictly narrower than `compress()` - it will not touch what the model is
    reasoning about right now, which is the same rule that governs elision and
    for the same reason.

    The original is never overwritten here: `_history` keeps it, the transcript
    keeps it, and the `COMPRESSION_FIELD` record carries its digest and the path
    to the spill.
    """
    if summarizer is None or not messages:
        return list(messages)
    cutoff = max(0, len(messages) - KEEP_RECENT_MESSAGES)
    if not should_summarize(messages, max_input_tokens=max_input_tokens,
                            count_limit=count_limit):
        return list(messages)
    out = list(messages)
    done = 0
    for index in range(cutoff):
        message = out[index]
        content = message.get("content")
        if (message.get("role") != "tool"
                or not isinstance(content, str)
                or len(content) <= COMPRESS_THRESHOLD_CHARS
                or message.get(COMPRESSION_FIELD)):
            continue
        summary = _summarize_or_none(content, summarizer)
        if not summary:
            continue
        spill = _spill(content)
        out[index] = {**message, "content": summary,
                      **_describe("summary", content, spill=spill, summary=summary)}
        done += 1
    if done:
        logger.info("Summarized %d tool result(s) against the %s rubric",
                    done, summary_budget_chars(max_input_tokens))
    return out


def _fingerprint(messages: "list[dict]") -> str:
    """A cheap identity for a message list, for the staleness guard below.

    Length alone is not enough - a summarizer that yields can have a message
    replaced in place - and `id()` alone is not enough either, because the list
    is not held by reference anywhere once `build_messages()` has returned it.
    """
    digest = hashlib.sha256()
    for message in messages:
        digest.update(repr((message.get("role"),
                            message.get("tool_call_id"),
                            message.get("content"))).encode("utf-8", "replace"))
    return f"{len(messages)}:{digest.hexdigest()[:16]}"


def summarize_history(messages: list[dict], summarizer, *,
                      max_input_tokens: "int | None" = None,
                      max_depth: int = MAX_SUMMARY_DEPTH,
                      min_split: int = 4) -> list[dict]:
    """Depth-bounded recursive summarization, from aider's `summarize_real`.

    Elision is not budget-convergent: `compress()` can reach a state where every
    candidate is a bare note and the window is still over budget, and all it can
    do about that is log a warning. Recursion converges, because the input to the
    next round is the output of this one.

    The algorithm, in aider's order: return untouched if under budget at depth 0;
    otherwise accumulate a tail backwards to half the budget so the recent tail
    survives verbatim; move the cut point back until the head ends on an
    assistant message; summarize the head; return if that now fits; otherwise
    recurse one level deeper, collapsing to a flat summarize-all past
    `MAX_SUMMARY_DEPTH`.

    **The staleness guard.** aider's `base_coder.py:1031-1032` compares the list
    it started with against the list the summary was computed from, and
    *discards* the summary if they differ. Applying it would silently drop
    turns. This is not a hypothetical hazard in a sync function: `compress()` is
    called from `build_messages()` with a fresh copy, and a summarizer built on
    this repo's own `AsyncBridge` runs the model call on another thread and
    delivers back through `GLib.idle_add` - so the assistant's history can
    genuinely grow while a summarization is in flight. On a detected change the
    summary is thrown away and the deterministic elision is returned instead,
    which is correct rather than merely safe.
    """
    before = _fingerprint(messages)
    budget = summary_budget_chars(max_input_tokens)
    result = _summarize_real(messages, summarizer, depth=0, max_depth=max_depth,
                             min_split=min_split,
                             max_input_tokens=max_input_tokens)
    if _fingerprint(messages) != before:
        logger.warning("The message list changed while it was being summarized; "
                       "discarding the summary rather than dropping turns")
        return compress(messages)
    remaining = context_size(result)
    if remaining > budget:
        # Recursion converges only if the summarizer actually shrinks. A model
        # that returns its input back, or a message that is on its own larger
        # than the whole budget, exhausts the depth bound and comes back over
        # budget. Saying so is the same discipline `compress()` applies when its
        # floor cannot be met, and it is the difference between a budget that is
        # reported and one that is quietly missed.
        logger.warning(
            "History is %d characters after summarizing, still over the %d "
            "budget; the summarizer is not reducing it further", remaining, budget,
        )
    return result


def _summarize_real(messages: list[dict], summarizer, *, depth: int,
                    max_depth: int, min_split: int,
                    max_input_tokens: "int | None") -> list[dict]:
    budget = summary_budget_chars(max_input_tokens)
    total = context_size(messages)
    if total <= budget and depth == 0:
        return list(messages)
    if len(messages) <= min_split or depth > max_depth:
        return _summarize_all(messages, summarizer)

    half = budget // 2
    tail_start = len(messages)
    tail_chars = 0
    for index in range(len(messages) - 1, -1, -1):
        size = _size(messages[index])
        if tail_chars + size >= half:
            break
        tail_chars += size
        tail_start = index

    while tail_start > 1 and messages[tail_start - 1].get("role") != "assistant":
        tail_start -= 1
    if tail_start <= min_split:
        return _summarize_all(messages, summarizer)

    head, tail = messages[:tail_start], messages[tail_start:]
    summary = _summarize_all(head, summarizer)
    result = summary + tail
    if context_size(result) <= budget:
        return result
    return _summarize_real(result, summarizer, depth=depth + 1, max_depth=max_depth,
                           min_split=min_split, max_input_tokens=max_input_tokens)


def _size(message: dict) -> int:
    """Characters this message contributes, as `context_size` counts them."""
    content = message.get("content")
    return len(content) if isinstance(content, str) else 0


def _summarize_all(messages: list[dict], summarizer) -> list[dict]:
    """One flat summary of a run of messages, keeping the newest intact.

    The collapse aider reaches at `depth > 3`. Prose is folded into a single
    summary message and each tool result is summarized on its own, so a result
    that matters can still be read back individually - collapsing everything
    into one blob is what makes a summary useless for the one question it was
    built to answer.

    If the summarizer fails, the prose transcript is kept *verbatim* rather than
    paraphrased into something smaller. A degraded summary is still a summary;
    a lossy one nobody asked for is not.
    """
    if not messages:
        return []
    system = [m for m in messages[:1] if m.get("role") == "system"]
    prose = [m for m in messages
             if m.get("role") in ("user", "assistant") and isinstance(m.get("content"), str)]
    results = [m for m in messages
               if m.get("role") == "tool" and isinstance(m.get("content"), str)]

    out = list(system)
    if prose:
        transcript = "".join(
            f"# {m['role'].upper()}\n{m['content']}\n" for m in prose)
        summary = _summarize_or_none(transcript, summarizer)
        out.append({"role": "user", "content": summary or transcript})
    for message in results:
        content = message["content"]
        summary = _summarize_or_none(content, summarizer)
        if summary:
            out.append({**message, "content": summary,
                        **_describe("summary", content, spill=_spill(content),
                                    summary=summary)})
        else:
            out.append(message)
    if not out:
        out = list(messages)
    return out


#: What the last `compress()` call changed, for the UI to be able to say so.
class Elision(NamedTuple):
    """How many messages were shortened, and by how much. `dropped` counts whole
    turns left out of the request by `local_llm.fit_to_context`, which is a
    different and larger loss - the model did not shorten them, it never saw
    them."""

    messages: int = 0
    chars: int = 0
    dropped: int = 0

    @property
    def anything(self) -> bool:
        return bool(self.messages or self.dropped)

    def sentence(self) -> str:
        """One honest line, or empty when nothing was cut."""
        bits = []
        if self.messages:
            bits.append(f"{self.messages} older tool result(s) shortened by "
                        f"about {self.chars:,} characters")
        if self.dropped:
            bits.append(f"{self.dropped} earlier message(s) left out of this "
                        f"request entirely")
        if not bits:
            return ""
        return "; ".join(bits) + " to fit the context window"


#: Reset at the top of every `compress()` call, so a request that compressed
#: nothing reports nothing rather than repeating the previous turn's news.
_LAST_ELISION = Elision()


def _record_elision(messages: int = 0, chars: int = 0, dropped: int = 0) -> None:
    global _LAST_ELISION
    _LAST_ELISION = Elision(messages=messages, chars=chars, dropped=dropped)


def last_elision() -> Elision:
    """What the most recent `compress()` call shortened. `NamedTuple`, so an
    empty answer is `Elision()` rather than `None` - a caller that forgets to
    check for `None` gets zeros, which is the safe reading."""
    return _LAST_ELISION


def compress(messages: list[dict], summarizer=None,
             has_delegation_tool: bool = HAS_DELEGATION_TOOL) -> list[dict]:
    """Return `messages` with old, oversized tool results elided.

    Never mutates the input. Returns the input unchanged - the same list object
    is not used, but the contents are identical - when there is nothing to
    compress, so the common case costs one pass and no allocation of new dicts
    beyond the shallow copy.

    `summarizer` is opt-in. With one, a result the elision would otherwise
    reduce to head-and-foot is summarised instead, using
    `SUMMARIZE_PROMPT`. `assistant.py:185` calls this with no summarizer, so the
    default path is byte-identical to the deterministic one.

    **What it cut is recorded, for a person to see.** `last_elision()` reports
    the messages shortened, the characters saved and the whole turns dropped.
    Compressing silently is how an assistant looks like it has forgotten
    something: the loss is real, it is deliberate, and until now the only trace
    of it was a `logger.info` line nobody reads.
    """
    _record_elision()          # a request that compresses nothing says nothing
    if not messages:
        return []

    cutoff = max(0, len(messages) - KEEP_RECENT_MESSAGES)
    out: list[dict] = []
    changed = False
    saved = 0
    shortened = 0

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
                out.append(_reduce(message, content, summarizer=summarizer,
                                 has_delegation_tool=has_delegation_tool))
                changed = True
                shortened += 1
                saved += len(content) - len(out[-1].get("content") or "")
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
                out.append(_reduce(message, content, summarizer=summarizer,
                                 has_delegation_tool=has_delegation_tool))
                changed = True
                shortened += 1
                saved += len(content) - len(out[-1].get("content") or "")
                continue
        out.append(message)

    if changed:
        logger.info("Elided oversized tool output from %d earlier message(s)", cutoff)
        _record_elision(shortened, saved)
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
        out[index] = _reduce(message, content,
                                        has_delegation_tool=has_delegation_tool)
        elided = out[index]["content"]
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
            out[index] = _reduce(message, content, deep=True,
                                            has_delegation_tool=has_delegation_tool)
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
    _record_elision(reduced, saved)
    return out


def prune(messages: list[dict], *,
          protect_chars: int = PRUNE_PROTECT_CHARS,
          minimum_chars: int = PRUNE_MINIMUM_CHARS) -> list[dict]:
    """Kilo's compaction prune: backward pass clearing old tool results in place.

    Walks backwards from the oldest prunable message toward the protected
    window, replacing each tool result's content with `PRUNE_MARKER` until
    the remaining prunable characters are <= `minimum_chars`. The full text
    remains in `_history` and the transcript - this only frees the wire.

    `protect_chars` is the tool-result character budget from the end that is
    never touched (equivalent to kilo's `PRUNE_PROTECT`). A tool result
    within this many characters of the end is immune. `minimum_chars` is the
    floor - pruning stops when the prunable portion reaches this (equivalent
    to kilo's `PRUNE_MINIMUM`).

    Skills (role == "skill") are protected from pruning because their output
    may contain state the model needs to continue.
    """
    if not messages:
        return []

    # Find the prunable window: tool results before the last `protect_chars`
    # characters of tool results.
    total_tool_chars = 0
    cutoff_index = 0
    for index in range(len(messages) - 1, -1, -1):
        if (messages[index].get("role") == "tool"
                and isinstance(messages[index].get("content"), str)):
            total_tool_chars += len(messages[index]["content"])
        if total_tool_chars >= protect_chars:
            cutoff_index = index
            break

    # Prunable messages are tool results before cutoff_index
    prunable_indices = [i for i in range(cutoff_index)
                        if messages[i].get("role") == "tool"
                        and isinstance(messages[i].get("content"), str)
                        and messages[i].get("content")
                        and messages[i].get("role") not in PROTECTED_TOOL_ROLES]

    if not prunable_indices:
        return list(messages)

    out = list(messages)
    pruned = 0
    remaining = sum(len(out[i].get("content", "")) for i in prunable_indices)

    # Walk backwards (oldest first) through prunable tool results
    for index in prunable_indices:
        if remaining <= minimum_chars:
            break
        message = out[index]
        content = message.get("content")
        if not isinstance(content, str) or not content:
            continue
        out[index] = {**message, "content": PRUNE_MARKER}
        remaining -= len(content) - len(PRUNE_MARKER)
        pruned += 1

    if pruned:
        logger.info("Pruned %d old tool result(s); prunable chars %d -> %d",
                    pruned, total_tool_chars, remaining)
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
