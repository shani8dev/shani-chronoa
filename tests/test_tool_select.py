"""Only the tool schemas a request could use are sent with it.

All 80 schemas were ~11,800 tokens with every request, against the 8192 /
4096 / 2048-token windows the hardware tiers ask Ollama for: every request
overflowed and was cut (found by shani-testbed's chronoa-voice run).
"""

import json

import pytest

from shani_chronoa.tool_select import CORE, select_tools
from shani_chronoa.tools import TOOLS


def _names(tools):
    return {t["function"]["name"] for t in tools}


def _tokens(tools):
    return len(json.dumps(tools)) // 4


def test_all_of_them_did_not_fit_any_tier():
    """The bug, measured: this test documents why selection exists."""
    assert _tokens(TOOLS) > 8192


@pytest.mark.parametrize("request_, tool", [
    ("what is two plus two", "calculate"),
    ("press the enter key", "press_key"),
    ("turn the volume up", "set_volume"),
    ("set a timer for 5 minutes", "set_timer"),
    ("open firefox", "open_application"),
    ("is my battery low", "get_battery_status"),
    ("make the screen darker", "set_brightness"),
    ("delete the file notes.txt", "delete_file"),
    ("what is the weather in mumbai today", "web_search"),
    ("what time is it", "get_datetime"),
    # each word had two entries in the synonym table and the second silently won
    ("fix up my photo", "edit_image"),
    ("set this photo as my background", "set_wallpaper"),
    ("is the internet down", "check_internet"),
    ("search the internet for flights", "web_search"),
])
def test_the_tool_a_request_needs_is_sent(request_, tool):
    assert tool in _names(select_tools(request_, TOOLS))


@pytest.mark.parametrize("request_", ["what is two plus two", "press the enter key",
                                      "how can i travel from mumbai to pune", "", "hello"])
def test_a_selection_fits_the_smallest_window(request_):
    sent = select_tools(request_, TOOLS)
    assert _tokens(sent) < 2048 * 0.75, "leave room for the conversation in a 2048 window"


def test_the_core_is_always_sent():
    assert set(CORE) <= _names(select_tools("zzzz qqqq", TOOLS))


def test_a_tool_used_this_turn_stays_sent():
    assert "press_key" in _names(select_tools("what is two plus two", TOOLS, in_use={"press_key"}))


def test_selection_never_invents_a_tool():
    assert _names(select_tools("anything at all", TOOLS)) <= _names(TOOLS)
