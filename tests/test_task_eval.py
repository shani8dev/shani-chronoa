"""The task eval's scorer: a wrong call must never score as right, and the cases must name real tools.

An eval that cannot fail measures nothing, so the rules are checked here from
both sides - the call that should pass, and near-misses that must not.
"""

import importlib.util
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
spec = importlib.util.spec_from_file_location("task_eval", ROOT / "tools" / "task_eval.py")
task_eval = importlib.util.module_from_spec(spec)
spec.loader.exec_module(task_eval)


def call(name, **args):
    return [{"name": name, "args": args}]


def test_numbers_strings_lists_and_contains():
    case = {"expect": [{"tool": "set_timer", "args": {"seconds": 90, "label": "~egg"}}]}
    assert task_eval.score(case, call("set_timer", seconds=90.0, label="Eggs")) [:2] == (True, True)
    assert task_eval.score(case, call("set_timer", seconds=900, label="eggs"))[:2] == (True, False)
    assert task_eval.score(case, call("set_timer", seconds=90))[:2] == (True, False), "a missing argument is wrong"
    assert task_eval.score(case, call("reminders", text="eggs"))[:2] == (False, False)
    cur = {"expect": [{"tool": "convert_currency", "args": {"from_currency": ["USD", "dollar"]}}]}
    assert task_eval.score(cur, call("convert_currency", from_currency="usd"))[1]
    assert not task_eval.score(cur, call("convert_currency", from_currency="EUR"))[1]


def test_alternatives_and_presence():
    case = {"expect": [{"tool": "get_weather", "args": {"place": "~pune"}}, {"tool": "web_search", "args": {"query": "~pune"}}]}
    assert task_eval.score(case, call("web_search", query="weather in Pune today"))[:2] == (True, True)
    calc = {"expect": [{"tool": "calculate", "args": {"expression": "*"}}]}
    assert task_eval.score(calc, call("calculate", expression="17*23"))[1]
    assert not task_eval.score(calc, call("calculate", expression=""))[1]


def test_no_tool_cases_and_asking():
    chat = {"none": True}
    assert task_eval.score(chat, [])[:2] == (True, True)
    assert task_eval.score(chat, call("web_search", query="joke"))[:2] == (False, False)
    vague = {"none": True, "ask": True}
    assert task_eval.score(vague, call("ask_user", question="Delete what?", options=[]))[1]
    assert not task_eval.score(vague, call("delete_file", path="x"))[1]


def test_unparsed_arguments_are_kept_and_fail():
    calls = task_eval._calls({"tool_calls": [{"function": {"name": "set_volume", "arguments": "{percent: 40"}}]})
    assert calls[0]["args"] == {"__unparsed__": "{percent: 40"}
    assert not task_eval.score({"expect": [{"tool": "set_volume", "args": {"percent": 40}}]}, calls)[1]


def test_every_case_names_real_tools_and_real_arguments():
    from shani_chronoa.tools import TOOLS
    params = {t["function"]["name"]: set((t["function"].get("parameters") or {}).get("properties", {})) for t in TOOLS}
    cases = json.loads((ROOT / "tools" / "eval_cases.json").read_text())["cases"]
    assert len({c["id"] for c in cases}) == len(cases)
    for c in cases:
        for want in c.get("expect", []):
            assert want["tool"] in params, f"{c['id']}: no tool {want['tool']}"
            unknown = set(want.get("args", {})) - params[want["tool"]]
            assert not unknown, f"{c['id']}: {want['tool']} has no argument {unknown}"
        assert c.get("none") or c.get("expect"), c["id"]


def test_the_recorded_coverage_is_still_the_coverage():
    """`eval_cases.json` states how much of the skill set it asserts.

    It is a *partial* instrument - 55 of 200 skills - and nothing else in the
    tree could tell you that: the suite proves every skill is reachable and
    that every skill dispatches, and neither of those asks whether the model
    picks the right one. So the gap was silent, and a silent gap in the
    instrument that measures the stated goal ("the harness should be good
    enough that the smallest model beats bigger ones") is the kind that gets
    forgotten.

    The numbers are asserted against the file rather than trusted, so adding
    cases updates the claim and forgetting to update it fails here.
    """
    from shani_chronoa.tools import TOOLS

    data = json.loads((ROOT / "tools" / "eval_cases.json").read_text())
    stated = data.get("coverage")
    assert stated, (
        "eval_cases.json records no coverage. The scorer only checks the "
        "cases it has, so without a stated scope nobody can tell how much of "
        "the skill set is measured - and 'no test' reads as 'nothing left to "
        "do'.")

    skills = {t["function"]["name"] for t in TOOLS}
    asserted = {want["tool"] for c in data["cases"] for want in c.get("expect", [])}
    asserted &= skills
    assert stated["cases"] == len(data["cases"]), (
        f"coverage.cases says {stated['cases']}, the file has {len(data['cases'])}")
    assert stated["skills_total"] == len(skills), (
        f"coverage.skills_total says {stated['skills_total']}, the tree has "
        f"{len(skills)} skills - a new skill makes this stale")
    assert stated["skills_asserted"] == len(asserted), (
        f"coverage.skills_asserted says {stated['skills_asserted']} but the "
        f"cases assert {len(asserted)}: {sorted(asserted)}")
    # And the prose claim matches those numbers. This used to assert the word
    # "PARTIAL", which was right when it was written and wrong the moment
    # coverage became complete - a guard that fails on the truth is a guard that
    # has to be deleted to make the suite green.
    complete = stated["skills_asserted"] == stated["skills_total"]
    assert ("COVERAGE IS COMPLETE" in data["about"]) == complete, (
        "the about text's coverage claim does not match the numbers it records")
    if complete:
        assert "generated" in data["about"], (
            "complete coverage from generated cases must say so, or a reader "
            "cannot tell a transcript from a starting point")


