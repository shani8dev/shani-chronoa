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
