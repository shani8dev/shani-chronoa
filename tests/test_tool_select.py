"""Only the tool schemas a request could use are sent with it.

All 80 schemas were ~11,800 tokens with every request, against the 8192 /
4096 / 2048-token windows the hardware tiers ask Ollama for: every request
overflowed and was cut (found by shani-testbed's chronoa-voice run).
"""

import json

import pytest

from shani_chronoa.tool_select import (ARITHMETIC, CORE, ranked, select_tools,
                                       wants_a_browser, wants_a_conversation)
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


@pytest.mark.parametrize("request_", [
    "Open blaze demo dot com and find the cheapest flight",
    "Go to the blaze demo website and find the cheapest flight",
    "open youtube dot com",
    "On blazedemo.com book the cheapest flight from Boston to London",
])
def test_a_site_request_is_never_trimmed_of_its_browser(request_):
    """The cap must not undo the rule that asked for the browser.

    `wants_a_browser` is a decision about what the request *is* - it names a
    site, or asks for something done on one - and `browse` is then **added**
    rather than scored. `_cap_to_window` drops by ascending score, so an added
    tool with no score of its own is exactly what it removes first when the
    budget is tight.

    That happened. Measured on "Open blaze demo dot com and find the cheapest
    flight": `keep` held 11 tools including `browse`, and `_cap_to_window`
    returned 7 without it - `browse` is ~850 tokens of a 1536-token budget. So
    the selection for a request whose whole premise was working on a website had
    no browser, and nothing anywhere said so: `wants_a_browser` still returned
    True.

    The counterpart is the file case, and both are asserted because the budget is
    zero-sum: protecting `browse` on "download the file from example.com to my
    Downloads folder" starves `open_file`, which is the tool that request is
    actually about. Two tests, one budget, and the rule has to say which wins.
    """
    assert wants_a_browser(request_), f"this request no longer trips the site rule: {request_!r}"
    assert "browse" in _names(select_tools(request_, TOOLS)), (
        f"{request_!r} names a website but the selection was trimmed of the "
        "browser - the token cap undid the site rule")


@pytest.mark.parametrize("request_", [
    "download the file from example.com to my Downloads folder",
])
def test_a_file_request_keeps_its_file_tools_even_though_it_names_a_site(request_):
    """The other half of the same budget: a download is not a browsing session.

    `_FILE_OR_APP` decides both whether the site displaces the file tools and
    whether the browser is protected from the cap. When it matches, the file
    tools win, because that is what the person asked about.
    """
    assert wants_a_browser(request_), "the site rule fires for this too - both halves are in play"
    assert "open_file" in _names(select_tools(request_, TOOLS)), (
        f"{request_!r} is about a file and lost open_file to the browser")


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


def _eval_says():
    """Every request in the eval corpus. Real ones - a 40-byte fixture is why a
    shape bug hid in this module for a session."""
    import pathlib
    cases = json.loads((pathlib.Path(__file__).resolve().parents[1] /
                        "tools" / "eval_cases.json").read_text())["cases"]
    return [case["say"] for case in cases]


