"""A check between the model deciding and the tool running.

Everything else in the dispatch path is a *permission* decision: is this tool
allowed, is this resource permitted, is the consent key open. Those are all
questions about authority, and all of them are answered correctly by a policy
lookup.

This module answers a different question - is the call even well-formed - and it
answers it the same way every time, with no model involved.

## Why it cannot be a prompt

The obvious way to stop a model doing something malformed is to tell it not to
in the system prompt. That is a promise the model may or may not keep, and when
it breaks, nothing observable has failed: the tool runs with `dry_run` silently
missing, or `path` arrives as a list, and the skill does something adjacent to
what was asked. The same reasoning the plan-mode block already states - a prompt
is a promise, a fact is the only reason the check is worth having.

So this is deliberately dumb and deliberately deterministic. No model call, no
heuristic scoring, no confidence threshold. Either the arguments match the
declared schema or they do not.

## What it does *not* do

It does not judge whether the call is *wise*. A well-formed request to delete
the wrong file passes here and is caught - or not - by the consent layer, which
is the layer that owns that question. Mixing the two would produce a guardrail
that blocks on opinions, and an opinions-based guardrail gets disabled the first
time it is inconvenient.
"""

from __future__ import annotations

import json
import logging
from typing import Optional

logger = logging.getLogger(__name__)

#: JSON Schema primitive -> the Python types we will accept for it. A model
#: returning `1` where a string is declared is a real failure mode, and letting
#: it reach a subprocess turns it into a traceback rather than a message.
_TYPE_MAP: dict[str, tuple[type, ...]] = {
    "string": (str,),
    "number": (int, float),
    "integer": (int,),
    "boolean": (bool,),
    "array": (list,),
    "object": (dict,),
    "null": (type(None),),
}


class GuardrailResult:
    """A five-way verdict on a tool call, mirroring pydantic-ai's shape.

    The five verdicts cover every decision the dispatch path needs to make
    about a single tool call:

    - `valid` — the call is well-formed; run it.
    - `invalid` — the call is malformed; do not run, report the reason.
    - `retry` — the call is well-formed but should be retried with a template
      message (e.g. a JSON-arg parse error: "call it again with valid JSON").
    - `reask` — the model should be asked again; the reason is a question for
      the model, not a human (unlike HITL `ask`, which goes to the user).
    - `stop` — the call must never run; a hard stop with a reason.

    This unifies what the consent gates, sandbox verdicts, and schema checks
    each answer separately, so the dispatch layer sees one consistent shape
    instead of three different return types.
    """

    VALID = "valid"
    INVALID = "invalid"
    RETRY = "retry"
    REASK = "reask"
    STOP = "stop"

    def __init__(self, verdict: str, message: str = "", template: str = "") -> None:
        if verdict not in (self.VALID, self.INVALID, self.RETRY, self.REASK, self.STOP):
            raise ValueError(
                f"unknown GuardrailResult verdict {verdict!r}; expected one of "
                f"valid, invalid, retry, reask, stop"
            )
        self.verdict = verdict
        self.message = message
        self.template = template

    @property
    def should_run(self) -> bool:
        """Whether the call should proceed."""
        return self.verdict == self.VALID

    @property
    def should_retry(self) -> bool:
        """Whether the call should be retried with a template message."""
        return self.verdict == self.RETRY

    @property
    def should_reask(self) -> bool:
        """Whether the model should be asked again (model-initiated re-ask)."""
        return self.verdict == self.REASK

    @property
    def should_stop(self) -> bool:
        """Whether the call must never run (hard stop)."""
        return self.verdict == self.STOP


def _type_of(schema: dict) -> "tuple[type, ...] | None":
    """The Python types acceptable for a JSON Schema node, or None if unknown."""
    declared = schema.get("type")
    if declared is None:
        return None
    # A union such as ["string", "null"] is legitimate and appears in real
    # schemas; anything unrecognised is left unconstrained rather than guessed.
    if isinstance(declared, list):
        accepted: list[type] = []
        for name in declared:
            accepted.extend(_TYPE_MAP.get(name, ()))
        return tuple(accepted) or None
    return _TYPE_MAP.get(declared)


