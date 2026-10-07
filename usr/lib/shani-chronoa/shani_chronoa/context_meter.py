"""What is actually in the prompt, and what it cost the window.

opencode shows a context meter with a per-segment breakdown
(`packages/app/src/components/session/session-context-breakdown.ts:4` -
`system | user | assistant | tool | other`) and cline prints token counts on
its compaction row (`CompactionRow.tsx`). Neither was here, and the gap was not
"we do not have the numbers":

- `assistant._note_model_call` already collects the provider's own
  `last_usage` per call and writes it into the conversation record;
- `local_llm.context_tokens()` already asks llama-server for the real
  `n_ctx`;
- `local_llm.fit_to_context` already drops whole turns when the request will
  not fit, and says in a note how many messages it left out.

So every number needed for a meter existed and none of it reached a person.
A user who notices Chronoa "forgetting" cannot tell a full context window from
amnesia, which is the exact confusion this module exists to remove.

**The estimate is an estimate, and says so.** Without a tokenizer (this
codebase has none, deliberately) tokens are `chars // CHARS_PER_TOKEN`, the same
divisor `local_llm.fit_to_context` uses so the meter and the thing that actually
drops messages cannot disagree about what "too big" means. When the provider
reports real usage, `real_tokens` carries it and the UI prefers that number.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from typing import Optional

logger = logging.getLogger(__name__)

#: The same divisor `local_llm` uses, resolved through it. There is no
#: constant-of-record anywhere else for this, so rather than trusting a
#: guessed 4 I read the value the thing that actually drops messages uses -
#: my first guess was wrong (I wrote 4, the shipped one is 3), which is
#: exactly the drift this had to not happen. The lazy import keeps the GUI
#: build from dragging the whole LLM stack in just for this file.
def _chars_per_token() -> float:
    from shani_chronoa import local_llm
    return local_llm.CHARS_PER_TOKEN


def estimate_tokens(text: str) -> int:
    """`chars / CHARS_PER_TOKEN`, rounded up. The house rule for "how big is
    this", read from the same constant the shipper uses."""
    if not text:
        return 0
    per = _chars_per_token()
    return max(1, int(len(text) / per) + (1 if len(text) % per else 0))


def _message_text(message: dict) -> str:
    """Every character of one message: content, tool-call arguments and names."""
    parts = [str(message.get("content") or "")]
    for call in message.get("tool_calls") or []:
        function = call.get("function") or {}
        parts.append(str(function.get("name") or ""))
        parts.append(str(function.get("arguments") or ""))
    return "\n".join(parts)


@dataclass
class Segment:
    """One slice of the prompt, and how much of the window it is eating."""

    key: str
    tokens: int = 0
    chars: int = 0

    @property
    def label(self) -> str:
        return {
            "system": "Instructions",
            "percepts": "What it perceives",
            "user": "What you said",
            "assistant": "What it said",
            "tool": "What tools returned",
            # The *schemas* sent with every request, as opposed to what the
            # tools came back with. opencode keeps these apart for the same
            # reason: "the tool list is the biggest thing in your context" is a
            # very different conversation from "a tool returned a lot".
            "tools": "Tool definitions",
            "other": "Other",
        }.get(self.key, self.key.title())


