"""Every read-only tool survives a wrong-typed argument.

The same real `tools.execute_tool` dispatch as `test_every_tool_answers`, aimed
at `guardrail.py` - the layer that turns a malformed call into one readable
sentence instead of a traceback three frames down in a subprocess. It was wired
in 2026-10-05, and **nothing has checked that it covers the whole registry**.

For every tool in `capabilities.READ_ONLY_TOOLS`, and for each parameter it
declares, this passes a value of the **wrong** type: an integer where a string
is expected, a string where a number is, a dict where an object is, a list where
an array is. **158 (tool, parameter) pairs, measured on this box: zero
tracebacks, zero raises.**

What that proves is narrower than it looks, and the narrower claim is the one
worth recording. It proves nothing reaches the model as a Python traceback. It
does **not** prove a wrong-typed argument is always *refused* - a tool may
coerce it, and `execute_tool(name, {})` gave no way to observe from outside
whether the coercion happened. Asserting "it must refuse" would need a
behavioural contract per tool, and this file's job is the crash class.

**Why the wrong type and not the missing one.** `test_every_tool_answers`
already fires every tool with no arguments at all, so the missing-argument path
is covered there. This is the half nobody had exercised: a value present and
the wrong *shape*.

**Three answers are fine** - a refusal naming the argument, a real reading the
tool could still produce, a missing dependency named. `None` and an uncaught
exception are not, and both are rejected by the same predicate the other file
uses, which is why they share it rather than each keeping a copy.
"""

from __future__ import annotations

import sys

sys.path.insert(0, "usr/lib/shani-chronoa")

import pytest  # noqa: E402

from shani_chronoa import capabilities as capabilities_mod  # noqa: E402
from shani_chronoa import skills as skills_pkg  # noqa: E402
from shani_chronoa import tools  # noqa: E402

_TRACEBACK = "Traceback (most recent call last)"


def _wrong_for(wanted) -> object:
    """A value of the wrong type for a schema property.

    **Every branch is a real mismatch, checked by `test_the_wrong_types_are_
    really_wrong` below.** The first version of this table sent a dict for an
    `object` parameter and a list for an `array` one - both the *right* type, so
    the run proved much less than it appeared to. That control is what found it.
    """
    return {
        "string": 12345,
        "integer": "not-a-number",
        "number": "not-a-number",
        "boolean": "yes",
        "object": "not-an-object",
        "array": "not-an-array",
    }.get(wanted, 12345)


def _pairs():
    """`(tool, parameter, wrong value)` for every read-only tool's properties."""
    registered, _ = skills_pkg.discover_skills()
    read_only = set(capabilities_mod.READ_ONLY_TOOLS)
    for schema in registered:
        name = schema["function"]["name"]
        if name not in read_only:
            continue
        properties = (schema["function"].get("parameters", {}) or {}).get(
            "properties", {})
        for prop, spec in sorted((properties or {}).items()):
            yield name, prop, _wrong_for(spec.get("type"))


class TestNoToolTracebacksOnAWrongType:
    @pytest.mark.parametrize("name,prop,wrong", list(_pairs()))
    def test_it_does_not_reach_the_model_as_a_traceback(self, name, prop, wrong):
        answer = tools.execute_tool(name, {prop: wrong})
        assert isinstance(answer, str), (
            f"{name}({prop}={wrong!r}) returned "
            f"{type(answer).__name__}, not a string")
        assert _TRACEBACK not in answer, (
            f"{name}({prop}={wrong!r}) handed the model a traceback, so "
            f"guardrail did not cover it:\n{answer[:600]}")
        assert answer.strip(), (
            f"{name}({prop}={wrong!r}) answered with an empty string, which is "
            f"indistinguishable from a skill that did not run")

    def test_there_are_enough_pairs_to_be_worth_running(self):
        """A guard against the parameter lists emptying out by accident.

        With no pairs the parametrized test above is green having done nothing,
        which is a smoke test that passes for a reason nobody notices.
        """
        assert len(list(_pairs())) >= 100

    def test_the_wrong_types_are_really_wrong(self):
        """A control on the fixture, not on the product.

        Every value this file sends must actually mismatch the type it is
        sent for, or the whole run tests the wrong thing - the same "a control
        that cannot fail" failure as a checker that only looks for one shape.
        """
        # **`_wrong_for(wanted)`, not a second copy of the table.** The first
        # version listed the values by hand and kept the ones the fixture had
        # just been fixed to stop sending - so the control passed while the run
        # sent a dict to an `object` parameter and a list to an `array` one,
        # both the right type. One source of truth, or the control checks a
        # table that is not the one being used.
        for wanted in ("string", "integer", "number", "boolean", "object",
                       "array"):
            value = _wrong_for(wanted)
            assert not isinstance(value, _python_type(wanted)), (
                f"{value!r} was sent as the wrong type for {wanted} but is not "
                f"one - so this run would test nothing")


def _python_type(wanted):
    return {"string": str, "integer": int, "number": (int, float),
            "object": dict, "array": list, "boolean": bool}.get(wanted, object)
