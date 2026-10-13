"""Plan mode has to be a fact, not a request.

"Disk is nearly full - what would you do about it?" needs reading the machine
and deleting nothing. Chronoa's consent keys cannot express that: they are
global and persistent, so the only alternative to switching one on is a session
that cannot change anything at all, which is not what was asked for either.

The point of these tests is that the refusal happens in the **dispatch path**.
A system-prompt instruction would pass every one of them while the model went
ahead and deleted the file, which is the failure the feature exists to prevent.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

_REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_REPO / "usr" / "lib" / "shani-chronoa"))

from shani_chronoa import planmode  # noqa: E402
from shani_chronoa.capabilities import gated_by  # noqa: E402
from shani_chronoa.tools import TOOLS, execute_tool  # noqa: E402

DESCRIPTIONS = {s["function"]["name"]: s["function"].get("description", "") or ""
                for s in TOOLS if isinstance(s, dict) and s.get("function")}


@pytest.fixture(autouse=True)
def plan_mode_off():
    planmode.set_enabled(False)
    yield
    planmode.set_enabled(False)


def _gated_tool() -> str:
    for name, description in DESCRIPTIONS.items():
        if gated_by(name, description):
            return name
    pytest.skip("no gated tool to test with")


def _ungated_tool() -> str:
    for name, description in DESCRIPTIONS.items():
        if not gated_by(name, description) and name != "ask_user":
            return name
    pytest.skip("no ungated tool to test with")


class TestItGatesAtDispatch:
    def test_a_mutating_tool_is_refused_while_on(self):
        planmode.set_enabled(True, "planning")
        out = execute_tool(_gated_tool(), {})
        assert "Plan mode is on" in out

    def test_the_tool_did_not_run(self):
        """The refusal must come from the gate, not from the tool failing."""
        name = _gated_tool()
        planmode.set_enabled(True)
        gated = execute_tool(name, {"path": "/nonexistent-plan-mode-probe"})
        planmode.set_enabled(False)
        ungated = execute_tool(name, {"path": "/nonexistent-plan-mode-probe"})
        assert "Plan mode is on" in gated
        assert "Plan mode is on" not in ungated, (
            "the same call behaved identically with plan mode off, so the gate "
            "is not what stopped it"
        )

    def test_a_read_only_tool_still_works(self):
        """Reading the machine is the entire reason to be in plan mode."""
        planmode.set_enabled(True, "planning")
        out = execute_tool(_ungated_tool(), {})
        assert "Plan mode is on" not in out

    def test_nothing_is_refused_while_off(self):
        assert planmode.blocked_reason(_gated_tool()) == ""


class TestTheRefusalExplainsItself:
    def test_it_quotes_why_plan_mode_was_entered(self):
        planmode.set_enabled(True, "to answer what you would do about the disk")
        out = execute_tool(_gated_tool(), {})
        assert "what you would do about the disk" in out

    def test_it_offers_a_way_forward(self):
        planmode.set_enabled(True)
        out = execute_tool(_gated_tool(), {})
        assert "describe the steps instead" in out or "leave plan mode" in out

    def test_it_names_the_tool(self):
        name = _gated_tool()
        planmode.set_enabled(True)
        assert name in execute_tool(name, {})


class TestItIsNotATrap:
    def test_it_can_be_turned_off(self):
        name = _gated_tool()
        planmode.set_enabled(True)
        assert "Plan mode is on" in execute_tool(name, {})
        planmode.set_enabled(False)
        assert "Plan mode is on" not in execute_tool(name, {})

    def test_the_state_is_readable_from_anywhere(self):
        assert planmode.is_enabled() is False
        planmode.set_enabled(True, "because")
        assert planmode.is_enabled() is True
        assert planmode.reason() == "because"
        planmode.set_enabled(False)
        assert planmode.reason() == ""

    def test_it_starts_off(self):
        """A machine that silently boots into plan mode looks broken."""
        assert planmode.is_enabled() is False

    def test_entering_twice_is_not_a_stacking_privilege(self):
        planmode.set_enabled(True, "first")
        planmode.set_enabled(True, "second")
        assert planmode.reason() == "second"


class TestWhatItBlocksIsTheMaintainedAnswer:
    """**This class asserted the opposite of what it now asserts, and the
    assertion was the bug.**

    It read *"plan mode blocks exactly the gated tools"* - and that premise is
    wrong for the tools that matter most. Measured when it was written: plan
    mode blocked **92** tools and allowed `write_text_file` and `apply_patch`,
    so entering plan mode did not stop the model writing your files. **A
    consent key is not the maintained answer to "does this change something"** -
    it answers "does this need a switch switched on", which is a different
    question, and the two answers differ on the file-writing tools.

    `MUTATING_TOOLS` is the answer to the question being asked, so that is what
    is asserted - with `TALKS_ONLY` taken back out, because asking a question is
    not touching the machine and a plan mode that cannot ask anything is
    unusable.

    **The old assertion is kept as a control.** It now fails, and the failure
    names the tools where the two answers differ, which is the measurement the
    change was made on.
    """
    def test_it_blocks_everything_that_changes_something(self):
        """**The union, and both halves are load-bearing.**

        `MUTATING_TOOLS` is the answer to *"does this change something"*;
        `gated_by` is the answer to *"does this need a switch"*. The first
        version of this test asserted only the first and named `airplane_mode` -
        a tool that changes the machine and has no consent key of its own beyond
        the radio one, so it is in the second set and not the first. Asserting
        one half drops the other, and the half dropped is the half that was
        already there before this change.

        Measured today over 256 tools: **130 blocked**, against 92 for the
        consent-key answer alone.
        """
        from shani_chronoa.capabilities import MUTATING_TOOLS, TALKS_ONLY

        planmode.set_enabled(True)
        expected_blocked = (set(MUTATING_TOOLS)
                            | {n for n, d in DESCRIPTIONS.items()
                               if gated_by(n, d)}) - set(TALKS_ONLY)
        for name in DESCRIPTIONS:
            expected = name in expected_blocked
            assert bool(planmode.blocked_reason(name)) is expected, (
                f"{name}: plan mode disagrees with the maintained answer about "
                f"whether this changes anything")
        assert len(expected_blocked) >= 130, (
            f"only {len(expected_blocked)} tools blocked - the file-writing tools "
            f"this change was made for would be through again")

    def test_the_two_answers_really_do_differ(self):
        """**The control: the old assertion must still be false.**

        Without this, a change that made the two sets identical would pass the
        test above and quietly restore the premise - and the 38 tools where they
        differ are the whole reason the answer moved.
        """
        from shani_chronoa.capabilities import MUTATING_TOOLS, TALKS_ONLY

        gated = {n for n, d in DESCRIPTIONS.items() if gated_by(n, d)}
        blocked = set(MUTATING_TOOLS) - set(TALKS_ONLY)
        assert blocked - gated, (
            "every mutating tool is gated, so the two answers coincide and the "
            "question of which is maintained has gone away - re-measure before "
            "assuming either is right")
        for name in ("write_text_file", "apply_patch"):
            assert name in blocked - gated, (
                f"{name} is the tool the old plan mode let through; if it is "
                f"now gated, re-measure the whole decision")

    def test_a_question_is_not_a_change(self):
        """`ask_user` is in `MUTATING_TOOLS` because a question has an effect -
        it appears. Blocking it would make plan mode a session in which nothing
        can be asked, which is not what the mode is for."""
        from shani_chronoa.capabilities import TALKS_ONLY

        planmode.set_enabled(True)
        for name in sorted(TALKS_ONLY):
            assert planmode.blocked_reason(name) == "", (
                f"{name} asks rather than changes, and plan mode blocks it")