# --------------------------------------------------------------------------
# What the model said when it did not act.
#
# `score` reports "no tool" for every case with no call, which discards the only
# evidence there was. The two states that matters most - declining the request,
# and promising it and never doing it - are opposite failures with opposite
# fixes, and both were `no tool`.
# --------------------------------------------------------------------------

#: The reply llama-server produced on this box for eval case `time`
#: ("What time is it?") on 2026-10-12, verbatim. Not invented: this is the
#: fixture's reason for existing, because it is what turned out to be wrong about
#: an empty response.
REAL_REFUSAL = ("I don't have access to real-time data, so I can't tell you the "
                "exact current time. However, I can help you find your time zone.")
REAL_EMPTY = ""


class TestWhatItSaidWhenItDidNotAct:
    def test_the_measured_refusal_is_a_refusal(self):
        assert task_eval.classify_reply(REAL_REFUSAL) == task_eval.REPLY_REFUSAL

    def test_an_empty_reply_is_empty_and_not_an_answer(self):
        """**The measured reason this state exists at all.**

        Qwen3 is a thinking model: with `max_tokens: 80` the same question that
        produced `REAL_REFUSAL` came back with `content == ""`, the budget spent
        on `reasoning_content` before it spoke. An empty string contains no
        marker, so every other branch passes it through — as `answer`.
        """
        assert task_eval.classify_reply(REAL_EMPTY) == task_eval.REPLY_EMPTY
        assert task_eval.classify_reply("   \n ") == task_eval.REPLY_EMPTY

    def test_emphasis_does_not_hide_the_marker(self):
        """`I *can't*` splits the marker's own word apart. Measured as the reason
        a model writes it: markdown is how a chat reply arrives."""
        assert task_eval.classify_reply("I *can't* do that.") == task_eval.REPLY_REFUSAL

    def test_a_typographic_apostrophe_does_not_hide_the_marker(self):
        """U+2019 is what a phone keyboard and most word processors produce."""
        assert task_eval.classify_reply("I can’t do that.") == task_eval.REPLY_REFUSAL

    def test_a_narration_is_not_a_refusal(self):
        """**The distinction the whole addition exists for.** Promising an action
        and not taking it is a bug in the assistant's loop; declining the request
        is a property of the model. The same fix is not right for both."""
        assert task_eval.classify_reply("Let me check that for you.") == task_eval.REPLY_NARRATION

    def test_a_plain_answer_is_an_answer(self):
        assert task_eval.classify_reply("It is 3:42 pm.") == task_eval.REPLY_ANSWER

    def test_a_refusal_wins_over_a_narration_in_the_same_reply(self):
        """Both can appear in one message, and the refusal is the operative one:
        it is the answer, where the narration is only the wrapper."""
        assert task_eval.classify_reply(
            "Let me check. I can't access that, sorry.") == task_eval.REPLY_REFUSAL


def _results(rows):
    return {"select": {"right_tool": 0, "right_call": 0, "cases": len(rows),
                       "mean_seconds": "1.0", "not_measured": 0, "rows": rows}}


def _row(**kw):
    base = {"id": "x", "tool": False, "args": False, "seconds": 1.0, "seen": "no tool",
            "schemas": 20, "unavailable": False}
    base.update(kw)
    return base


class TestTheReportCarriesIt:
    """Pressed, not called - `markdown` is what a person reads, and every test
    above measures the classifier behind it rather than the sentence."""

    def test_a_refusal_is_visible_in_the_report(self):
        out = task_eval.markdown(_results([
            _row(id="time", reply=task_eval.REPLY_REFUSAL, text=REAL_REFUSAL)]))
        assert "refused outright" in out, out
        assert "time" in out, out

    def test_a_narration_is_visible_and_named_as_something_else(self):
        out = task_eval.markdown(_results([
            _row(id="both", reply=task_eval.REPLY_NARRATION, text="Let me check")]))
        assert "said it would act and did not" in out, out
        assert "both" in out, out

    def test_the_counts_are_a_rate_over_the_run_not_a_list(self):
        out = task_eval.markdown(_results([
            _row(id="a", reply=task_eval.REPLY_REFUSAL),
            _row(id="b", reply=task_eval.REPLY_ANSWER)]))
        assert "| select | 1 | 0 | 0 | 1 |" in out, out

    def test_a_run_with_nothing_to_say_adds_no_section(self):
        """An empty section reads as "nothing was refused" when it means "nothing
        was recorded" - the same substitution the classifier refuses."""
        out = task_eval.markdown(_results([_row(id="a", seen="no tool")]))
        assert "What the model said" not in out, out


