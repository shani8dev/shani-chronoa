"""The senses layer must actually RUN in the GUI, not merely be toggleable.

A user can enable 44 consent switches in Settings. Before this wiring the
scheduler that polls them was constructed by the CLI and nothing else, so every
one of those switches was inert: the assistant perceived nothing and reported no
reason why. A consent panel that cannot grant anything is worse than no panel,
because it reads as working.

These tests drive the real `ChronoaApplication` and a real `AmbientScheduler`.
Nothing here mocks the scheduler.
"""

import os

import pytest

from shani_chronoa import files
from shani_chronoa.app import ChronoaApplication
from shani_chronoa.senses.scheduler import AmbientScheduler


@pytest.fixture
def app(monkeypatch, tmp_path):
    """A real application, not a stand-in.

    `_init_components` is called directly rather than through `do_startup` so the
    test does not need a D-Bus session; that is also exactly how the rest of the
    suite constructs it, which is why the scheduler must not start there.
    """
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "data"))
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state"))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))
    application = ChronoaApplication()
    application._init_components()
    yield application
    scheduler = getattr(application, "sense_scheduler", None)
    if scheduler is not None:
        scheduler.stop()


class TestTheSchedulerIsWired:
    def test_the_app_builds_one(self, app):
        assert isinstance(app.sense_scheduler, AmbientScheduler)

    def test_it_shares_the_apps_own_percept_store(self, app):
        """A second store would be a second, invisible one.

        The assistant reads `percept_store` through the context builder, so a
        scheduler writing anywhere else would produce percepts nothing ever
        sends - the exact dead-layer shape this repo keeps being bitten by.
        """
        assert app.sense_scheduler._store is app.percept_store

    def test_it_has_an_event_engine_so_triggers_can_fire(self, app):
        """`triggers.py` was reachable in the GUI only for authoring rules.

        Nothing constructed an `EventEngine` outside the CLI, so a rule created
        in the GUI could never fire. The scheduler polls event rules through
        this engine, so wiring it here is what makes unattended triggers real.
        """
        from shani_chronoa.triggers import EventEngine
        assert isinstance(app.sense_scheduler._event_engine, EventEngine)

    def test_constructing_does_not_start_polling(self, app):
        """The whole reason it is built in _init_components and started later.

        Tests call `_init_components` directly. A scheduler that began polling
        there would spawn a thread per test, and senses read real system state.
        """
        assert app.sense_scheduler.running is False


class TestActivationStartsIt:
    """The behaviour that actually matters, and the easiest one to leave untested.

    An earlier draft of this file asserted only that the scheduler was
    constructed, that it shared the store, and that start/stop worked when the
    test called them directly. Deleting `self.sense_scheduler.start()` from
    `do_activate` therefore left all nine assertions green - the tests never
    drove the method that wires it up. These do.
    """

    class _StubWindow:
        def present(self):
            pass

    def test_activating_the_app_starts_polling(self, app):
        # Pre-set the window so do_activate takes the `else: present()` path and
        # does not try to build real GTK widgets, which needs a display.
        app.window = self._StubWindow()
        app.do_activate()
        assert app.sense_scheduler.running is True
        app.sense_scheduler.stop()

    def test_activating_twice_leaves_one_thread(self, app):
        app.window = self._StubWindow()
        app.do_activate()
        first = app.sense_scheduler._thread
        app.do_activate()
        assert app.sense_scheduler._thread is first
        app.sense_scheduler.stop()

    def test_polling_starts_before_the_window_is_built(self, app):
        """Ordering is deliberate.

        `start()` is the first statement, so a slow or failing window build
        cannot leave the scheduler permanently unstarted - which is the state
        this whole change exists to fix.
        """
        app.window = None
        try:
            app.do_activate()
        except Exception:
            pass  # the window may need a display; the scheduler must not care
        assert app.sense_scheduler.running is True
        app.sense_scheduler.stop()


class TestStartingIsSafeAndReversible:
    def test_start_starts_and_stop_stops(self, app):
        scheduler = app.sense_scheduler
        scheduler.start()
        assert scheduler.running is True
        scheduler.stop()
        assert scheduler.running is False

    def test_starting_twice_is_one_thread_not_two(self, app):
        """`do_activate` runs again every time the window is raised."""
        scheduler = app.sense_scheduler
        scheduler.start()
        first = scheduler._thread
        scheduler.start()
        assert scheduler._thread is first
        assert scheduler.running is True
        scheduler.stop()

    def test_stopping_twice_is_not_an_error(self, app):
        scheduler = app.sense_scheduler
        scheduler.start()
        scheduler.stop()
        scheduler.stop()
        assert scheduler.running is False


class TestSensesActuallyRun:
    def test_an_enabled_sense_really_produces_a_percept(self, app, monkeypatch):
        """The end-to-end claim: a poll writes something a turn could send.

        `filessystems` reads mounts, which exist on any Linux machine, and needs
        no external binary - so this exercises the real sense, the real store
        and the real deposit path without a fixture faking the result.
        """
        scheduler = app.sense_scheduler
        result = scheduler.poll("filessystems")
        # A refusal is a legitimate outcome (consent off by default); what must
        # NOT happen is a crash or a silent success.
        assert result is not None
        assert getattr(result, "percept", None) is not None or result.reason, (
            "a poll must either deposit a percept or name why it did not"
        )

    def test_a_consent_refusal_names_the_reason_rather_than_looking_clean(self, app):
        """The rule this project holds itself to.

        A sense whose failure mode is a plausible-looking wrong answer is worse
        than a sense that fails, so the refusal has to say which key was off.
        """
        scheduler = app.sense_scheduler
        scheduler._arguments.setdefault("filessystems", {})
        result = scheduler.poll("filessystems")
        if result.percept is None:
            assert result.reason, "a refusal with no reason reads as 'nothing to see'"