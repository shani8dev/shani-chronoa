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


def check(name: str, arguments: dict, schema: dict) -> "Optional[str]":
    """Why this call should not run, or None if it is well-formed.

    A returned string is a reason to halt, written so the model can act on it -
    naming the argument, what was wrong, and what was expected. A traceback
    three frames down in a subprocess tells the model nothing it can use.
    """
    if not isinstance(schema, dict) or not schema:
        return None

    properties = schema.get("properties") or {}
    if not isinstance(properties, dict):
        return None

    # A missing required argument is the common case, and the one most likely to
    # be a hallucinated call rather than a real request.
    for required in schema.get("required") or []:
        if required not in arguments:
            known = ", ".join(sorted(properties)) or "none declared"
            return (
                f"{name} was called without the required argument "
                f"'{required}'. It accepts: {known}."
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
                return (
                    f"{name} was called with '{key}', which it does not "
                    f"accept. It accepts: {known}."
                )
            continue
        wanted = declared.get("type") if isinstance(declared, dict) else None
        # Checked *before* the isinstance test, not after. `bool` is a subclass
        # of `int`, so a declared "integer" accepts True and the general check
        # below waves it through - the first version of this had the bool
        # branch after the `continue`, where it could never run.
        if wanted == "integer" and isinstance(value, bool):
            return f"{name} was called with a boolean for '{key}', which wants a number."
        expected = _type_of(declared)
        if expected is None or isinstance(value, expected):
            continue
        return (
            f"{name} was called with {_describe(key)} of the wrong type "
            f"({type(value).__name__}); it wants {wanted}."
        )

    return None