class TestTheCapArithmeticIsMeasuringRatherThanReasoning:
    """The cap sums per-tool sizes instead of re-serialising the selection.

    `_fits` is `len(json.dumps(request_tools))`, and the arithmetic that replaced
    it has to produce **the same number**. The cap and the window test cannot
    disagree about what "fits" means, or the guarantee quietly becomes whatever
    the faster path happens to think.

    Three separate things can go wrong, and they need three separate pins:

    * a wrong **size** - not measuring the whole schema. `test_every_size_is_the_
      whole_schema`.
    * a wrong **formula** - brackets, separators. `test_the_sum_equals_what_
      serializing_would_say`.
    * a cap that does not actually cap. `test_every_real_request_fits_the_window`.

    None of these is hypothetical. The first was real: summing
    `len(json.dumps(fn))` rather than the schema is short by **34 chars per tool -
    95.4% of the true size** - and a selection of eight hands back ~68 tokens of
    a 2048 window it has already spent.

    The second is why these call `_selection_chars` instead of writing the
    arithmetic out again here. A test that re-derives what it is testing checks
    its own copy: with the formula written out in the test, dropping the brackets
    from the code left it **green**. That is the shape of a check that cannot
    fail, and it is the reason the code is a named function at all.
    """

    def test_every_size_is_the_whole_schema(self):
        """The size the cap sums is the size `_fits` measured."""
        from shani_chronoa import tool_select
        corpus = tool_select._corpus(list(TOOLS))
        wrong = ["%s: summed %d, real %d" % (t["function"]["name"],
                                              corpus.chars_by_name[t["function"]["name"]],
                                              len(json.dumps(t)))
                 for t in TOOLS
                 if corpus.chars_by_name[t["function"]["name"]] != len(json.dumps(t))]
        assert not wrong, (
            "the cap sums the wrong size for %d of %d tools, so the window "
            "guarantee is measured in something other than what is sent:\n  %s"
            % (len(wrong), len(TOOLS), "\n  ".join(wrong[:5])))

    def test_the_sum_equals_what_serializing_would_say(self):
        """`_selection_chars` is `len(json.dumps(...))`, on all 225 selections."""
        from shani_chronoa import tool_select
        corpus = tool_select._corpus(list(TOOLS))
        says = _eval_says()
        assert len(says) >= 200, "the corpus shrank: %d requests" % len(says)
        wrong, worst, first = 0, 0, ""
        for say in says:
            kept = select_tools(say, TOOLS)
            names = [t["function"]["name"] for t in kept]
            total = tool_select._selection_chars(names, corpus.chars_by_name)
            real = len(json.dumps(kept))
            if total != real:
                wrong += 1
                worst = max(worst, abs(total - real))
                first = first or ("%r: %d tools, summed %d, json.dumps says %d"
                                  % (say, len(kept), total, real))
        assert wrong == 0, (
            "%d of %d real selections disagree with len(json.dumps(...)), worst "
            "off by %d chars. %s" % (wrong, len(says), worst, first))

    def test_the_fast_path_agrees_with_the_function_it_replaced(self):
        """`_selection_chars` answers exactly as `_fits` does, at every budget.

        `_fits` is no longer on the hot path - that is the whole point - so
        without this it would be dead code, and dead code that documents the
        definition stops being checked the moment it stops being read. Here it is
        the **oracle**: the original, obviously-correct predicate, and the fast
        one has to agree with it.

        Swept over many budgets rather than the one the cap happens to use. Both
        sides do the same integer division, so a disagreement would not show up
        at every threshold - an off-by-`2*k` error is invisible at the single
        budget where the selection fits and visible at the one below it.
        """
        from shani_chronoa import tool_select
        corpus = tool_select._corpus(list(TOOLS))
        mismatches = []
        for say in _eval_says():
            kept = select_tools(say, TOOLS)
            names = [t["function"]["name"] for t in kept]
            chars = tool_select._selection_chars(names, corpus.chars_by_name)
            for budget in (0, 1, 50, 200, 400, 600, 800, 1000, 1536, 2048, 4096):
                fast = chars // 4 <= budget
                slow = tool_select._fits(kept, budget)
                if fast != slow:
                    mismatches.append("%r at budget %d: fast %s, _fits %s"
                                      % (say, budget, fast, slow))
        assert not mismatches, ("the fast path and the function it replaced "
                                "disagree at %d point(s):\n  %s"
                                % (len(mismatches), "\n  ".join(mismatches[:5])))

    def test_every_real_request_fits_the_window_or_has_nothing_left_to_drop(self):
        """The guarantee, over the whole corpus rather than five handpicked asks.

        `test_a_selection_fits_the_smallest_window` covers five requests someone
        thought of. The arithmetic that replaced measuring the selection runs on
        all 225, so all 225 are what has to hold.

        **The budget is not a hard ceiling, and this test says so.** `browse` is
        ~850 tokens and is protected - a request that cannot be answered without
        a browser is sent with one. `browse` plus `CORE` alone is past a 1536
        budget, so "Click." finishes at 1936 tokens. Measured on HEAD, unchanged
        by this work. So the invariant is *fits, or nothing droppable was left* -
        which still fails the moment the cap stops dropping, and fails if a new
        request overflows for any other reason.

        Asserting a flat "always fits" would have been red on this machine
        before a line changed, and would have been the kind of test that gets
        deleted instead of the bug that made it red.
        """
        from shani_chronoa import tool_select
        budget = 2048 * 0.75
        overflowed, unjustified = [], []
        for say in _eval_says():
            sent = select_tools(say, TOOLS)
            if _tokens(sent) < budget:
                continue
            names = {t["function"]["name"] for t in sent}
            overflowed.append("%r: %d tokens, budget %d, %d tools"
                              % (say, _tokens(sent), int(budget), len(sent)))
            # Everything left must be protected. If anything droppable remains,
            # the cap stopped dropping - which is the regression worth catching.
            protected = tool_select.protected_for(say, names, None)
            spare = sorted(names - protected)
            if spare:
                unjustified.append("%r still holds droppable %s" % (say, spare))
        assert not unjustified, (
            "%d request(s) went over budget with tools the cap was allowed to "
            "drop:\n  %s" % (len(unjustified), "\n  ".join(unjustified[:5])))
        # Named rather than merely tolerated: a new entry here is a behaviour
        # change and should be argued for, not discovered by a later reader.
        assert len(overflowed) == 1 and "Click." in overflowed[0], (
            "the set of requests that finish over budget changed - it was "
            "exactly 1 ('Click.', 1936 tokens, all five protected). Now %d:\n  %s"
            % (len(overflowed), "\n  ".join(overflowed[:5])))


