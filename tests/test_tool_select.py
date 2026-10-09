"""Only the tool schemas a request could use are sent with it.

All 80 schemas were ~11,800 tokens with every request, against the 8192 /
4096 / 2048-token windows the hardware tiers ask Ollama for: every request
overflowed and was cut (found by shani-testbed's chronoa-voice run).
"""

import json

import pytest

from shani_chronoa.tool_select import ARITHMETIC, CORE, select_tools, wants_a_conversation
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


# --- a turn to answer versus a task to call a tool for ------------------------
#
# Both halves are here because each was measured wrong at least once.
# `chat-explain` was the last miss in the 68-case eval (tools/task_eval.py
# --configs select, 67/68), and it was not the model: the request never got
# the chance to be a chat turn, because "in TWO SENTENCES" read as a sum.


@pytest.mark.parametrize("request_", [
    "explain what a black hole is in two sentences",
    "explain gravity",
    "describe a black hole",
    "what is a black hole",
    "why is the sky blue",
    "explain it in three bullet points",
])
def test_a_question_is_answered_rather_than_looked_up(request_):
    assert wants_a_conversation(request_), request_


@pytest.mark.parametrize("request_", [
    # a number word beside an operator, or in a fraction, is arithmetic
    "what is two plus two",
    "what is 17 times 23",
    "what is 15% of 2400",
    "what is two thirds of six",
    "what is three quarters of 12",
    # and a machine noun is a task even though it is phrased as a question
    "what is the time",
    "what is the weather in mumbai",
    "set a timer for 5 minutes",
])
def test_arithmetic_and_machine_questions_are_tasks(request_):
    assert not wants_a_conversation(request_), request_


def test_a_bare_number_word_is_a_length_instruction_not_a_sum():
    """The specific defect, so the operator-less form cannot come back.

    Asserted as the pair rather than one case: a test that only says "two
    sentences is not arithmetic" passes just as happily if the matcher has
    stopped recognising arithmetic at all.
    """
    assert not ARITHMETIC.search("explain it in two sentences")
    assert ARITHMETIC.search("what is two plus two")
