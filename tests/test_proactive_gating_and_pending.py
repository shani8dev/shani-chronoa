"""Focused tests: rhythm gating is real, and held announcements survive.

Two pieces of proactive behaviour used to be inert: the daemon asked the rule
gate but nothing answered differently by state, and a held reflex announcement
vanished after one 900 s cooldown. These tests pin the decision, its wiring,
and the bounded queue that replaces the silent drop.
"""

import logging

import pytest

from shani_chronoa import daemon, reflex, rhythm, runner_lock
from shani_chronoa.senses.scheduler import AmbientScheduler


@pytest.fixture
def observed_state():
    """Drive the state the scheduler thread actually reads, then restore it."""
    original = rhythm.current_state()
    yield rhythm.note_state
    rhythm.note_state(original)


# ------------------------------------------------------------------ rhythm gate


def test_presence_states_run_routine_rules():
    # A quiet room is exactly when background rules are meant to fire, so IDLE
    # must never stop them. Nor should an active or merely awake machine.
    for state in (rhythm.Rhythm.ACTIVE, rhythm.Rhythm.AWAKE, rhythm.Rhythm.IDLE):
        assert rhythm.should_run_rules(state) is True
        assert rhythm.should_run_rules(rhythm.RhythmState(state, 0.0, 0.0)) is True


def test_routine_rules_wait_while_the_machine_is_already_working():
    for state in (rhythm.Rhythm.SLEEP, rhythm.Rhythm.DREAM, rhythm.Rhythm.MAINTENANCE):
        assert rhythm.should_run_rules(state) is False
        assert rhythm.should_run_rules(rhythm.RhythmState(state, 0.0, 0.0)) is False


def test_critical_and_user_armed_actions_are_never_suppressed():
    """The gate never silences an urgent event or an armed unattended action."""
    for state in rhythm.Rhythm:
        assert rhythm.should_run_rules(state, critical=True) is True
        assert rhythm.should_run_rules(state, armed=True) is True
        assert rhythm.should_run_rules(state, critical=True, armed=True) is True


def test_daemon_gate_follows_the_observed_state(observed_state):
    observed_state(rhythm.Rhythm.IDLE)
    assert daemon._rules_allowed() is True
    observed_state(rhythm.Rhythm.SLEEP)
    assert daemon._rules_allowed() is False
    # The same gate, asked about a critical or armed action, still says yes.
    assert daemon._rules_allowed(critical=True) is True
    assert daemon._rules_allowed(armed=True) is True


def test_the_scheduler_skips_rules_only_while_the_gate_is_closed(monkeypatch):
    """The daemon's decision reaches the thread that polls armed rules."""
    monkeypatch.setattr(runner_lock, "holds", lambda: True)

    class Engine:
        def __init__(self):
            self.polls = 0

        def poll(self):
            self.polls += 1
            return []

        def run_due(self):
            return []

    engine = Engine()
    allowed = [False]
    scheduler = AmbientScheduler(
        senses={},
        store=object(),
        event_engine=engine,
        rule_gate=lambda: allowed[0],
        reflex_layer=False,
    )
    scheduler._last_event_poll = 0.0
    assert scheduler._poll_event_rules(now=100.0) == []
    assert engine.polls == 0, "a held state still polled armed rules"

    allowed[0] = True
    scheduler._last_event_poll = 0.0
    scheduler._poll_event_rules(now=100.0)
    assert engine.polls == 1, "an allowed state did not poll armed rules"


# ------------------------------------------------------------- held announcements


@pytest.fixture
def clean_pending():
    reflex.clear_pending()
    yield
    reflex.clear_pending()


@pytest.fixture
def no_dnd(monkeypatch):
    monkeypatch.setattr(reflex, "_do_not_disturb", lambda: False)


def test_a_held_announcement_is_surfaced_later(no_dnd, clean_pending):
    urge = reflex.Urgence(True, reflex.URGENCY_HIGH, "Battery", "8 percent")
    assert reflex.announce([urge], rhythm_state=rhythm.Rhythm.SLEEP, now=100.0) == []
    assert [h.urge.subject for h in reflex.pending()] == ["Battery"]

    decided = reflex.announce([], rhythm_state=rhythm.Rhythm.AWAKE, now=160.0)
    assert [(u.subject, d) for u, d in decided] == [("Battery", "notify")]
    assert decided[0][0] is urge, "a different object was surfaced"
    assert reflex.pending() == (), "a surfaced announcement is still held"


def test_clear_pending_forgets_held_announcements(no_dnd):
    reflex.clear_pending()
    urge = reflex.Urgence(True, reflex.URGENCY_HIGH, "Battery", "8 percent")
    reflex.announce([urge], rhythm_state=rhythm.Rhythm.SLEEP, now=100.0)
    assert len(reflex.pending()) == 1

    reflex.clear_pending()
    assert reflex.pending() == ()
    assert reflex.announce([], rhythm_state=rhythm.Rhythm.AWAKE, now=101.0) == []


def test_the_pending_queue_stays_bounded(no_dnd, clean_pending, caplog):
    with caplog.at_level(logging.WARNING, logger=reflex.logger.name):
        for index in range(reflex._PENDING_CAPACITY + 10):
            reflex.announce(
                [reflex.Urgence(True, reflex.URGENCY_HIGH, f"Event {index}", "detail")],
                rhythm_state=rhythm.Rhythm.SLEEP,
                now=100.0 + index,
            )
    assert len(reflex.pending()) == reflex._PENDING_CAPACITY
    assert "queue was full" in caplog.text, "an eviction happened silently"


def test_an_expired_hold_is_logged_not_silently_dropped(no_dnd, clean_pending, caplog):
    urge = reflex.Urgence(True, reflex.URGENCY_HIGH, "Battery", "8 percent")
    reflex.announce([urge], rhythm_state=rhythm.Rhythm.SLEEP, now=100.0)
    with caplog.at_level(logging.WARNING, logger=reflex.logger.name):
        decided = reflex.announce(
            [],
            rhythm_state=rhythm.Rhythm.AWAKE,
            now=100.0 + reflex._PENDING_TTL_SECONDS + 1.0,
        )
    assert decided == []
    assert reflex.pending() == ()
    assert "TTL" in caplog.text, "an expired notable event vanished silently"


def test_the_pending_ttl_is_not_the_notify_cooldown():
    # One cooldown is how often a condition may re-notify; the hold window is
    # how long a notable event waits for a room that will let it speak.
    assert reflex._PENDING_TTL_SECONDS > reflex._COOLDOWN_SECONDS


def test_critical_notifications_survive_a_closed_rule_gate(monkeypatch):
    """The gate parks routine rules only; a reflex still reaches the person."""
    urged = [reflex.Urgence(True, reflex.URGENCY_HIGH, "Battery", "8 percent")]

    class Reflexes:
        def consolidate(self):
            return None

        def due(self):
            return urged

    sent = []
    monkeypatch.setattr(reflex, "notify", lambda urges: sent.append(urges))
    scheduler = AmbientScheduler(
        senses={},
        store=object(),
        rule_gate=lambda: False,
        reflex_layer=Reflexes(),
    )
    scheduler._reflex_tick()
    assert sent == [urged], "a quiet rhythm silenced a critical notification"
