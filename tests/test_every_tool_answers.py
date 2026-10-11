"""Every read-only tool answers. None of them traceback.

Fired through the **real** `tools.execute_tool`, one call per tool with no
arguments, for all 88 tools in `capabilities.READ_ONLY_TOOLS`. **Only the
read-only half**: mutating a machine to find out whether it answers is not a
check anyone should run, and this file's whole value is that it is safe to run
on a real machine.

What it catches is the shape this repo keeps recording: a skill that is green in
its own unit tests - which call `_run()` directly - and that **raises on the
trivial call**, because the unit test supplied arguments the dispatch path never
gets, or because it ran under a machine state the live box does not have. The
`midi`, `credential`, and `argfile` defects were all invisible to a suite that
called the function and visible to anything that ran it.

**Three answers count as answering, and all three are legitimate:**

- a real answer (`system_info` describing this machine);
- a refusal naming the switch that is off - `temperatures` with `hwmon` off is
  the correct output, and a test that demands otherwise would fail on every
  default install;
- a missing dependency named - `snapshot_status` has no `shani-deploy` here.

What does **not** count: a Python traceback, an empty string, or a message that
is only whitespace. A traceback reaches the model as tool output, which is the
one thing a skill must never send.

`TestTheCheckItself` drives the detector **both ways** - it rejects a real
traceback, an empty string, whitespace, `None` and a dict, and it accepts a real
reading and a consent refusal - because a smoke test whose detector cannot fail
is the "green looks like coverage" failure this whole file is for. Its
`_a_real_traceback()` raises the exception rather than formatting an un-raised
one: `format_exception(type(e), e, e.__traceback__)` on an exception that was
never raised yields only the message line, so the first version of that control
passed against a detector that could not see a traceback at all.

**Measured cost: 46.5 s for all 88**, dominated by `find_recently_modified` at
21.6 s, which walks the real home directory because that is what it does.
"""

from __future__ import annotations

import sys

sys.path.insert(0, "usr/lib/shani-chronoa")

import pytest  # noqa: E402

from shani_chronoa import capabilities as capabilities_mod  # noqa: E402
from shani_chronoa import skills as skills_pkg  # noqa: E402
from shani_chronoa import tools  # noqa: E402

_TRACEBACK = "Traceback (most recent call last)"


def _read_only_tools():
    """**The handler names, not the schemas.** `discover_skills()` returns a
    list of `{"type": "function", "function": {...}}` dicts beside the handler
    map, so `set(tools)` is a set of dicts and raises
    `TypeError: cannot use 'dict' as a set element` - an error that reads as a
    broken registry and is a wrong variable.
    """
    registered, _ = skills_pkg.discover_skills()
    names = {tool["function"]["name"] for tool in registered}
    declared = set(capabilities_mod.READ_ONLY_TOOLS)
    return sorted(declared & names)


def _a_real_traceback() -> str:
    """What Python actually prints when a handler blows up.

    **The exception has to be raised.** `traceback.format_exception(type(e), e,
    e.__traceback__)` on an exception that was never raised yields only the
    final message line, because `__traceback__` is `None` - so the header line
    this file looks for is absent, and the first version of this control passed
    against a detector that could not see a real traceback at all.
    """
    import traceback
    try:
        raise RuntimeError("a handler that blows up")
    except RuntimeError:
        return traceback.format_exc()


#: What a handler that blew up actually reaches the model as: the dispatch path
#: turns an exception into text, and that text is tool output.
_RAISED = _a_real_traceback()


def _is_an_answer(text) -> "tuple[bool, str]":
    """`(is this a usable answer, why not)`.

    **Extracted so the guard can be controlled both ways.** A smoke test whose
    detector is an inline `assert` can only be exercised by making a real skill
    misbehave - which nobody does - so "this test can fail" is a claim rather
    than a fact. `TestTheCheckItself` drives this function directly against a
    traceback, an empty string and a real answer.
    """
    if not isinstance(text, str):
        return False, f"returned {type(text).__name__}, not a string"
    if _TRACEBACK in text:
        return (False,
                "handed the model a Python traceback, which is the one thing "
                f"a skill must never send:\n{text[:800]}")
    if not text.strip():
        return (False,
                "answered with an empty string. An empty answer is "
                "indistinguishable from a skill that did not run.")
    return True, ""


class TestEveryReadOnlyToolAnswers:
    @pytest.mark.parametrize("name", _read_only_tools())
    def test_it_answers_without_tracing_back(self, name):
        usable, why_not = _is_an_answer(tools.execute_tool(name, {}))
        assert usable, f"{name}: {why_not}"

    def test_the_registry_agrees_with_itself(self):
        """A tool listed as read-only that does not exist would be silently
        skipped by the parametrize above, which is a check that passes for a
        reason nobody notices.
        """
        _, handlers = skills_pkg.discover_skills()
        declared = set(capabilities_mod.READ_ONLY_TOOLS)
        assert declared <= set(handlers), (
            f"READ_ONLY_TOOLS names {len(declared - set(handlers))} tool(s) "
            f"that are not registered: {sorted(declared - set(handlers))}")

    def test_there_are_enough_to_be_worth_running(self):
        """A guard against the list shrinking to nothing by accident - a smoke
        test over three tools is not a smoke test.
        """
        assert len(_read_only_tools()) >= 50


class TestTheCheckItself:
    """The detector must fail on a handler that raises, and must pass a refusal.

    Both directions, because a smoke test whose detector cannot fail is the same
    failure as a skill that never runs - which is the thing this file exists to
    catch, so it gets the treatment the skills get.
    """

    def test_a_handler_that_raises_is_not_an_answer(self):
        """The predicate rejects what a crashed handler produces."""
        useful, why = _is_an_answer(_RAISED)
        assert not useful
        assert "Traceback" in why

    def test_every_rejected_shape_is_rejected_for_its_own_reason(self):
        """Every value the predicate must reject, each with a reason.

        A detector that only knows about one failure mode passes a skill that
        answers `""` - and an empty string is indistinguishable from a skill
        that did not run at all.
        """
        for bad, described in (
                (_RAISED, "a traceback"),
                ("", "an empty string"),
                ("   \n\t ", "whitespace only"),
                (None, "None"),
                ({}, "a dict")):
            useful, why = _is_an_answer(bad)
            assert not useful, f"{described} was accepted as an answer"
            assert why.strip(), f"{described} was rejected with no reason"

    def test_a_real_answer_passes(self):
        assert _is_an_answer("Sunday, October 11, 2026") == (True, "")

    def test_a_refusal_is_a_real_answer(self):
        """Consent-off is the correct output on a default install.

        Demanding a *reading* would fail this file on every machine, which is
        the over-fix that turns a smoke test into a false alarm. So this drives
        the real tool rather than a hand-typed sentence: a fixture would drift
        from the tool within one edit - the first version of this fixture quoted
        a sentence this file had already changed, and it was believed anyway.
        """
        useful, why = _is_an_answer(tools.execute_tool("temperatures", {}))
        assert useful, why

    def test_the_traceback_marker_is_the_one_python_prints(self):
        """The string the detector looks for is the one Python emits, or every
        crash passes.
        """
        assert _TRACEBACK in _a_real_traceback()
