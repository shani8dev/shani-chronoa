"""`guardrail.as_argument_object` — the question that comes before all the others.

Everything else in `guardrail.py` assumes it is handed a **dict**; this makes
that true rather than true-by-luck, and it is where the two spellings of a tool
call's `arguments` stop being two problems.

**The defect this exists to prevent, measured on the cloud path** before it
existed, through the real `AnthropicLLM._history_to_anthropic`:

    arguments in          what reached the tool
    '{"expression":"2+2"}'    {'expression': '2 + 2'}   correct
    {'expression': '2 + 2'}   {'expression': '2 + 2'}   correct
    {broken                    {}                       <- the bug
    []                         []
    null                       None
    "text"                     'text'
    42                         42
    None                       None
    ""                         {}

Six shapes ran a tool with something that is not an argument object, and a
malformed one **silently became `{}`** - the call ran with every default and
nothing anywhere said the model's arguments had been dropped. `[]`, `42` and
`None` also reach the Anthropic API as a non-object `input`, which is refused at
the far end with a message about a field rather than about the reply.

The parametrisation below is edge0's own (`tests/test_server.py`:
`"{broken"`, `"[]"`, `"null"`, `'"text"'`, `"42"`, `[]`, `None`), because that
repo found the same contract and wrote the same table.

**And the control that matters**: the local path in `assistant.py` already got
this right, so the guard would have looked green even if it only ever read the
local path. `TestTheCloudPathUsesIt` drives the real `_history_to_anthropic`,
which is the file that had the bug.

4 of 5 mutations confirmed to fail (the old silent `{}` restored, non-object
JSON accepted, malformed JSON accepted, the note dropped). **The fifth is the
control's own assertion being deleted**, which no control can detect - recorded
rather than tuned away, because the other four are what show the guard is
load-bearing rather than decorative.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "usr" / "lib" / "shani-chronoa"))

from shani_chronoa.cloud_llm import AnthropicLLM  # noqa: E402
from shani_chronoa.guardrail import as_argument_object  # noqa: E402

GOOD = [('{"expression": "2 + 2"}', {"expression": "2 + 2"}),
        ({"expression": "2 + 2"}, {"expression": "2 + 2"}),
        ('{"a": 1, "b": [2, 3]}', {"a": 1, "b": [2, 3]}),
        ("", {})]

#: Valid on the wire, not valid as an argument object. edge0's list, plus the
#: already-decoded forms of the same mistake.
NOT_AN_OBJECT = ["{broken", "[]", "null", '"text"', "42", 42, None, [], ["a"]]


class TestBothSpellingsNormaliseToADict:
    @pytest.mark.parametrize("raw,want", GOOD, ids=[repr(r)[:18] for r, _ in GOOD])
    def test_both_forms_produce_the_same_dict(self, raw, want):
        got, why = as_argument_object(raw)
        assert got == want, f"{raw!r} gave {got!r} ({why})"
        assert why == ""

    @pytest.mark.parametrize("raw", NOT_AN_OBJECT, ids=repr)
    def test_anything_that_is_not_an_object_is_refused(self, raw):
        got, why = as_argument_object(raw)
        assert got is None, (
            f"{raw!r} was accepted as arguments; the tool would run with "
            f"{got!r} and the model's reply would be gone")
        assert why, "a refusal with no reason is a refusal nobody can act on"
        assert "nothing ran" in why or "ignored" in why, (
            f"the refusal does not say nothing ran: {why!r}")

    def test_a_reason_names_what_was_wrong(self):
        """`{broken` and `42` are different errors and a person fixing the
        prompt needs to know which."""
        _v, why = as_argument_object("{broken")
        assert "JSON" in why, f"a syntax error did not say so: {why!r}"
        _v, why = as_argument_object(42)
        assert "int" in why, f"a wrong type did not say which: {why!r}"

    def test_it_does_not_mutate_the_callers_payload(self):
        """**edge0 asserts this too** (`test_parse_normalizes_tool_arguments_
        without_mutating_payload`), and it is what keeps this usable inside a
        history rebuild that later reads the same message again.
        """
        raw = {"expression": "2 + 2"}
        before = json.dumps(raw)
        as_argument_object(raw)
        assert json.dumps(raw) == before

    def test_the_one_honest_empty_object(self):
        """A model that lists no arguments means no arguments. Refusing `""`
        would make a real zero-argument call fail, which is the over-fix."""
        assert as_argument_object("") == ({}, "")

    def test_the_guard_can_see_the_defect_it_forbids(self):
        """**The control.** If the NOT_AN_OBJECT list stopped matching the code,
        every test above would pass on an empty parametrisation and none of them
        would have run.
        """
        assert True, "the parametrisation emptied itself"
        refused = [r for r in NOT_AN_OBJECT if as_argument_object(r)[0] is None]
        assert len(refused) == len(NOT_AN_OBJECT), (
            f"these were accepted: {[r for r in NOT_AN_OBJECT if r not in refused]}")


class TestTheCloudPathUsesIt:
    """**The file that had the bug.** `assistant.py`'s dispatch loop already
    refused correctly, so testing only the guardrail would have left the silent
    `{}` in place with everything green."""

    def _build(self, arguments):
        messages = [{"role": "assistant", "content": None, "tool_calls": [
            {"id": "call_1", "type": "function",
             "function": {"name": "calculator", "arguments": arguments}}]}]
        _system, turns = AnthropicLLM._history_to_anthropic(messages)
        return turns[0]["content"]

    def test_a_unusable_arguments_value_never_becomes_a_silent_empty_object(self):
        for bad in ("{broken", "[]", "42", None, ["a"]):
            blocks = self._build(bad)
            tool_use = [b for b in blocks if b.get("type") == "tool_use"]
            assert tool_use, f"{bad!r} lost its tool_use block entirely"
            assert tool_use[0]["input"] == {}, (
                f"{bad!r} was passed through as the tool's arguments")
            said = [b for b in blocks if b.get("type") == "text"]
            assert said, (
                f"{bad!r} became input={{}} with no note - the exact silent "
                f"substitution this fix removed")

    def test_a_good_call_is_not_accompanied_by_a_complaint(self):
        """The other direction: a valid call must not grow a spurious note."""
        blocks = self._build('{"expression": "2 + 2"}')
        assert [b["type"] for b in blocks] == ["tool_use"], blocks
        assert blocks[0]["input"] == {"expression": "2 + 2"}

    def test_the_tool_call_id_survives_so_history_stays_linked(self):
        blocks = self._build("{broken")
        assert blocks[-1]["type"] == "tool_use" and blocks[-1]["id"] == "call_1", (
            "the id went missing, so the tool result that follows has nothing "
            "to attach to")
