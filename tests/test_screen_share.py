"""Screen share: the assistant's half - the `screen_to_phone` skill and its switch.

The panel moved to Shani Link (the Screen tab of Devices) on 2026-11-04, with its
tests (shani-link tests/test_app_screen_share.py). What stays here is Chronoa's
own claim: the skill is a tool the assistant can call, and it is gated behind
`screen-share-enabled`.
"""
import os
import sys

sys.path.insert(0, os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "usr", "lib", "shani-chronoa"))


def test_the_skill_is_a_tool_the_assistant_can_call():
    from shani_chronoa import capabilities, tools

    names = [t.get("function", {}).get("name") for t in tools.TOOLS]
    assert "screen_to_phone" in names, "the skill is not registered at all"
    assert capabilities.GATED.get("screen_to_phone") == "screen-share-enabled"


def test_the_panel_is_not_in_chronoas_sidebar_any_more():
    from shani_chronoa.gui.surfaces import SURFACE_IDS
    assert "screen_share" not in SURFACE_IDS