def _describe(path: str) -> str:
    return path or "the arguments"


def as_argument_object(raw) -> "tuple[Optional[dict], str]":
    """A tool call's `arguments` as a dict, or `("" , why not)`.

    **The question that comes before every other one here.** `check()` assumes
    it is handed a dict; this is the function that makes that assumption true
    rather than true-by-luck, and it is the one place the two spellings of
    `arguments` stop being two problems.

    Two shapes arrive, both legitimate: the OpenAI wire form, where
    `arguments` is a **JSON string**, and several clients' already-decoded
    **dict**. Both normalise to a dict.

    **Measured on the cloud path before this existed**, through the real
    `AnthropicLLM._history_to_anthropic`, with `test_server.py`'s exact
    parametrisation from edge0:

        arguments in        what reached the tool
        '{"expression":"2+2"}'  {'expression': '2 + 2'}   correct
        {'expression': '2 + 2'} {'expression': '2 + 2'}   correct
        {broken                  {}                       <- the defect
        []                       []
        null                     None
        "text"                   'text'
        42                       42
        None                     None
        ""                       {}

    Six shapes ran a tool with something that is not an argument object, and a
    malformed one **silently became `{}`** - the call ran with every default,
    and nothing anywhere said the model's arguments had been dropped. `[]`,
    `42` and `None` also reach the Anthropic API as a non-object `input`, which
    is refused at the far end with a message about a field rather than about the
    model's reply.

    So: normalise, and **refuse anything that is not an object**. `""` is the
    one honest `{}` - a model that lists no arguments means no arguments. Note
    that edge0's test asserts the caller's payload is *not* mutated, which is
    the property that keeps this usable inside a history rebuild.
    """
    if isinstance(raw, dict):
        return raw, ""
    if isinstance(raw, str) and not raw.strip():
        return {}, ""
    if not isinstance(raw, str):
        return None, (f"the arguments were {type(raw).__name__}, not a JSON "
                      f"object; nothing ran")
    try:
        decoded = json.loads(raw)
    except ValueError as exc:
        return None, (f"the arguments were not valid JSON ({exc}); nothing ran")
    if not isinstance(decoded, dict):
        return None, (f"the arguments were {json.dumps(decoded)[:48]}, not a "
                      f"JSON object; nothing ran")
    return decoded, ""


