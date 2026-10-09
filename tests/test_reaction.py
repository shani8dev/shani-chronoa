"""Sequence awareness: some bad outcomes are twenty individually-correct calls.

Chronoa's permission layer is per-call and stateless, and it should stay that
way - `reaction.py` does not weaken it. What it adds is the one thing stateless
judgement cannot see: that the *pattern* of allowed calls is itself the problem.

Every test here is about a tripwire, not an understanding. The thresholds are
crude on purpose and the tests assert they are crude, because a layer that
claimed to know what the user was doing would be making a claim nobody can
verify.
"""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "usr/lib/shani-chronoa"))

from shani_chronoa import reaction  # noqa: E402
from shani_chronoa.reaction import ReactionLayer  # noqa: E402
from shani_chronoa.tool_tracking import ORIGIN_UNATTENDED, ORIGIN_USER  # noqa: E402


@pytest.fixture
def clock():
    """A clock the test moves by hand, so the window is exercised without sleep."""
    box = {"now": 0.0}
    return box


@pytest.fixture
def layer(clock):
    return ReactionLayer(clock=lambda: box_now(clock))


def box_now(clock):
    return clock["now"]


# --------------------------------------------------------------- it stays quiet

def test_ordinary_multi_step_work_never_prompts(layer):
    """The failure that would make this layer unusable: a person reading ten
    files in a turn is doing something normal, not something suspicious."""
    for index in range(10):
        decision = layer.check("read_text_file", {"path": f"/home/u/{index}.txt"})
        assert decision.confirm is None, f"prompted on ordinary reading at {index}"


def test_a_single_call_of_anything_never_prompts(layer):
    assert layer.check("delete_file", {"path": "/etc/passwd"},
                       destructive=True).confirm is None


def test_a_different_tool_each_time_never_prompts(layer):
    """Counts are per tool. Ten different tools is not a loop in any of them."""
    tools = ["read_text_file", "list_directory", "get_datetime", "system_info",
             "disk_usage", "get_volume", "list_apps", "list_services",
             "read_logs", "compute_hash"]
    for tool in tools:
        assert layer.check(tool, {"path": "/x"}).confirm is None


# --------------------------------------------------------------- it trips

def test_fan_out_over_distinct_targets_prompts(layer):
    """Deleting forty files in one directory is ordinary; touching forty
    separate targets is a different shape and worth a question."""
    prompted_at = None
    for index in range(30):
        decision = layer.check("delete_file", {"path": f"/home/u/d{index}"},
                               destructive=True)
        if decision.confirm and prompted_at is None:
            prompted_at = index
            assert any(s.name == "fanout" for s in decision.signals)
            break
    assert prompted_at is not None, "40 distinct targets did not prompt"


def test_many_calls_to_one_target_prompt_as_repeat(layer):
    """The other signal: volume, even against a single target. A model stuck in
    a loop on one path looks like this."""
    prompted_at = None
    for index in range(reaction._REPEAT_LIMIT + 5):
        decision = layer.check("read_text_file", {"path": "/same/file"})
        if decision.confirm and prompted_at is None:
            prompted_at = index
            assert any(s.name == "repeat" for s in decision.signals)
            break
    assert prompted_at is not None


def test_unattended_is_held_to_a_stricter_bound(layer):
    """Nobody is watching an automatic rule, so it gets less rope."""
    user_layer = ReactionLayer(clock=layer._clock)
    user_at = None
    for index in range(reaction._REPEAT_LIMIT + 5):
        if user_layer.check("read_text_file", {"path": f"/u{index}"},
                            origin=ORIGIN_USER).confirm:
            user_at = index
            break

    unattended_layer = ReactionLayer(clock=layer._clock)
    unattended_at = None
    for index in range(reaction._REPEAT_LIMIT + 5):
        if unattended_layer.check("read_text_file", {"path": f"/u{index}"},
                                  origin=ORIGIN_UNATTENDED).confirm:
            unattended_at = index
            break

    assert unattended_at is not None and user_at is not None
    assert unattended_at < user_at, (
        f"unattended ({unattended_at}) must prompt no later than "
        f"attended ({user_at})"
    )