def test_run_case_keeps_what_the_model_said():
    """**The recording, not the classifier.**

    `markdown`'s tests build their rows by hand, so they prove the report renders
    a classification but not that anything ever produces one. This drives
    `run_case` with a reply that declines, which is the only way to see whether
    the field is populated on the path the harness actually runs - and it is the
    same gap this repository records as "a test that calls the function is not a
    test that the control is reachable".
    """
    import asyncio

    class _Declines:
        async def chat_message(self, messages, tools=None):
            return {"content": REAL_REFUSAL, "tool_calls": None}

    case = {"id": "time", "say": "What time is it?", "expect": [{"tool": "get_datetime"}]}

    got = asyncio.run(task_eval.run_case(_Declines(), "select", case))

    assert got["reply"] == task_eval.REPLY_REFUSAL, got
    assert REAL_REFUSAL[:60] in got["text"], (
        f"the words are the evidence and they must survive: {got['text']!r}")
    assert got["seen"] == "no tool", got


def test_every_refusal_marker_is_exercised_by_something():
    """**A marker list is only as tested as its least-covered member.**

    A mutation emptied three of the thirteen refusal markers and this file stayed
    green: the one measured refusal contains "i don't have access" and "i can't",
    so the three that went unused had no witness at all. That is this
    repository's hand-kept-list defect in a list of thirteen strings, and the
    fix is not more samples but a property: each marker, on its own, must
    classify.
    """
    for marker in task_eval._REFUSAL_MARKERS:
        assert task_eval.classify_reply(marker) == task_eval.REPLY_REFUSAL, (
            f"the marker {marker!r} matches nothing on its own, so deleting it "
            "cannot fail")

    # **The marker alone, not a sentence containing it.** Wrapping each one in
    # filler made this property immune to the mutation it exists for: deleting
    # `"i'm sorry, but"` still classified "I am sorry, i'm sorry, but thing."
    # through `i'm sorry`... no - through nothing, but every *remaining* marker
    # still classified, because the wrapper itself matched one. A reply that is
    # *only* the deleted marker is the input that distinguishes them.
    assert task_eval.classify_reply("i'm sorry, but") == task_eval.REPLY_REFUSAL, (
        "a reply that is nothing but one marker must classify, or removing a "
        "marker cannot be observed")


def test_the_refusal_list_has_not_lost_a_phrase():
    """**Pinning the list, which looks like the hand-kept-list defect and is not.**

    The property above iterates the *current* list, so it catches a marker that
    matches nothing - and is blind to a marker that has been deleted, because a
    deleted marker simply stops being iterated. Deleting `"i'll look"` left this
    file green.

    Which direction matters settles the shape. Adding a phrase to a list of ways
    a model declines is an editorial judgement and going green on it is fine.
    **Deleting one makes a refusal stop being recognised**, and the result is a
    declining model reported as answering - so that direction is pinned. A
    literal list is the right instrument for a judgement being *reviewed*, and
    the wrong one for a fact being *derived*; `skills_total` is the latter and is
    generated for exactly that reason.
    """
    assert task_eval._REFUSAL_MARKERS == (
        "i can't", "i cant", "i cannot",
        "i won't", "i wont", "i will not",
        "i'm unable", "i am unable", "im unable",
        "i'm not able", "i am not able",
        "i don't have access", "i do not have access",
        "i'm sorry, but", "i am sorry, but", "unfortunately, i",
    ), (
        "a refusal phrase was added or removed. Adding is fine and needs no "
        "test change; removing one makes a decline read as an answer, so say why "
        "in the commit rather than in a quiet edit.")


def test_the_narration_list_has_not_lost_a_phrase():
    """Pinned for the same reason as the refusal list.

    The harm direction is the same one: a marker that stops being recognised turns
    *"I said I would check and did not"* into `answer`, which is the
    substitution this whole addition refuses. Adding is free, removing is not.
    """
    assert task_eval._NARRATION_MARKERS == (
        "let me", "i'll check", "i will check", "i'll look", "i will look",
        "one moment", "give me a moment", "hang on", "i'll find", "i will find",
        "i'll pull", "i will pull",
    ), "a narration phrase was added or removed; say why in the commit"


def test_every_narration_marker_is_exercised_by_something():
    for marker in task_eval._NARRATION_MARKERS:
        assert task_eval.classify_reply(marker) == task_eval.REPLY_NARRATION, (
            f"the marker {marker!r} matches nothing on its own, so deleting it "
            "cannot fail")
