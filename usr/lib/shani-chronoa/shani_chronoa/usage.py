"""What a model call actually cost, counted rather than guessed.

Chronoa talks to three kinds of backend and all three already report token
counts. Every one of those counts was being thrown away: `chat_message` returns
only the message, so Ollama's `prompt_eval_count`/`eval_count` and the cloud
providers' `usage` block never reached anything. The commit that added the turn
wall-clock budget said so explicitly - the counts were "the obvious next step and
is not done here".

opencode does this properly and is worth copying the shape of. Its cost comes
from a published price table (models.dev) converted to a per-token rate, then
multiplied by the counts the provider actually reported. The important part is
what it does when the price is unknown: nothing. No number is invented.

## Why "unknown" must not become zero

A cost of $0.00 is a claim, and for a local model it is false — inference costs
electricity, memory and the user's time. For an unpriced cloud model it is
falsely reassuring. So an unpriced call reports its tokens and says the price
is unknown, which is the difference between "this was free" and "I do not know
what this cost". A caller that wants a number can then decide whether to
display nothing, or to look the price up itself.

This is the same rule the senses already follow. "I could not determine this" is
a state the code can represent, and it is never rounded up to a clean yes - or,
here, down to a clean zero.

## It is not in the message dict

Usage is exposed as `last_usage` on the backend rather than folded into the
returned message. A message is appended to `_history` and written to the
transcript, and then sent to the model on every subsequent turn - so a `usage`
key there would be persisted forever and re-sent as context, teaching the model
about a number it has no use for. Keeping it beside the message avoids that
entirely.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional


@dataclass(frozen=True)
class Usage:
    """Token counts for one model call, and a cost only where one is known."""

    input_tokens: int = 0
    output_tokens: int = 0
    #: Dollars, or None when no price is known. Never 0 as a stand-in for
    #: "unknown" - see the module docstring.
    cost_usd: Optional[float] = None
    #: Which backend reported it, so a reader can tell an absent count from a
    #: backend that simply does not report one.
    source: str = ""

    @property
    def total_tokens(self) -> int:
        return self.input_tokens + self.output_tokens

    @property
    def priced(self) -> bool:
        return self.cost_usd is not None

    def as_dict(self) -> dict:
        out = {
            "input_tokens": self.input_tokens,
            "output_tokens": self.output_tokens,
            "total_tokens": self.total_tokens,
            "priced": self.priced,
            "source": self.source,
        }
        if self.cost_usd is not None:
            out["cost_usd"] = round(self.cost_usd, 6)
        return out

    def __add__(self, other: "Usage") -> "Usage":
        """Add two calls. A sum is priced only if both parts were.

        Summing a priced and an unpriced call and reporting the priced part
        alone would understate the cost, which is the same error as reporting
        zero for an unpriced call - just smaller, and therefore easier to miss.
        """
        both = self.priced and other.priced
        return Usage(
            input_tokens=self.input_tokens + other.input_tokens,
            output_tokens=self.output_tokens + other.output_tokens,
            cost_usd=(self.cost_usd + other.cost_usd) if both else None,
            source=self.source or other.source,
        )


def from_ollama(data: dict, model: str = "") -> Usage:
    """Ollama's counts, which live beside the message rather than inside it."""
    return Usage(
        input_tokens=int(data.get("prompt_eval_count") or 0),
        output_tokens=int(data.get("eval_count") or 0),
        cost_usd=None,  # local inference: no published per-token price
        source="ollama",
    )


def from_openai(data: dict) -> Usage:
    """The OpenAI-compatible `usage` block, which every cloud provider sends."""
    block = data.get("usage") or {}
    return Usage(
        input_tokens=int(block.get("prompt_tokens") or 0),
        output_tokens=int(block.get("completion_tokens") or 0),
        source="openai-compatible",
    )


def from_anthropic(data: dict) -> Usage:
    """Anthropic's Messages API names the same two counts differently."""
    block = data.get("usage") or {}
    return Usage(
        input_tokens=int(block.get("input_tokens") or 0),
        output_tokens=int(block.get("output_tokens") or 0),
        source="anthropic",
    )


def price(usage: Usage, usd_per_million: Optional[dict]) -> Usage:
    """Attach a cost from a published price table, if one covers this model.

    `usd_per_million` is `{"input": float, "output": float}` in dollars per
    million tokens. None - or a table missing either rate - leaves the usage
    unpriced rather than assuming a rate. Callers that hold no table get tokens
    and time, which is the honest floor.
    """
    if not usd_per_million:
        return usage
    try:
        rate_in = float(usd_per_million["input"])
        rate_out = float(usd_per_million["output"])
    except (KeyError, TypeError, ValueError):
        return usage
    return Usage(
        input_tokens=usage.input_tokens,
        output_tokens=usage.output_tokens,
        cost_usd=(usage.input_tokens * rate_in + usage.output_tokens * rate_out)
        / 1_000_000,
        source=usage.source,
    )