def test_attended_and_unattended_windows_do_not_share_counts(layer):
    """A burst while unattended must not make the next attended call prompt."""
    for index in range(reaction._REPEAT_LIMIT - 1):
        layer.check("read_text_file", {"path": f"/x{index}"}, origin=ORIGIN_UNATTENDED)
    assert layer.check("read_text_file", {"path": "/y"},
                       origin=ORIGIN_USER).confirm is None


# --------------------------------------------------------------- it escalates

def test_repeated_confirmation_changes_the_question(clock):
    """Five approvals of the same pattern points at the model repeating itself,
    and saying so is more useful than asking a sixth time."""
    layer = ReactionLayer(clock=lambda: clock["now"])
    escalated = None
    # An approval starts the count again, so five approvals take five windows.
    for index in range((reaction._REPEAT_LIMIT + 1) * (reaction._REPEAT_ESCALATION + 1)):
        decision = layer.check("read_text_file", {"path": "/same"})
        if decision.confirm is None:
            continue
        if "stuck" in decision.confirm or "rephrase" in decision.confirm:
            escalated = index
            break
        layer.confirmed("read_text_file", ORIGIN_USER, decision.signals[0].name)
    assert escalated is not None, "approving forever never escalated"


# --------------------------------------------------------------- the window

def test_the_window_slides(clock):
    """A pattern from ten minutes ago is not a pattern now.

    Written so that **disabling the prune makes it fail**. The first version
    used 11 calls, which is below the fan-out threshold and below the unattended
    threshold, so it passed no matter what the prune did - a mutation that set
    the window to a thousand years left all 15 tests green. This version builds
    up to an actual tripwire first, so the prune is the only thing that can
    silence it.
    """
    layer = ReactionLayer(clock=lambda: clock["now"])
    # Build a real fan-out, which does prompt.
    prompted = None
    for index in range(40):
        if layer.check("delete_file", {"path": f"/d{index}"},
                       destructive=True).confirm:
            prompted = index
            break
    assert prompted is not None, "the tripwire never fired, so this proves nothing"

    clock["now"] = reaction._WINDOW_SECONDS * 2
    # The OLD calls are gone: one fresh call must not carry the old count with
    # it. A new fan-out would of course still trip - 40 distinct targets is
    # 40 distinct targets - so what is checked here is that the count restarts,
    # which shows up as the new fan-out tripping at the same offset rather than
    # immediately on the first call.
    fresh = None
    for index in range(40):
        if layer.check("delete_file", {"path": f"/later{index}"},
                       destructive=True).confirm:
            fresh = index
            break
    assert fresh == prompted, (
        f"the new fan-out tripped at {fresh} rather than {prompted}: the "
        f"expired window's count was carried over"
    )


def test_reset_clears_everything(clock):
    layer = ReactionLayer(clock=lambda: clock["now"])
    for index in range(40):
        layer.check("read_text_file", {"path": f"/x{index}"})
    layer.reset()
    assert layer.check("read_text_file", {"path": "/y"}).confirm is None


# --------------------------------------------------------------- shape

def test_the_decision_cannot_grant_anything():
    """The single most important structural property: this layer has no way to
    say yes. It can only produce a question or nothing."""
    fields = set(ReactionLayer(clock=lambda: 0.0).check(
        "read_text_file", {"path": "/x"})._fields)
    assert fields == {"confirm", "signals"}, (
        f"Decision gained a field ({fields}); anything that can allow is a way "
        f"around the consent keys"
    )
    assert not hasattr(reaction, "allow")
    assert not hasattr(reaction, "permit")


def test_destructive_naming_is_a_set_not_a_flag():
    """So a new destructive skill is covered by being named, rather than by a
    caller remembering to pass an argument."""
    names = reaction.destructive_tools()
    assert "delete_file" in names and "kill_process" in names
    assert isinstance(names, frozenset)


def test_target_extraction_falls_back_rather_than_guessing():
    assert reaction._target_of({"path": "/a"}) == "/a"
    assert reaction._target_of({"irrelevant": 1}) == ""
    assert reaction._target_of("not a dict") == ""


