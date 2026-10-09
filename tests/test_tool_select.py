"""Only the tool schemas a request could use are sent with it.

All 80 schemas were ~11,800 tokens with every request, against the 8192 /
4096 / 2048-token windows the hardware tiers ask Ollama for: every request
overflowed and was cut (found by shani-testbed's chronoa-voice run).
"""

import json

import pytest

from shani_chronoa.tool_select import (ARITHMETIC, CORE, ranked, select_tools,
                                       wants_a_conversation)
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


def _fat(words, count, size=160):
    """Large schemas that genuinely *match* `words`.

    The first version of these tests padded with `"padding " * 300`, which
    scored **zero** against every request - so `select_tools`' own
    `score >= top/3` floor excluded them before the cap was ever consulted, and
    all four cap mutations survived including "never apply the cap at all". A
    test that builds a selection the product has already discarded cannot
    observe what it does with one it keeps.

    Repeating the request's own words is what makes them legitimate matches,
    which is the situation the cap exists for: several plausible tools, all of
    them plausible, more of them than the window holds.
    """
    return [{"type": "function", "function": {
        "name": f"fat_{i}", "description": f"{words} " * size,
        "parameters": {"type": "object", "properties": {}},
    }} for i in range(count)]


BUDGET = int(2048 * 0.75)


def test_the_cap_is_real_and_not_just_this_registry_happening_to_fit():
    """The window guarantee, proved by a selection that does not fit.

    `test_a_selection_fits_the_smallest_window` passed in isolation and failed
    in a full run, because `ranked()` re-sorts through learned weights - so the
    same request selected a different set of eight tools depending on what else
    had run. A guarantee that holds or fails according to the machine's
    training history is not a guarantee, and the failing case is real: a
    selection over budget is a request the smallest tier rejects before the
    model reads a word, which is the whole reason selection exists.

    The premise is asserted, not assumed - "the uncapped selection is over
    budget" - so this cannot go back to passing for a reason that has nothing
    to do with the cap.
    """
    req = "brightness of the screen please"
    pool = list(TOOLS) + _fat("brightness screen", 8)
    floor_scored = ranked(req, pool)
    floor = floor_scored[0][0] / 3
    keep = {n for _s, n, _h in floor_scored[:8] if _s >= floor}
    assert _tokens([t for t in pool if t["function"]["name"] in keep]) > BUDGET, (
        "the premise: this registry does not overflow, so the cap is untested")

    sent = select_tools(req, pool)
    assert _tokens(sent) <= BUDGET, f"{_tokens(sent)} tokens against {BUDGET}"
    assert _tokens(sent) < _tokens([t for t in pool if t["function"]["name"] in keep])


def test_the_cap_drops_the_worst_match_rather_than_an_arbitrary_one():
    """What goes when the window is tight is the tool with the least evidence.

    The fat tools match "brightness screen" once each, so they score below
    `set_brightness`, whose own schema says it plainly. The best-supported
    answer to the request is the one that must survive the trim, and the
    padding is what gets spent.

    **The count is "as many as were needed", not "all of them":** the cap stops
    the moment the selection fits, so on this fixture one `fat_*` is left
    because dropping it too would waste the last of the budget on nothing.
    Asserting that none survived was my first attempt and it was wrong about
    the code, which is doing the better thing.
    """
    pool = list(TOOLS) + _fat("brightness screen", 8)
    sent = _names(select_tools("brightness of the screen please", pool))
    assert "set_brightness" in sent, sent
    left = sent & {f"fat_{i}" for i in range(8)}
    assert len(left) <= 1, f"trimmed the wrong end: {sent}"


def test_a_tool_in_use_is_never_starved_by_the_cap():
    """`in_use` beats the budget.

    A tool the person is already calling mid-task is not a candidate for
    trimming; dropping it would break a turn that was already under way.

    The tool is made enormous on purpose. The first version of this test put
    the padding in tools that scored *zero*, which `select_tools` drops before
    the cap is consulted - so the tool in use had plenty of room and removing
    the protection changed nothing. Here it is the only thing big enough to
    matter, so protecting it is the whole question.
    """
    giant = {"type": "function", "function": {
        "name": "read_text_file", "description": "padding " * 3000,
        "parameters": {"type": "object", "properties": {}}}}
    pool = [t for t in TOOLS if t["function"]["name"] != "read_text_file"] + [giant]
    sent = _names(select_tools("zzz qqq", pool, in_use={"read_text_file"}))
    assert "read_text_file" in sent, sent
    # and the cap really was under pressure, or the assertion is vacuous
    assert _tokens([t for t in pool if t["function"]["name"] == "read_text_file"]) > BUDGET


def test_the_core_is_never_dropped_even_when_it_does_not_fit():
    """If the core alone exceeds the window, send the core anyway.

    Dropping `ask_user` to fit would leave a turn with no way to ask the person
    anything, and the resulting rejection says nothing about why. The honest
    outcome is the over-budget core, which fails visibly.

    The core is inflated so the premise holds - with the real schemas it fits
    comfortably, the cap never runs, and "the core is protected" is true of a
    code path never entered, which is how removing the protection from the
    *scored* list left this file green.

    The request names what the schemas say, so the core tools are **scored**
    rather than sitting in the zero-score list. Both lists have to be protected;
    testing only the unscored one leaves the other filter untested.
    """
    core = [{"type": "function", "function": {
        "name": name,
        "description": f"brightness screen ask time search the web {'padding ' * 1100}",
        "parameters": {"type": "object", "properties": {}}}} for name in CORE]
    assert _tokens(core) > BUDGET, "premise: the core must overflow here"
    assert ranked("brightness of the screen", core), "premise: core must score"
    sent = _names(select_tools("brightness of the screen please", core))
    assert set(CORE) <= set(sent), sent


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
