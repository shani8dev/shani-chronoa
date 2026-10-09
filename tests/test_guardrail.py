"""A check that runs between the model deciding and the tool running.

Everything else in the dispatch path answers a question about *authority*: is
this tool allowed, is this resource permitted, is the consent key open. Those
are all policy lookups and all of them are right.

This is the other question - is the call even well-formed - answered the same
way every time with no model involved. A prompt would be a promise the model may
or may not keep, and when it breaks nothing observable has failed: the tool runs
with `dry_run` silently missing and does something adjacent to what was asked.
The plan-mode block already states the reasoning; this is the same one applied
to shape rather than permission.

A second half of this was written and removed: a tripwire that stopped a tool
being called identically three times running. It seemed well-motivated - a model
that has misunderstood a tool does not try something else, it retries - and
measuring it showed it was not worth having. `MAX_TOOL_ROUNDS = 4` already bounds
the assistant's own loop, so inside a turn it saved nothing at all. Its only
remaining scope was the MCP host and the trigger rules, which is exactly where
repeating a call is legitimate work, and there it refused a request for five
screenshots in a row by failing the fourth. It also broke thirteen unrelated
tests before that was noticed. The tests below record the absence on purpose.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

_REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_REPO / "usr" / "lib" / "shani-chronoa"))

from shani_chronoa import guardrail  # noqa: E402
from shani_chronoa.tools import execute_tool_outcome  # noqa: E402

SCHEMA = {
    "type": "object",
    "properties": {
        "path": {"type": "string"},
        "recursive": {"type": "boolean"},
        "count": {"type": "integer"},
        "mode": {"type": ["string", "null"]},
    },
    "required": ["path"],
}


class TestMalformedCallsAreCaught:
    def test_a_wrong_type_is_rejected(self):
        reason = guardrail.check("read_text_file", {"path": 12345}, SCHEMA)
        assert not reason.should_run
        assert "path" in reason.message

    def test_the_reason_names_the_argument_and_the_expectation(self):
        """A traceback in a subprocess tells the model nothing it can use."""
        reason = guardrail.check("read_text_file", {"path": 12345}, SCHEMA)
        assert "wants string" in reason.message
        assert "int" in reason.message

    def test_a_missing_required_argument_is_rejected(self):
        reason = guardrail.check("read_text_file", {}, SCHEMA)
        assert not reason.should_run
        assert "required" in reason.message
        assert "path" in reason.message

    def test_a_boolean_is_not_an_integer(self):
        """bool subclasses int, so a declared "integer" would accept True."""
        reason = guardrail.check("x", {"path": "/a", "count": True}, SCHEMA)
        assert not reason.should_run
        assert "boolean" in reason.message

    def test_a_union_type_allows_either(self):
        """`["string", "null"]` is legitimate and appears in real schemas."""
        reason = guardrail.check("x", {"path": "/a", "mode": None}, SCHEMA)
        assert reason.should_run
        reason2 = guardrail.check("x", {"path": "/a", "mode": "fast"}, SCHEMA)
        assert reason2.should_run


class TestWhatIsNotRejected:
    def test_a_well_formed_call_passes(self):
        reason = guardrail.check("read_text_file",
                                 {"path": "/tmp/a", "recursive": True}, SCHEMA)
        assert reason.should_run

    def test_an_undeclared_argument_is_allowed_by_default(self):
        """JSON Schema allows extras unless told otherwise.

        A skill that ignores a field it did not declare is not thereby unsafe,
        and refusing would break calls the skill handles perfectly well.
        """
        reason = guardrail.check("x", {"path": "/a", "surprise": 1}, SCHEMA)
        assert reason.should_run

    def test_extras_are_refused_only_when_the_schema_says_so(self):
        strict = {**SCHEMA, "additionalProperties": False}
        reason = guardrail.check("x", {"path": "/a", "surprise": 1}, strict)
        assert not reason.should_run
        assert "surprise" in reason.message

    def test_no_schema_means_nothing_to_check(self):
        reason = guardrail.check("x", {"anything": object()}, {})
        assert reason.should_run
        reason2 = guardrail.check("x", {"anything": object()}, None)
        assert reason2.should_run

    def test_an_unknown_type_is_left_unconstrained(self):
        """Guessing at a schema we do not understand would reject good calls."""
        odd = {"type": "object", "properties": {"x": {"type": "wat"}},
               "required": []}
        reason = guardrail.check("x", {"x": 5}, odd)
        assert reason.should_run


class TestThroughTheDispatcher:
    def test_a_malformed_call_never_reaches_the_skill(self):
        out = execute_tool_outcome("read_text_file", {"path": 12345}).text
        assert "wrong type" in out

    def test_a_normal_call_still_works(self):
        assert "wrong type" not in execute_tool_outcome("get_datetime", {}).text

    def test_repeating_the_same_call_is_not_refused(self):
        """A host doing repeated identical work must not be cut off.

        There was a tripwire here that stopped the fourth identical call. It is
        gone, and this test is the reason: `MAX_TOOL_ROUNDS = 4` already bounds
        the assistant's own loop, so within a turn it saved nothing, while
        across the MCP host and trigger rules it refused legitimate work - a
        request for five screenshots in a row failed on the fourth. It broke
        thirteen unrelated tests before anyone noticed, which is also the point.
        """
        outcomes = [execute_tool_outcome("get_datetime", {}).text
                    for _ in range(5)]
        assert not any("Stopping" in o for o in outcomes)


class TestARetryTellsTheModelHowToAskAgain:
    """The RETRY verdict's payload is its template, and it was being dropped.

    `guardrail.check` marks a call whose string argument starts with "ERROR:"
    as RETRY and fills `template` with the instruction to ask again. The
    dispatcher read only `message` - which is the model's own error text - so
    the turn echoed the failure back at it and spent a round of the loop on a
    call the template would have fixed.

    Two copies of the check existed and the fix landed in the unreachable one
    first, which is the same defect this file's docstring records from the
    other side: `_dispatch` runs `_guardrail_refuses` before `_dispatch_inner`
    is ever called, so the inline `guardrail.check` there could not fire. The
    test drives the public entry point, so it asserts the reachable path.
    """

    def test_the_template_reaches_the_turn_not_the_models_own_error_text(self):
        r = execute_tool_outcome("speak", {"text": "ERROR: Unterminated string"})
        assert "Retry speak" in r.text, (
            f"the model was handed its own error text instead of the retry "
            f"instruction: {r.text!r}")
        assert "Unterminated string" not in r.text, (
            "the raw error text is still what reached the model")

    def test_the_call_did_not_run(self):
        """A refused malformed call is not a completed action.

        `ran=False` is what keeps this out of the replay cache's receipts and
        out of the outcome model's "the tool worked" class.
        """
        r = execute_tool_outcome("speak", {"text": "ERROR: Unterminated string"})
        assert r.ran is False

    def test_an_invalid_call_still_gets_its_reason(self):
        """The template is not a substitute for the INVALID reason.

        `template or message` must not become `template or nothing`: a
        wrong-typed argument has no template and its reason is the only
        thing worth saying.
        """
        r = execute_tool_outcome("speak", {"text": {"not": "a string"}})
        assert r.ran is False
        assert "Not run" in r.text or "Refused" in r.text, r.text
        assert "Retry speak" not in r.text, (
            "an invalid call was answered with a retry it did not earn")