class TestTheCorpusIsBuiltOnceAndOnlyForThisToolSet:
    """`_corpus` memoises the last tool list it was given, by identity.

    `select_tools` builds one `tools` list and hands the same object to
    `ranked()` and `_cap_to_window()`, which each asked for a corpus - 512
    `_tool_facts` lookups per request to build the same one twice.

    The memo is one entry deep and holds the list it was built from, which is
    what makes an identity key safe: a freed list's id could otherwise be handed
    to a different list. These are the two ways that goes wrong.
    """

    def test_another_tool_set_does_not_get_this_one_s_statistics(self):
        from shani_chronoa import tool_select
        # Distinct words on purpose: a shared one ("alpha only" / "beta only")
        # belongs in **both** corpora, and asserting it away would be the test
        # being wrong about what a document frequency is.
        small = [{"function": {"name": "alpha", "description": "alpha waters"}}]
        wide = [{"function": {"name": "beta", "description": "beta tides"}},
                {"function": {"name": "gamma", "description": "gamma rays"}}]
        one, two = tool_select._corpus(small), tool_select._corpus(wide)
        assert one.n == 1 and two.n == 2
        assert "alpha" in one.df, sorted(one.df)
        assert "alpha" not in two.df, (
            "the second tool set was handed the first one's words: %s" % sorted(two.df))
        # Expected stems come from `_words` rather than being spelled out: the
        # stemmer turns "rays" into "ray" and "tides" into "tid", and hardcoding
        # those would make this test fail for the right reason at the wrong time.
        expected = set(tool_select._words("beta tides gamma rays"))
        assert set(two.df) == expected, (sorted(two.df), sorted(expected))

    def test_the_facts_cache_is_bounded(self):
        """Keyed on content, so contents that keep changing would keep entries.

        A schema edited, installed, or generated by the Workbench is a key this
        has never seen. Unbounded, every one of them is kept - ~7 KB of stemmed
        words each - for the life of the process. The live registry is 256, so
        this bound is never reached by normal use.
        """
        from shani_chronoa import tool_select
        tool_select._TOOL_FACTS.clear()
        for i in range(tool_select._TOOL_FACTS_MAX + 50):
            tool_select._tool_facts(
                {"function": {"name": "tool%d" % i,
                              "description": "description number %d" % i}})
        assert len(tool_select._TOOL_FACTS) <= tool_select._TOOL_FACTS_MAX, (
            "the facts cache grew to %d entries" % len(tool_select._TOOL_FACTS))
        # ...and still correct afterwards: an evicted tool re-derives, it does not
        # come back wrong.
        again = tool_select._tool_facts(
            {"function": {"name": "tool0", "description": "description number 0"}})
        assert "number" in again.body

    def test_an_edited_schema_gets_its_own_facts(self):
        """Keyed on content, so an in-place edit lands on a different key.

        Keying on `id()` would have been faster and wrong: a schema edited in
        place keeps its identity, and the cache would go on describing the old
        one.
        """
        from shani_chronoa import tool_select
        tool = {"function": {"name": "alpha", "description": "the original words"}}
        before = tool_select._tool_facts(tool)
        tool["function"]["description"] = "completely different words"
        after = tool_select._tool_facts(tool)
        assert "original" in before.body and "original" not in after.body
        assert before is not after
