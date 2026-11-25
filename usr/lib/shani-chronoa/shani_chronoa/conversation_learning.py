"""The optional local-model half of a sleep pass.

`consolidation` never imports a model: it has to run on a laptop with no server
up. This module is the bridge a caller can hand to it. It uses only the local
llama.cpp path Chronoa already runs for every other answer - never a cloud
provider, never a new download, never a second model dependency - and it
returns nothing at all unless that server is already up.

A summary it writes is still a proposal in the sleep digest. It does not write
a memory, grant a tool, or change what reaches the model on the next turn.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Callable, Optional, Sequence

logger = logging.getLogger(__name__)

#: How much of the conversation the prompt may see. A sleep pass is not a
#: second copy of the whole transcript in the model's context.
MAX_PROMPT_CHARS = 4000
#: A bounded wait. This runs from a timer on a machine that may be busy; a
#: summary that hangs is worse than the extractive one we already have.
ENRICH_TIMEOUT_SECONDS = 8.0


def _user_text(messages: Sequence[dict], budget: int = MAX_PROMPT_CHARS) -> str:
    parts = []
    used = 0
    for message in messages:
        if not isinstance(message, dict) or message.get("role") != "user":
            continue
        content = message.get("content", message.get("text", ""))
        if not isinstance(content, str):
            continue
        piece = " ".join(content.split())
        if not piece:
            continue
        if used + len(piece) > budget:
            piece = piece[: max(0, budget - used)]
        parts.append(piece)
        used += len(piece) + 1
        if used >= budget:
            break
    return "\n".join(parts)


def local_enricher(
    config=None, timeout: float = ENRICH_TIMEOUT_SECONDS
) -> Optional[Callable[[Sequence[dict], str], str]]:
    """A callable that asks the *running* local server for a short summary.

    `None` when no local server answers, so the caller keeps its extractive
    digest. Every failure returns "" for the same reason: consolidation is a
    background pass, and one slow model call must not cost the night's work.
    `config` is accepted for symmetry with other backends but deliberately not
    used to pick a provider - a sleep-time judgement stays on this machine.
    """
    from shani_chronoa import local_llm

    if not local_llm.is_up():
        return None

    def enrich(messages: Sequence[dict], extractive: str) -> str:
        text = _user_text(messages)
        if not text:
            return ""

        async def ask() -> str:
            llm = local_llm.LocalLLM()
            reply = await asyncio.wait_for(
                llm.chat_message(
                    [
                        {
                            "role": "system",
                            "content": (
                                "Summarise what the person said in at most three short "
                                "sentences. Use only facts they stated, never your own "
                                "advice, and do not invent names or details."
                            ),
                        },
                        {"role": "user", "content": text},
                    ]
                ),
                timeout=timeout,
            )
            return reply.get("content", "") if isinstance(reply, dict) else ""

        try:
            answer = asyncio.run(ask())
        except Exception:  # noqa: BLE001 - the extractive digest is the fallback
            logger.info(
                "the local sleep summary failed; keeping the extractive one",
                exc_info=True,
            )
            return ""
        return answer.strip()

    return enrich