def check(name: str, arguments: dict, schema: dict) -> GuardrailResult:
    """Verdict on whether this call is well-formed enough to run.

    Returns a `GuardrailResult`. **Three of the five declared verdicts are
    reachable today; two are not yet produced:**

    | verdict | produced by `check()`? | what it means |
    |---|---|---|
    | `valid` | yes | the call is well-formed; run it |
    | `invalid` | yes | the call is malformed; do not run, report the reason |
    | `retry` | yes, once | well-formed, but should be retried with `template` (a string argument that starts `ERROR:`) |
    | `reask` | **no** | the model should be asked again; the reason is a question for the model, not a human |
    | `stop` | **no** | the call must never run; a hard stop with a reason |

    **This list used to claim all five were reachable**, which sent the wrong way
    twice: `should_reask` and `should_stop` were read as two verdicts the dispatch
    layer acted on, when no code path constructs either verdict and nothing reads
    those two properties. `should_retry` is reachable but redundant - the RETRY
    payload is its `template`, and `tools.py` uses `reason.template or
    reason.message` directly and deliberately, with the reason written down there.

    A returned `GuardrailResult` is the decision the dispatch layer acts on -
    naming the argument, what was wrong, and what was expected. A traceback
    three frames down in a subprocess tells the model nothing it can use.
    """
    if not isinstance(schema, dict) or not schema:
        return GuardrailResult(GuardrailResult.VALID)

    properties = schema.get("properties") or {}
    if not isinstance(properties, dict):
        return GuardrailResult(GuardrailResult.VALID)

    # A missing required argument is the common case, and the one most likely to
    # be a hallucinated call rather than a real request.
    for required in schema.get("required") or []:
        if required not in arguments:
            known = ", ".join(sorted(properties)) or "none declared"
            return GuardrailResult(
                GuardrailResult.INVALID,
                message=(
                    f"{name} was called without the required argument "
                    f"'{required}'. It accepts: {known}."
                ),
            )

    # Types, checked per property. Nested objects and arrays are not descended
    # into: a schema that is wrong at depth is a bug in the skill, and
    # guessing at it here would reject calls the skill itself handles.
    for key, value in arguments.items():
        declared = properties.get(key)
        if declared is None:
            # Only a schema that explicitly forbids extras is enforced. The
            # default in JSON Schema is to allow them, and a skill that ignores
            # an extra field is not thereby unsafe.
            if schema.get("additionalProperties") is False:
                known = ", ".join(sorted(properties)) or "none"
                return GuardrailResult(
                    GuardrailResult.INVALID,
                    message=(
                        f"{name} was called with '{key}', which it does not "
                        f"accept. It accepts: {known}."
                    ),
                )
            continue
        wanted = declared.get("type") if isinstance(declared, dict) else None
        # Checked *before* the isinstance test, not after. `bool` is a subclass
        # of `int`, so a declared "integer" accepts True and the general check
        # below waves it through - the first version of this had the bool
        # branch after the `continue`, where it could never run.
        if wanted == "integer" and isinstance(value, bool):
            return GuardrailResult(
                GuardrailResult.INVALID,
                message=f"{name} was called with a boolean for '{key}', which wants a number.",
            )
        expected = _type_of(declared)
        if expected is not None and not isinstance(value, expected):
            return GuardrailResult(
                GuardrailResult.INVALID,
                message=(
                    f"{name} was called with {_describe(key)} of the wrong type "
                    f"({type(value).__name__}); it wants {wanted}."
                ),
            )
        # **An enum is the only schema constraint this module was not checking.**
        # Measured over the registry: 682 property nodes across 256 tools, of
        # which 90 declare an enum in 73 tools and **zero** declare a numeric or
        # string bound - so this was the whole remaining gap.
        #
        # And it was a gap in *placement*, not in the check. `airplane_mode`
        # already refuses an out-of-enum action itself:
        #
        #     action = (arguments.get("action") or "status").strip().lower()
        #     if action not in ("status", "on", "off"): ...
        #
        # which is the right rule - in the child, after dispatch, after the
        # sandbox and after the audit origin, so the refusal reads as a tool that
        # ran. The same rule one layer earlier is what this is for.
        #
        # **Matched with the tolerance the skills use**, because a gate stricter
        # than the skills refuses calls that work today: `airplane_mode` strips
        # and lowercases, so `action="LIST"` is a legal call and is still one.
        legal = declared.get("enum") if isinstance(declared, dict) else None
        if legal and isinstance(value, str):
            said = value.strip().lower()
            if not any(said == str(item).strip().lower() for item in legal):
                return GuardrailResult(
                    GuardrailResult.INVALID,
                    message=(
                        f"{name} was called with {_describe(key)} of {value!r}; "
                        f"it accepts: {', '.join(str(item) for item in legal)}."
                    ),
                )

    # Retry: a well-formed call that the model should retry with a template.
    # This is the "call it again with valid JSON" shape - the arguments
    # parsed fine but the model should be asked to produce a cleaner call.
    # Triggered when a string argument looks like a JSON parse error message.
    for key, value in arguments.items():
        if isinstance(value, str) and value.startswith("ERROR:"):
            return GuardrailResult(
                GuardrailResult.RETRY,
                message=value,
                template=f"Retry {name}: the previous call failed with a parsing error. "
                f"Please provide valid arguments for {name}.",
            )

    return GuardrailResult(GuardrailResult.VALID)
