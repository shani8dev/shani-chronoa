"""A cheap self-check before a risky action, and an explicit not-sure path.

This is the "judge" box in ``ARCHITECTURE-TARGET.md``. It is deliberately not a
second opinion on *every* call: a small model on a CPU cannot afford one, and a
judge asked about reading the time spends tokens answering a question nobody
had. Two rules keep it cheap and keep it honest:

- it only looks at actions whose consent key is in
  `capabilities.DESTRUCTIVE_CONSENT_KEYS`, derived from the table the gates
  already use rather than from a second hand-kept list of "dangerous" tools;
- the deterministic check runs first and the local model is consulted only if
  a person has asked for more thought through the existing ``thinking``
  setting. No new setting, no new model, no cloud route: a cloud backend is
  never asked to judge, because that would turn an opt-in self-check into an
  egress event.

A failed check does **not** override a consent gate, an approval, or the
sandbox. It skips the call and hands the model a tool result saying plainly
that the assistant is not sure, so the user hears that rather than seeing a
half-explained refusal. The gates still run for every call this module clears.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from typing import Optional

logger = logging.getLogger(__name__)

#: The one sentence a person hears when the check fails. Kept here rather than
#: in assistant.py so the judgement and the words that report it cannot drift.
UNSURE_SENTENCE = "I'm not sure about that one."

#: The existing setting that opts a person into the model pass. Off by default,
#: so a machine that never asked for more thinking pays nothing for this.
JUDGE_FLAG = "thinking"

#: How long a judge call may take. A self-check that costs more than the action
#: it guards is not cheap any more; a timeout is an answer of "not sure".
JUDGE_TIMEOUT_SECONDS = 6.0


@dataclass
class Check:
    """One answer from the self-check.

    `confident` is the whole decision. `reason` is for the log and the model's
    tool result, not for a caller to re-interpret; `source` says which stage
    produced the answer so a test can tell a heuristic from a model judgement.
    """

    confident: bool
    reason: str
    source: str = "heuristic"

    def __bool__(self) -> bool:
        return self.confident


def judge_enabled(config=None) -> bool:
    """Whether the person opted into the model half of the check.

    Reads ``thinking`` through `ChronoaConfig.get`, which is the flag that
    already asks "should this model work harder?" on every backend. Anything
    unreadable or unknown is off: a stub config and an old install both keep
    the cheap path rather than growing an accidental model call.
    """
    if config is None:
        return False
    try:
        value = config.get(JUDGE_FLAG, "off")
    except Exception:  # noqa: BLE001 - an unreadable setting is not an opt-in
        return False
    return isinstance(value, str) and value.strip().lower() not in ("", "off")


def risky_tools() -> frozenset[str]:
    """Tools whose consent key is one that cannot be undone."""
    from shani_chronoa import capabilities

    return frozenset(
        name
        for name, key in capabilities.GATED.items()
        if key in capabilities.DESTRUCTIVE_CONSENT_KEYS
    )


def is_risky(name: str) -> bool:
    return isinstance(name, str) and name in risky_tools()


def cheap_check(name: str, arguments, schema: Optional[dict] = None) -> Check:
    """A deterministic sanity check: does the action name its target?

    A destructive call with no target, or with a blank one, is the one shape
    that is cheap to catch without a model and expensive to get wrong. The
    required fields come from the skill's own schema, so adding a skill cannot
    silently opt it out of the check - and there is no second list to keep in
    step. A tool that is not destructive is left alone.
    """
    if not is_risky(name):
        return Check(True, "not an irreversible action")
    if schema is None:
        try:
            from shani_chronoa import tools

            schema = next(
                (t for t in tools.TOOLS if t.get("function", {}).get("name") == name),
                None,
            )
        except Exception:  # noqa: BLE001 - no registry means no required fields
            schema = None
    required = []
    if isinstance(schema, dict):
        params = schema.get("function", {}).get("parameters", {})
        if isinstance(params, dict) and isinstance(params.get("required"), list):
            required = [key for key in params["required"] if isinstance(key, str)]
    if not isinstance(arguments, dict):
        return Check(False, "the call has no arguments to check")
    missing = [
        key
        for key in required
        if key not in arguments or arguments[key] in ("", None, [], {})
    ]
    if missing:
        return Check(False, "no target was given for " + ", ".join(missing))
    if not required and not arguments:
        # Several skills (delete_file among them) declare no `required` list at
        # all, so an empty argument object would sail through the check above.
        # An irreversible action with nothing saying what to act on is exactly
        # the shape the self-check exists to catch.
        return Check(False, "the action has no target at all")
    return Check(True, "the action names its target")


def judge_messages(name: str, arguments) -> list[dict]:
    """A tiny prompt: one decision, a closed vocabulary, no conversation.

    The arguments are shown to the local model already sanitised by its
    `chat_message`, not by this module - sanitising twice would be two
    implementations of one rule.
    """
    return [
        {
            "role": "system",
            "content": (
                "You check one proposed action. Reply with exactly one word: SURE "
                "if the action is clear and should go ahead, UNSURE if it is vague, "
                "ambiguous, or you cannot tell. Never explain."
            ),
        },
        {"role": "user", "content": f"Action: {name}\nArguments: {arguments!r}"},
    ]


def parse_judge(text) -> Optional[bool]:
    """SURE -> True, UNSURE -> False, anything else -> None (no verdict).

    "unsure" is tested first: it contains "sure", so the other order would
    turn every refusal into an approval - the dangerous direction to be wrong.
    """
    if not isinstance(text, str):
        return None
    low = text.strip().lower()
    if "unsure" in low or "not sure" in low:
        return False
    if low == "sure" or low.startswith("sure "):
        return True
    return None


async def judge(
    name: str,
    arguments,
    llm=None,
    config=None,
    timeout: float = JUDGE_TIMEOUT_SECONDS,
    schema: Optional[dict] = None,
) -> Check:
    """The whole pass: deterministic check, then an optional local opinion.

    When the check is off, this is a no-op that reports `confident=True` - a
    person who never opted in must see no latency and no behaviour change. A
    judge that times out, raises, or answers outside its vocabulary yields
    `confident=False`, which is the explicit "I'm not sure" path; guessing a
    verdict from a broken check would be the failure this box exists to avoid.
    """
    if not judge_enabled(config):
        return Check(True, "the self-check is off", "off")
    base = cheap_check(name, arguments, schema)
    if not base.confident or not is_risky(name):
        return base
    if llm is None or not getattr(llm, "local", False):
        # No local model (or a cloud chain): the cheap answer stands, and no
        # request leaves the machine on the strength of a self-check.
        return Check(True, "no local model to ask; the cheap check stands", "heuristic")
    try:
        reply = await asyncio.wait_for(
            llm.chat_message(judge_messages(name, arguments)), timeout=timeout
        )
    except (Exception, asyncio.CancelledError) as exc:  # noqa: BLE001
        # CancelledError is a BaseException in modern Python. A cancelled turn
        # must not be turned into an approval by the judge falling through.
        logger.info("the self-check could not answer for %s: %s", name, exc)
        return Check(False, "the self-check could not answer", "judge-error")
    verdict = parse_judge(reply.get("content", "")) if isinstance(reply, dict) else None
    if verdict is None:
        return Check(False, "the self-check gave no usable answer", "judge")
    if verdict:
        return Check(True, "the local self-check was sure", "judge")
    return Check(False, "the local self-check was not sure", "judge")