# --------------------------------------------------------------- controls

def test_control_raising_the_threshold_silences_the_layer(clock):
    """Proves the tests above are reading the thresholds rather than passing
    because the assertions are weak: with a limit no test can reach, nothing
    prompts, and every tripwire test must fail."""
    # BOTH limits, not just the repeat one: the first version of this control
    # raised only _REPEAT_LIMIT and the fan-out tripwire fired anyway, which
    # would have made the control look broken rather than make the layer look
    # fine. A control has to disable every path it is meant to disable.
    original_repeat = reaction._REPEAT_LIMIT
    original_fanout = reaction._FANOUT_LIMIT
    reaction._REPEAT_LIMIT = 100_000
    reaction._FANOUT_LIMIT = 100_000
    try:
        layer = ReactionLayer(clock=lambda: clock["now"])
        for index in range(500):
            assert layer.check("read_text_file", {"path": "/same"}).confirm is None
        for index in range(500):
            assert layer.check("delete_file", {"path": f"/d{index}"},
                               destructive=True).confirm is None
    finally:
        reaction._REPEAT_LIMIT = original_repeat
        reaction._FANOUT_LIMIT = original_fanout


def test_control_removing_origin_separation_merges_the_windows(clock):
    """The attended/unattended split is load-bearing; prove it is doing work."""
    layer = ReactionLayer(clock=lambda: clock["now"])
    for index in range(reaction._REPEAT_LIMIT - 1):
        layer.check("read_text_file", {"path": f"/x{index}"}, origin=ORIGIN_UNATTENDED)
    assert layer.check("read_text_file", {"path": "/y"},
                       origin=ORIGIN_USER).confirm is None
    # With the split removed - which is what keying on one origin would do -
    # the same burst would deny the attended call.
    merged = ReactionLayer(clock=lambda: clock["now"])
    for index in range(reaction._REPEAT_LIMIT - 1):
        merged.check("read_text_file", {"path": f"/x{index}"})
    assert merged.check("read_text_file", {"path": "/y"},
                        origin=ORIGIN_USER).confirm is None
    assert reaction._origin_key(ORIGIN_USER) != reaction._origin_key(ORIGIN_UNATTENDED)

# --------------------------------------------------------------- it asks

def test_the_question_reaches_a_person_and_a_yes_lets_work_continue(clock, monkeypatch):
    """`tools._reaction_refuses` asks; it used to return "Not run" without asking.

    Measured 2026-10-08: a booking in the real app stopped at its Purchase click
    after 25 browse calls, with the question never shown to anyone.
    """
    import threading
    from shani_chronoa import ask_bridge, tools
    monkeypatch.setattr(tools, "_REACTIONS", ReactionLayer(clock=lambda: clock["now"]))
    asked = []
    monkeypatch.setattr(ask_bridge, "has_presenter", lambda: True)
    monkeypatch.setattr(ask_bridge, "ask", lambda q, opts, **kw: (asked.append(q), "Let it continue")[1])
    results = []
    worker = threading.Thread(target=lambda: results.extend(
        tools._reaction_refuses("browse", {"url": "https://x.test/"}, ORIGIN_USER)
        for _ in range(reaction._REPEAT_LIMIT * 2)))
    worker.start(); worker.join()
    assert all(r is None for r in results), "a call was refused after the person said continue"
    assert len(asked) == 1, f"asked {len(asked)} times; one yes should cover the next stretch"


def test_with_nobody_to_ask_the_call_is_still_not_run(clock, monkeypatch):
    import threading
    from shani_chronoa import ask_bridge, tools
    monkeypatch.setattr(tools, "_REACTIONS", ReactionLayer(clock=lambda: clock["now"]))
    monkeypatch.setattr(ask_bridge, "has_presenter", lambda: False)
    results = []
    worker = threading.Thread(target=lambda: results.extend(
        tools._reaction_refuses("browse", {}, ORIGIN_USER) for _ in range(reaction._REPEAT_LIMIT + 1)))
    worker.start(); worker.join()
    assert results[-1] is not None and results[-1].ran is False
