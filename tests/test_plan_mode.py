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
    def test_it_blocks_exactly_the_gated_tools(self):
        """No second list. A parallel one would drift from the consent keys."""
        planmode.set_enabled(True)
        for name, description in DESCRIPTIONS.items():
            expected = bool(gated_by(name, description))
            assert bool(planmode.blocked_reason(name)) is expected, (
                f"{name}: plan mode disagrees with the consent keys about "
                f"whether this changes anything"
            )
