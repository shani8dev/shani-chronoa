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


class TestAValueOutsideTheDeclaredEnumIsRefusedBeforeDispatch:
    """**The enum is the only schema constraint nothing was checking.**

    Measured over the whole registry: 717 property nodes across 262 tools, of
    which **96 declare an `enum`** in **77 tools**, and **zero** declare a
    numeric or string bound. So `enum` is the entire gap - there is nothing else
    in these schemas left to validate.

    And it is a gap in *placement*, not in the check. `airplane_mode` already
    does this itself:

        action = (arguments.get("action") or "status").strip().lower()
        if action not in ("status", "on", "off"):
            return f"action must be status, on or off, not {action!r}."

    which is the right rule - but it runs **in the child**, after dispatch, after
    the sandbox and after the audit origin is set. So the refusal is a child
    result: the tool reads as *ran*, on a surface that marks the live ring. Doing
    it in `guardrail` instead is the same rule one layer earlier.
    """

    SCHEMA = {"type": "object",
              "properties": {"action": {"type": "string",
                                        "enum": ["list", "get", "set"]}},
              "required": ["action"]}

    def test_a_plausible_value_the_schema_forbids_is_refused(self):
        """"destroy" is the shape of a small model's mistake: a real word, in the
        right position, not in the list. Nothing else in the dispatch path can
        see it - the consent layer answers authority, not shape."""
        result = guardrail.check("files", {"action": "destroy"}, self.SCHEMA)
        assert not result.should_run, (
            "an out-of-enum action reached the sandbox; the refusal %r would "
            "come from the child, one layer too late" % result.message)

    def test_the_refusal_names_the_parameter_and_every_legal_value(self):
        """The model is asked again, and a message that names neither the field
        nor the alternatives is a message it cannot use - which is the whole
        reason this module exists rather than a traceback."""
        result = guardrail.check("files", {"action": "destroy"}, self.SCHEMA)
        assert "action" in result.message
        for legal in ("list", "get", "set"):
            assert legal in result.message, (legal, result.message)

    def test_the_spellings_the_skills_accept_are_still_accepted(self):
        """**The over-fix guard.** Every skill that reads an enum lowercases it,
        and `airplane_mode` also strips it. A gate stricter than the skills would
        refuse calls that work today, and `action="LIST"` becoming a refusal is a
        regression, not a guard."""
        for spelling in ("list", "LIST", "List", " list ", "\tlIST\n"):
            result = guardrail.check("files", {"action": spelling}, self.SCHEMA)
            assert result.should_run, (repr(spelling), result.message)

    def test_a_string_with_no_declared_enum_is_left_free(self):
        """No enum means no constraint. Inventing one is the over-fix in the
        other direction, and it is the failure this module's own docstring warns
        about - *"guessing at a schema we do not understand would reject good
        calls"*."""
        free = {"type": "object",
                "properties": {"path": {"type": "string"}}, "required": []}
        assert guardrail.check("files", {"path": "/tmp/anything at all"},
                               free).should_run

    def test_no_schema_means_no_enum_either(self):
        assert guardrail.check("x", {"action": "destroy"}, {}).should_run


class TestEveryEnumToolDeclaresWhatItsSkillAccepts:
    """**Why the gate above is safe, over the whole population.**

    Two of the 76 accept values **outside** their declared enum, through an alias
    table - `alarm` accepts 14 while declaring 6, `calendar_edit` accepts 10
    while declaring 3. So an enum gate written against the declaration alone
    would refuse `alarm action=remove` and `calendar_edit action=schedule`, which
    are the most natural phrasings of the verbs those tables already contain.

    The remedy is not an exception list in `guardrail` - it is to declare what the
    skill accepts, so the rule *"the value must be in the declared enum"* is
    true of every tool instead of true of 71. Offering `remove` to the model is
    also better selection than making it guess between `delete` and `remove`.

    This test is what makes that claim checkable rather than asserted, and it
    **fails today**: the 15 alias keys are accepted by their skills and declared
    by neither schema.
    """

    def _enum_tools(self):
        from shani_chronoa import skills
        schemas, _ = skills.discover_skills()
        for schema in schemas:
            fn = schema.get("function") or {}
            props = ((fn.get("parameters") or {}).get("properties") or {})
            enums = {k: list(v["enum"]) for k, v in props.items()
                     if isinstance(v, dict) and v.get("enum")}
            if enums:
                yield fn["name"], enums

    def test_the_population_is_what_the_gate_was_measured_against(self):
        """The number the reason above quotes, so a schema change that shrinks
        it cannot leave the reason quietly wrong."""
        tools = dict(self._enum_tools())
        assert len(tools) == 77, len(tools)
        assert sum(len(v) for v in tools.values()) == 96, (
            sum(len(v) for v in tools.values()))

    def test_every_alias_a_skill_accepts_is_declared_in_its_enum(self):
        import importlib
        undeclared = []
        for name, enums in self._enum_tools():
            try:
                module = importlib.import_module("shani_chronoa.skills." + name)
            except ImportError:
                continue
            aliases = getattr(module, "_ALIASES", None)
            if not isinstance(aliases, dict):
                continue
            declared = {v for values in enums.values() for v in values}
            for key in aliases:
                if key not in declared:
                    undeclared.append("%s: %r" % (name, key))
        assert not undeclared, (
            "these are accepted by their skills and declared by no schema, so an "
            "enum gate would refuse them: %s" % undeclared)
