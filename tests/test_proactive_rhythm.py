"""Rhythm: a state machine that keeps the daemon's rules running."""

from shani_chronoa import rhythm


def test_the_states_are_the_documented_six():
    assert {s.value for s in rhythm.Rhythm} == {
        "active",
        "awake",
        "idle",
        "sleep",
        "dream",
        "maintenance",
    }


def test_rules_run_in_presence_states_and_wait_in_quiet_ones():
    # A quiet room is exactly when most background rules are meant to fire, so
    # IDLE must never stop them. Only states where the machine is already doing
    # the work (sleep, an offline dream, maintenance) hold routine rules.
    assert all(rhythm.should_run_rules(s) for s in (
        rhythm.Rhythm.ACTIVE, rhythm.Rhythm.AWAKE, rhythm.Rhythm.IDLE,
    ))
    assert not any(rhythm.should_run_rules(s) for s in (
        rhythm.Rhythm.SLEEP, rhythm.Rhythm.DREAM, rhythm.Rhythm.MAINTENANCE,
    ))


def test_recent_input_is_active_and_idle_time_is_not_guessed():
    start = rhythm.RhythmState(rhythm.Rhythm.AWAKE, 0.0, 0.0)
    assert rhythm.observe(start, idle_seconds=1.0, now=10).state is rhythm.Rhythm.ACTIVE
    # No idle reading at all falls through to AWAKE, never to "someone is here".
    # The previous state carries the last known clock; an unknown clock is not 0.
    unknown = rhythm.observe(
        rhythm.RhythmState(rhythm.Rhythm.AWAKE, 0.0, rhythm.ACTIVE_IDLE_SECONDS + 1),
        now=10,
    )
    assert unknown.state is rhythm.Rhythm.AWAKE
    assert unknown.idle_seconds >= rhythm.ACTIVE_IDLE_SECONDS


def test_long_idle_and_a_locked_screen_mean_sleep():
    start = rhythm.RhythmState(rhythm.Rhythm.AWAKE, 0.0, 0.0)
    quiet = rhythm.observe(start, idle_seconds=400, now=10)
    assert quiet.state is rhythm.Rhythm.IDLE
    asleep = rhythm.observe(quiet, idle_seconds=4000, locked=True, now=20)
    assert asleep.state is rhythm.Rhythm.SLEEP


def test_sleep_outweaks_even_a_maintenance_request():
    start = rhythm.RhythmState(rhythm.Rhythm.AWAKE, 0.0, 0.0)
    got = rhythm.observe(start, asleep=True, maintenance=True, now=1)
    assert got.state is rhythm.Rhythm.SLEEP


def test_a_state_change_stamps_the_time_and_an_unchanged_one_does_not():
    start = rhythm.RhythmState(rhythm.Rhythm.AWAKE, 0.0, 10.0)
    got = rhythm.observe(start, idle_seconds=0, now=100)
    assert got.state is rhythm.Rhythm.ACTIVE and got.since == 100
    again = rhythm.observe(got, idle_seconds=1, now=200)
    assert again.since == 100


def test_the_percept_names_the_state_and_carries_its_provenance():
    state = rhythm.RhythmState(rhythm.Rhythm.IDLE, 5.0, 400.0)
    p = rhythm.percept(state, now=10)
    assert p.sense == "rhythm"
    assert p.content == "rhythm: idle"
    assert p.metadata == {"since": 5.0, "idle_seconds": 400.0}
    assert p.sensitivity == "personal"


def test_the_monitor_publishes_only_on_a_transition():
    class Store:
        def __init__(self):
            self.added = []

        def add(self, percept):
            self.added.append(percept)

    store = Store()
    monitor = rhythm.RhythmMonitor(store=store, read_idle=lambda: 1)
    assert monitor.tick().state is rhythm.Rhythm.ACTIVE
    assert len(store.added) == 1
    monitor.tick()
    assert len(store.added) == 1  # same state, no new percept
    monitor.read_idle = lambda: 500
    assert monitor.tick().state is rhythm.Rhythm.IDLE
    assert len(store.added) == 2


def test_an_unreadable_idle_clock_does_not_stop_the_monitor():
    def broken():
        raise OSError("no X display")

    monitor = rhythm.RhythmMonitor(read_idle=broken)
    assert monitor.tick().state is rhythm.Rhythm.AWAKE
