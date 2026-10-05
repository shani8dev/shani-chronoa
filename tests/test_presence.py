"""Presence: Active → Drowsy → Sleeping, and the claim each state makes.

`assistd` has three states and one key cycles them, so a laptop can stop holding
a model resident. Chronoa could not do it at all - `local_llm` had
`start_service()` and no `stop_service()`, and setup ran
`systemctl --user enable --now`, so from the first login the unit came up on its
own and stayed up.

**Drowsy here does not claim to unload weights**, because nothing here can. It
is: listening, model not resident, next question loads it cold. Sleeping is the
same but waits to be asked. The distinction a person wants between "let go of the
memory for now" and "leave the machine alone" - and a state that lied about
holding memory would be the exact failure this repository documents.
"""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "usr/lib/shani-chronoa"))

from shani_chronoa import local_llm, presence  # noqa: E402


# ---------------------------------------------------------------------------
# the cycle
# ---------------------------------------------------------------------------


def test_the_cycle_is_active_drowsy_sleeping_and_back():
    """`assistd`'s order, and one key reaches all three."""
    assert presence.Presence.ACTIVE.next() is presence.Presence.DROWSY
    assert presence.Presence.DROWSY.next() is presence.Presence.SLEEPING
    assert presence.Presence.SLEEPING.next() is presence.Presence.ACTIVE
    reached = {presence.Presence.ACTIVE}
    state = presence.Presence.ACTIVE
    for _ in range(2):
        state = state.next()
        reached.add(state)
    assert reached == set(presence.Presence), "a state is unreachable by cycling"


def test_every_state_names_its_next_action_and_its_cost():
    for state in presence.Presence:
        assert state.action(), f"{state} has no action"
        assert state.detail(), f"{state} explains nothing"
        assert state.label(), f"{state} has no label"


def test_drowsy_does_not_claim_to_unload_weights():
    """The wording is the claim. It says the model is *not resident*, because
    `local_llm` cannot unload weights - it can only stop the server."""
    detail = presence.Presence.DROWSY.detail().lower()
    assert "not loaded" in detail or "not resident" in detail
    for forbidden in ("unload", "frees the memory", "released the weights"):
        assert forbidden not in detail, (
            f"Drowsy claims {forbidden!r}, which nothing here can do")


def test_the_button_says_what_it_will_do_not_what_is_true():
    """A button reading "Drowsy" is a button whose meaning depends on the machine's
    current state, which is the wrong way round."""
    for state in presence.Presence:
        action = state.action().lower()
        assert state.name.lower() not in action, (
            f"{state}'s button reads like a state, not an action")


# ---------------------------------------------------------------------------
# detection reads the machine, not a remembered flag
# ---------------------------------------------------------------------------


def test_detection_follows_the_server_not_a_flag():
    up = {"value": True}
    assert presence.detect(lambda: up["value"]) is presence.Presence.ACTIVE
    up["value"] = False
    assert presence.detect(lambda: up["value"]) is presence.Presence.DROWSY


def test_detection_treats_a_stopped_server_as_drowsy_not_sleeping():
    """Two absent states, one observable thing. `Drowsy` is the honest default,
    because it is the one that still answers a question - and pretending otherwise
    would make a released model look broken."""
    assert presence.detect(lambda: False) is presence.Presence.DROWSY


# ---------------------------------------------------------------------------
# applying a state, and refusing to claim one that did not happen
# ---------------------------------------------------------------------------


def test_waking_starts_the_server_and_says_so():
    calls = []

    def wake():
        calls.append("wake")
        return ""

    ok, reason = presence.apply(presence.Presence.ACTIVE,
                                is_up=lambda: False, wake=wake, sleep=lambda: "")
    assert ok and reason == "loaded"
    assert calls == ["wake"]


def test_a_wake_that_fails_does_not_report_itself_as_loaded():
    """This is the whole point: a presence that disagrees with the machine is
    worse than no presence, because it reports memory held that is not."""
    ok, reason = presence.apply(presence.Presence.ACTIVE, is_up=lambda: False,
                                wake=lambda: "llama.cpp is not installed",
                                sleep=lambda: "")
    assert ok is False
    assert "llama.cpp" in reason


def test_a_failure_to_release_is_reported_and_not_counted_as_released():
    ok, reason = presence.apply(presence.Presence.DROWSY, is_up=lambda: True,
                                wake=lambda: "",
                                sleep=lambda: "systemctl is not available")
    assert ok is False
    assert "systemctl" in reason


def test_releasing_when_already_released_is_not_an_error():
    """Pressing the button twice must not look like a failure."""
    ok, reason = presence.apply(presence.Presence.DROWSY, is_up=lambda: False,
                                wake=lambda: "", sleep=lambda: "should not be called")
    assert ok and "already" in reason


def test_releasing_stops_the_server():
    calls = []
    presence.apply(presence.Presence.SLEEPING, is_up=lambda: True,
                   wake=lambda: "", sleep=lambda: calls.append("stop") or "")
    assert calls == ["stop"]


# ---------------------------------------------------------------------------
# the lever exists at all
# ---------------------------------------------------------------------------


def test_local_llm_can_stop_what_it_starts():
    """Without this the whole cycle is decoration: `start_service()` existed and
    nothing could undo it, so the unit came up at login and stayed up."""
    import inspect

    source = inspect.getsource(local_llm)
    assert "def start_service" in source
    assert "def stop_service" in source, (
        "there is still no way to release the model")
    stop = inspect.getsource(local_llm.stop_service)
    assert '"stop"' in stop, "it does not actually stop anything"
    # Deliberately not `disable`: the unit must stay enabled so a question can
    # start it again, which is what makes drowsy mean released, not gone.
    assert '"disable"' not in stop, (
        "disabling the unit would leave a machine whose assistant never answers")


def test_a_question_wakes_a_released_model(monkeypatch):
    """Otherwise releasing it is not a state, it is a brick."""
    import inspect

    from shani_chronoa.app import brain

    source = inspect.getsource(brain.BrainMixin._use_local_server)
    assert "start_service" in source, (
        "a released model is never brought back, so 'free the model' means "
        "'break the assistant'")
    assert "SLEEPING" in source, (
        "and it must respect an explicit choice to stay asleep")


def test_waking_can_be_refused_for_callers_that_must_not_start_anything():
    """Checking whether a model is present is not a reason to load one."""
    import inspect

    from shani_chronoa.app import brain

    signature = inspect.signature(brain.BrainMixin._use_local_server)
    assert "wake" in signature.parameters, (
        "there is no way to ask without starting the server")
    assert signature.parameters["wake"].default is True