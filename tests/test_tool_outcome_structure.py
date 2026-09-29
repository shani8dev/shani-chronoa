"""A tool that never ran used to look exactly like one that did.

Everything `execute_tool` returned was a string, so "the tool ran and the answer
is X" and "there is no such tool" / "plan mode refused" / "it crashed" were one
shape, and a caller - or the model - had to infer which from wording. Worse, the
verification verdict *was* computed exactly where it was needed and then thrown
away: `execute_tool_outcome` re-derived it by matching a marker in the prose it
had just been handed.

The two-bit result is what goose carries as `is_error` on the tool response:

    ran=True,  VERIFIED   - ran, post-condition held
    ran=True,  UNVERIFIED - ran, nothing to check against
    ran=True,  FAILED     - ran, post-condition did not hold
    ran=False, UNVERIFIED - never executed
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

_REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_REPO / "usr" / "lib" / "shani-chronoa"))

from shani_chronoa import planmode  # noqa: E402
from shani_chronoa import verification  # noqa: E402
from shani_chronoa.tools import (  # noqa: E402
    DispatchResult,
    ToolOutcome,
    execute_tool,
    execute_tool_outcome,
)


@pytest.fixture(autouse=True)
def plan_mode_off():
    planmode.set_enabled(False)
    yield
    planmode.set_enabled(False)


class TestTheStringAPIIsUnchanged:
    def test_execute_tool_still_returns_a_string(self):
        result = execute_tool("get_datetime", {})
        assert isinstance(result, str)
        assert result.strip()

    def test_a_refusal_is_still_a_readable_sentence(self):
        """The model reads this. Prose is right for it - just not sufficient."""
        planmode.set_enabled(True, "planning")
        out = execute_tool("delete_file", {"path": "/tmp/x"})
        assert isinstance(out, str)
        assert "Plan mode is on" in out


class TestDidItRun:
    def test_a_tool_that_answered_ran(self):
        assert execute_tool_outcome("get_datetime", {}).ran is True

    def test_an_unknown_tool_did_not_run(self):
        result = execute_tool_outcome("no_such_tool_at_all", {})
        assert result.ran is False
        assert result.is_error is True

    def test_a_plan_mode_refusal_did_not_run(self):
        planmode.set_enabled(True)
        result = execute_tool_outcome("delete_file", {"path": "/tmp/x"})
        assert result.ran is False
        assert "Plan mode is on" in result.text

    def test_a_local_tool_that_answered_ran(self):
        """Headless, `ask_user` refuses - but it ran, and said so honestly."""
        result = execute_tool_outcome("ask_user", {"question": "q",
                                                  "options": ["a", "b"]})
        assert result.ran is True
        assert "nobody" in result.text.lower()


class TestTheVerdictIsNoLongerReParsed:
    def test_the_dispatch_verdict_reaches_the_outcome(self):
        """The defect: the verdict was known at dispatch and re-derived from text.

        Asserted by checking the outcome is built from the dispatch, not by
        matching a marker - a test that greps the prose would pass even while the
        re-parsing was still there.
        """
        from shani_chronoa import tools

        seen = {}
        original = tools._dispatch

        def spy(name, arguments, by_reference=False, origin=tools.ORIGIN_USER):
            result = original(name, arguments, by_reference=by_reference,
                              origin=origin)
            seen["dispatch"] = result
            return result

        tools._dispatch = spy
        try:
            outcome = tools.execute_tool_outcome("get_datetime", {})
        finally:
            tools._dispatch = original

        assert seen["dispatch"].verdict is outcome.verdict
        assert seen["dispatch"].ran is outcome.ran
        assert seen["dispatch"].text == outcome.text

    def test_verdict_from_text_is_no_longer_on_the_outcome_path(self):
        """The docstring is excluded, and deliberately.

        It *should* name the function it removed - that is the record of why this
        change exists. Grepping the whole source therefore fails on correct code,
        which is the same over-specification the plan-mode tests avoid. This
        follows the docstring-stripping helper the gate-enforcement test already
        uses for the same reason.
        """
        import inspect

        from shani_chronoa import tools

        source = inspect.getsource(tools.execute_tool_outcome)
        doc = inspect.getdoc(tools.execute_tool_outcome) or ""
        body = source.replace(doc, "")
        assert "verdict_from_text" not in body, (
            "execute_tool_outcome is parsing the marker out of the prose again, "
            "which is the bug this change was meant to remove"
        )
        # Nothing is asserted about the docstring's wording. It should explain
        # what was removed and why; demanding it name the old function
        # verbatim is the same over-specification that made this test fail on
        # correct code in the first place.


class TestTheTypesAgree:
    def test_tool_outcome_mirrors_dispatch_result(self):
        for field in DispatchResult._fields:
            assert field in ToolOutcome._fields, f"ToolOutcome is missing {field}"

    def test_is_error_is_just_not_ran(self):
        assert DispatchResult("t", verification.Verdict.UNVERIFIED, False).is_error
        assert not DispatchResult("t", verification.Verdict.FAILED, True).is_error

    def test_evidence_defaults_to_empty(self):
        assert DispatchResult("t", verification.Verdict.UNVERIFIED, True).evidence == ""


class TestWhatIsStillNotStructural:
    """The gap this change does not close, recorded so it is not forgotten.

    A consent refusal happens *inside* the skill, so the dispatch sees exit 0
    and reports `ran=True, UNVERIFIED` - indistinguishable from a genuine
    answer. Making it structural means changing all 69 skills, so it is stated
    rather than faked with a heuristic that would guess wrong.
    """

    def test_a_consent_refusal_still_reads_as_ran(self):
        from shani_chronoa.config import ChronoaConfig

        config = ChronoaConfig()
        config.set("file-delete-enabled", "false")
        try:
            result = execute_tool_outcome("delete_file", {"path": "/tmp/nope"})
            assert result.ran is True, (
                "if this ever becomes False, consent refusals have gained a "
                "structural signal and this note is out of date"
            )
            assert "not permitted" in result.text.lower() or "turned off" in result.text.lower()
        finally:
            config.set("file-delete-enabled", "true")