@dataclass
class Report:
    """One request's worth of context, measured.

    `limit` is the model's real window where it can be read, and `None` when it
    cannot - in which case `percent` is None too rather than a guess against a
    made-up denominator. A meter that invents "42% of what?" is worse than one
    that says "1,240 tokens".
    """

    limit: Optional[int] = None
    total_tokens: int = 0
    total_chars: int = 0
    schema_tokens: int = 0
    schema_chars: int = 0
    segments: "list[Segment]" = field(default_factory=list)
    elided_messages: int = 0
    elided_chars: int = 0
    dropped_messages: int = 0
    real_tokens: Optional[int] = None
    reply_reserve: int = 0

    @property
    def percent(self) -> "Optional[float]":
        if not self.limit:
            return None
        return round(100.0 * self.total_tokens / self.limit, 1)

    @property
    def usable_percent(self) -> "Optional[float]":
        """Percent of what is actually sendable, not of the raw window.

        opencode's `overflow.ts` reserves the output allowance before measuring
        for the same reason: a window that is 80% full of input and wants 20% of
        output left over is not 80% usable.
        """
        usable = (self.limit or 0) - self.reply_reserve
        if usable <= 0:
            return None
        return round(100.0 * self.total_tokens / usable, 1)

    @property
    def elided(self) -> bool:
        """Whether anything was elided or dropped to make this request fit."""
        return bool(self.elided_messages or self.dropped_messages)

    def headline(self) -> str:
        """The one line a rail shows: `1.2k of 8k tokens (15%)`."""
        shown = f"{self.real_tokens:,}" if self.real_tokens else f"{self.total_tokens:,}"
        if not self.limit:
            return f"{shown} tokens (window size unknown)"
        pct = self.percent if self.percent is not None else 0
        return f"{shown} of {self.limit:,} tokens ({pct:g}%)"

    def segment_rows(self) -> "list[tuple[str, int, float]]":
        """`(label, tokens, percent_of_total)`, biggest first, for a breakdown."""
        rows = [(s.label, s.tokens,
                 (100.0 * s.tokens / self.total_tokens if self.total_tokens else 0.0))
                for s in self.segments if s.tokens]
        rows.sort(key=lambda row: row[1], reverse=True)
        return rows


def _segment_key(message: dict) -> str:
    role = str(message.get("role") or "")
    content = str(message.get("content") or "")
    if role == "system":
        # The percept block is a system message that is not instructions; it is
        # what the senses are reporting right now, and it is worth its own
        # slice because it is the part that grows without the user speaking.
        return "percepts" if "percept" in content.lower()[:200] else "system"
    if role == "tool":
        return "tool"
    if role == "user":
        return "user"
    if role == "assistant":
        return "assistant"
    return "other"


def measure(messages: "list[dict]", tools: "Optional[list]" = None,
            limit: Optional[int] = None, real_tokens: Optional[int] = None,
            elision=None, reply_reserve: int = 0) -> Report:
    """Measure one request's prompt. Pure: touches nothing, reads nothing global.

    `elision` is `compression.last_elision()`, so the meter can say *why* the
    prompt is the size it is - "4 tool results were shortened to fit" is a very
    different sentence from the same token count with no explanation.
    """
    report = Report(limit=limit, real_tokens=real_tokens,
                    reply_reserve=reply_reserve)
    order: "list[Segment]" = []
    index: dict = {}
    for message in messages or []:
        text = _message_text(message)
        if not text.strip():
            continue
        key = _segment_key(message)
        segment = index.get(key)
        if segment is None:
            segment = Segment(key=key)
            index[key] = segment
            order.append(segment)
        tokens = estimate_tokens(text)
        segment.tokens += tokens
        segment.chars += len(text)
        report.total_tokens += tokens
        report.total_chars += len(text)
    report.segments = order
    if tools:
        dumped = json.dumps(tools)
        report.schema_chars = len(dumped)
        report.schema_tokens = estimate_tokens(dumped)
        # Tool schemas are sent with every request and are usually the second
        # biggest slice after the system prompt; counting them as "other" hid
        # the reason a small conversation still overflowed.
        report.segments.append(Segment(key="tools",
                                       tokens=report.schema_tokens,
                                       chars=report.schema_chars))
        report.total_tokens += report.schema_tokens
        report.total_chars += report.schema_chars
    if elision is not None:
        report.elided_messages = getattr(elision, "messages", 0)
        report.elided_chars = getattr(elision, "chars", 0)
        report.dropped_messages = getattr(elision, "dropped", 0)
    return report
