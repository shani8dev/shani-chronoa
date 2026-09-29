"""Measured, and deliberately not acted on.

The tool schema is 34,181 bytes - roughly 8,500 tokens - on the wire for every
single request, sent before the user has typed anything. 24 of the 26
consent-gated tools are shut in a default install, and together they account for
12,089 bytes, 35% of the payload, for tools that cannot run. That is a real
cost and it is the largest single piece of per-turn overhead in the system.

The obvious fix is to stop advertising gated tools and let the model discover
them on demand. Claude Code and MCP hosts get large savings this way.

**This module does not do that, on purpose, and the reason is architectural.**

A grant needs the model to know the tool exists. The consent flow this codebase
built is: the model tries a gated tool, the dispatcher finds the key shut, and
the user is asked - "Shani wants to take a screenshot. That needs the
'vision-sense-enabled' permission, which is currently off. Allow it?" That
question is only answerable because the model could see `screenshot` in its
schema and understood it was being refused.

Remove the gated tools from the schema and the model has no way to know
`screenshot` exists. It cannot call it, so it is never refused, so it is never
offered a grant. The user asking "take a screenshot" gets "I can't do that"
rather than "I can, but it needs permission - allow it?". The 35% would be
bought with the single feature that makes the consent keys usable at all.

The cheaper alternative - keep every tool, shorten gated descriptions to one
line - was measured and is not worth it either. Descriptions are 5,186 of the
gated payload's 10,002 characters; capping them at 160 saves 1,461 bytes, under
4% of the total, and parameter schemas (4,816 characters) cannot be shortened
without making a call wrong. Trading real explanatory detail for 4% is a bad
trade even ignoring the conflict above.

So the cost stands, deliberately. If this ever changes, the precondition is not
"35% is a lot of tokens" - it is a discovery mechanism that survives the model
not being able to see a tool, which means the *consent question* has to be
answerable from the user's own words rather than from the model's attempt to
call something. That is a different consent design, not an optimisation.

These tests pin the decision so it cannot be undone by someone who measures only
the tokens.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

_REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_REPO / "usr" / "lib" / "shani-chronoa"))

from shani_chronoa import capabilities as C  # noqa: E402
from shani_chronoa.config import ChronoaConfig  # noqa: E402
from shani_chronoa.tools import TOOLS  # noqa: E402


def _payload(tools) -> int:
    return len(json.dumps({"tools": tools}, separators=(",", ":")))


@pytest.fixture(scope="module")
def config():
    return ChronoaConfig()


@pytest.fixture(scope="module")
def gated(config):
    shut = []
    for tool in TOOLS:
        function = tool["function"]
        key = C.gated_by(function["name"], function.get("description", ""))
        if key and not config.get_bool(key, False):
            shut.append(tool)
    return shut


class TestTheCostIsRealAndMeasured:
    def test_gated_tools_cost_a_large_share_of_the_payload(self, gated):
        """If this ever stops being true the argument above needs revisiting."""
        share = _payload(gated) * 100 // _payload(TOOLS)
        assert share > 20, f"gated payload is now only {share}% - re-evaluate the tradeoff"

    def test_most_gated_tools_are_shut_by_default(self, gated):
        assert len(gated) >= 20


class TestTheToolsStayVisible:
    """A grant needs the model to know the tool exists."""

    def test_a_shut_tool_is_still_advertised(self):
        """This is the decision, pinned.

        Removing it buys 35% of the token payload and costs the consent flow
        its entire entry point, so it stays.
        """
        assert "screenshot" in {t["function"]["name"] for t in TOOLS}
        assert "delete_file" in {t["function"]["name"] for t in TOOLS}

    def test_a_shut_tool_keeps_its_full_schema(self, gated):
        """Not just the name - the parameters, or a call it makes would be wrong."""
        for tool in gated:
            assert tool["function"].get("description"), \
                f"{tool['function']['name']} lost its description, so the model " \
                f"can no longer tell the user what it would have done"
            assert tool["function"].get("parameters") is not None

    def test_the_refusal_path_still_works_for_a_shut_tool(self):
        """The end-to-end reason for the above: a refusal is still explicable."""
        from shani_chronoa import permissions
        from shani_chronoa.tools import execute_tool_outcome
        permissions.clear()
        try:
            out = execute_tool_outcome("screenshot", {}).text
        finally:
            permissions.clear()
        assert "vision-sense-enabled" in out, (
            "a shut tool must still be able to explain itself, naming the "
            "setting that would enable it"
        )
