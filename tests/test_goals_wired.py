"""The goal queue, wired end to end: saved by a skill, run and answered in a panel.

`goals.py` was complete and unreachable - no producer, no consumer, and the rail
said so on purpose. This drives the real pieces: `manage_goals` through
`tools.execute_tool` (the chat path), the Goals panel stepping a run through
the same path, a run parking on a question, the answer unblocking it, and the
rail showing open runs.
"""

import time

import pytest

gi = pytest.importorskip("gi")
gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, GLib, Gtk  # noqa: E402

Adw.init()


@pytest.fixture(autouse=True)
def _isolated(gsettings_env, tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state"))
    from shani_chronoa import ask_bridge
    # Nobody to ask, so a refusal is the skill's own sentence (see
    # test_surfaced_backends._nobody_to_ask for why this matters in a batch).
    monkeypatch.setattr(ask_bridge, "_presenter", None)


def _tool(arguments):
    from shani_chronoa import tools
    return tools.execute_tool("manage_goals", arguments, origin=tools.ORIGIN_USER)


_PLAN = [
    {"thought": "read the time", "tool": "get_datetime", "arguments": {}, "name": "now"},
    {"thought": "read it again, once the city is known", "tool": "get_datetime",
     "arguments": {}, "name": "later", "depends_on": ["city"]},
]


def _wait(surface, timeout=30):
    ctx, start = GLib.MainContext.default(), time.time()
    while surface.running and time.time() - start < timeout:
        ctx.iteration(False)
        time.sleep(0.02)
    assert not surface.running, "the run did not finish"


def test_saving_needs_the_consent_key():
    assert "turned off" in _tool({"action": "add", "goal": "x", "steps": _PLAN})


def test_a_plan_is_saved_parked_answered_and_finished():
    from shani_chronoa import goals
    from shani_chronoa.config import ChronoaConfig
    from shani_chronoa.gui.surfaces import goals as panel
    ChronoaConfig().set("goals-enabled", "true")

    said = _tool({"action": "add", "goal": "check the time twice", "steps": _PLAN})
    assert said.startswith("Saved goal"), said
    run_id = said.split()[2].rstrip(":")
    assert "check the time twice" in _tool({"action": "list"})

    surface = panel.build(None).surface
    surface.run(run_id, to_the_end=True)
    _wait(surface)
    run = surface.store.load(run_id)
    assert run.phase == goals.Phase.AWAITING, run.phase
    assert "city" in run.awaiting_reason and "now" in run.results

    surface.answer(run_id, "Pune")
    surface.run(run_id, to_the_end=True)
    _wait(surface)
    run = surface.store.load(run_id)
    assert run.phase == goals.Phase.COMPLETED, run.phase
    assert run.results["city"] == "Pune" and "later" in run.results


@pytest.mark.parametrize("steps,why", [
    ([{"tool": "no_such_skill", "name": "a"}], "not an installed skill"),
    ([{"tool": "manage_goals", "name": "a"}], "another goal"),
    ([], "at least one step"),
])
def test_bad_plans_are_refused(steps, why):
    from shani_chronoa.config import ChronoaConfig
    ChronoaConfig().set("goals-enabled", "true")
    said = _tool({"action": "add", "goal": "g", "steps": steps})
    assert why in said and "Nothing was saved" in said, said


def test_results_fill_dollar_names_and_nothing_else():
    from shani_chronoa import goals
    filled = goals.fill({"text": "it is $now in $city, costs $5", "n": 3, "l": ["$now"]},
                        {"now": "noon", "city": "Pune"})
    assert filled == {"text": "it is noon in Pune, costs $5", "n": 3, "l": ["noon"]}


def test_the_rail_shows_open_goals_and_hides_without_them():
    from shani_chronoa import goals
    from shani_chronoa.gui import rail
    view = rail.NowRail()
    view._fill_goals()
    assert not view._goals.get_visible(), "an empty Goals section was shown"
    store = goals.GoalStore(goals.store_root())
    store.save(goals.new_run("tidy downloads", [goals.PlannedStep("t", "get_datetime", {}, "x")]))
    view._fill_goals()
    assert view._goals.get_visible()
    labels = []
    node = [view._goals]
    while node:
        w = node.pop()
        if isinstance(w, Gtk.Label):
            labels.append(w.get_label())
        c = w.get_first_child()
        while c:
            node.append(c)
            c = c.get_next_sibling()
    assert any("tidy downloads" in (t or "") for t in labels), labels
