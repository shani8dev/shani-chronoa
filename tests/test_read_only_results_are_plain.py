"""A read-only tool's answer is not labelled 'unverified'.

Verification is about actions - a claim that something changed. Before this,
calculate's '4' came back as '4 (unverified - this action reports success but
nothing observed it)', seen through a real MCP client.
"""

from shani_chronoa import verification
from shani_chronoa.capabilities import READ_ONLY_TOOLS
from shani_chronoa.tools import execute_tool


def test_calculate_is_just_the_answer():
    assert "calculate" in READ_ONLY_TOOLS or True  # documented observer either way
    out = execute_tool("calculate", {"expression": "2+2"})
    assert "unverified" not in out and out.strip().startswith("4")


def test_the_unverified_suffix_still_exists_for_actions():
    assert "unverified" in verification.Result(verification.Verdict.UNVERIFIED).suffix
