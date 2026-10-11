"""What's running: the stop that existed and nothing called, and the reaper.

Two measured findings behind this file.

**1. Six background watchers were started and could not be stopped.**
`app/application.py` starts the incoming-call watcher, the message watcher, the
file-sharing receiver, the watch companion, the phone-keyboard service and the
Bluetooth music remote unconditionally at startup. Each is written with a
`stop()`. A scan of the whole tree found that stop called by **nothing**, and a
grep over `gui/` and `app/tray.py` found none of the six named anywhere on
screen. So a machine could hold a Bluetooth link, a watch connection or a
microphone open for as long as the app ran, with quitting as the only lever -
which is the dead-code class this repo records four times, in the shape that
hurts.

**2. The child supervisor could see a hung child and had nothing to do about it.**
`check_heartbeats` reports `DISCONNECTED` - "alive but silent past the budget:
hung" - and its own docstring says "waiting longer for a hung one is pointless".
Every caller in the tree only *reads* that status. The cost was measured on this
box on 2026-10-11: two `whisper-cli` runs up 44 and 2 minutes on 3-4 second WAVs
held 4.7 GB each, and killing four PIDs took available memory from 9 GB to 18 GB.
Those were **not** Chronoa's children and no supervisor could have reached them -
a process may only signal its own descendants - but Chronoa's own hung child was
already visible to the supervisor and nothing acted on it.

`reap_hung()` closes that loop, guarded two ways because pid reuse is real: it
re-probes immediately before the signal, and only a child the supervisor is
tracking is ever signalled.
"""

from __future__ import annotations

import subprocess
import sys
import time
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "usr" / "lib"))

from shani_chronoa import background  # noqa: E402
from shani_chronoa import child_supervisor  # noqa: E402
from shani_chronoa.child_supervisor import ChildState  # noqa: E402


class _Stopper:
    """A component that can be stopped and says so."""

    def __init__(self, said: str = "stopped quietly"):
        self.said = said
        self.calls = 0

    def stop(self):
        self.calls += 1
        return self.said


class _Waker(_Stopper):
    def is_listening(self):
        return True


class _Barger(_Stopper):
    def is_monitoring(self):
        return True


class _Sched(_Stopper):
    def stop(self, timeout: float = 5.0):
        return None


def _app() -> type:
    """An app shaped like the real one: every attribute the inventory reads."""
    return type("A", (), {
        "wakeword": _Waker(),
        "barge_in_monitor": _Barger(),
        "incoming_calls": _Stopper(),
        "message_watcher": _Stopper(),
        "obex_receiver": _Stopper(),
        "watch_companion": _Stopper(),
        "phone_remote": _Stopper(),
        "avrcp_target": _Stopper(),
        "sense_scheduler": _Sched(),
    })()


class TestTheStopThatExistedAndNothingCalled:
    def test_all_six_watchers_get_a_stop(self):
        """**The finding.** Each of the six has a stop() in the component, so the
        inventory must reach it - a row with a dead button is the dead-control
        class this file exists to prevent.
        """
        inv = background.inventory(_app())
        for key in ("incoming-calls", "messages", "obex", "watch", "remote",
                    "avrcp"):
            row = inv.by_key(key)
            assert row is not None, f"{key} is missing from the inventory"
            assert row.stop is not None, (
                f"{row.label} has a stop() in its own component and the inventory "
                f"did not reach it, so the panel would show a dead button")
        assert len(inv.stoppable()) >= 9, (
            f"only {len(inv.stoppable())} of the started things can be stopped")

    def test_the_microphone_comes_first(self):
        """Ordering is the feature: it is what somebody came to turn off."""
        inv = background.inventory(_app())
        assert inv.items[0].key == "wake-word", (
            f"the first row is {inv.items[0].label!r}, not the microphone")
        assert inv.items[1].key == "barge-in", (
            "talk-over-the-reply holds the microphone a second time and must be "
            "next to it, not four rows down")

    def test_the_model_server_is_in_the_inventory(self):
        """The 1.09 GB `llama-server` measured on this box, up 7h36m."""
        assert background.inventory(_app()).by_key("model") is not None

    def test_an_app_with_no_attributes_is_not_a_crash(self):
        """A surface built from a stub, or from an app before `_init_components`
        has run, must still render - with rows that say they are not running
        rather than an AttributeError on the way out."""
        inv = background.inventory(type("A", (), {})())
        assert inv.items, "an app with no attributes read as 'nothing exists'"
        assert inv.by_key("model").running in (True, False)

    def test_a_row_says_what_it_is_holding_in_a_persons_words(self):
        """"a D-Bus signal subscription" is not a reason somebody can act on."""
        inv = background.inventory(_app())
        for row in inv.items:
            assert row.holds, f"{row.label} does not say what it is holding"
            assert row.holds[0].isupper() or row.holds[0] in "yt", (
                f"{row.label} holds {row.holds!r}, which is not a person's word")

    def test_a_component_with_no_stop_says_so_rather_than_showing_a_button(self):
        """**The rule the row is held to.** Where there genuinely is no stop, the
        row must be able to say it - not render a control that does nothing."""
        row = background.Running("x", "A thing", "your microphone", None,
                                 why_not="this one cannot be stopped at all")
        assert row.request_stop() == "this one cannot be stopped at all"
        assert row.stop is None, "a row with no stop must not grow one"


class TestTheStopReportsWhatItObserved:
    def test_a_stop_that_worked_marks_the_row_stopped(self):
        app = _app()
        inv = background.inventory(app)
        row = inv.by_key("barge-in")
        assert row.running is True
        sentence = row.request_stop()
        assert row.running is False
        assert "quietly" in sentence

    def test_a_stop_that_raises_does_not_crash_the_panel_and_is_not_a_success(self):
        """**A stop that was requested is not a stop that happened.** A row that
        read "stopped" after a `systemctl` failure would be the confident wrong
        answer this whole module exists to prevent.
        """
        def angry():
            raise RuntimeError("systemctl said no")

        row = background.Running("x", "A thing", "your microphone", angry,
                                 running=True)
        sentence = row.request_stop()
        assert row.running is True, "a failed stop left the row reading stopped"
        assert "Could not stop" in sentence, sentence
        assert "RuntimeError" in sentence

    def test_force_stop_all_returns_the_rows_that_could_not_be_stopped(self):
        """**The negative half.** A button that stops eight of ten and reports
        "stopped" has reported a success that is partial - ECC's #2463 lesson,
        applied to the machine rather than to a skill.
        """
        app = _app()
        inv = background.inventory(app)
        for key in ("watch", "remote"):
            inv.by_key(key).stop = None
            inv.by_key(key).why_not = "held by the watch's own app"
        before = len(inv.stoppable())
        said, refused = inv.force_stop_all()
        assert before == 8, before
        assert len(refused) == 2, [r.label for r in refused]
        assert {r.label for r in refused} == {"Watch companion",
                                             "Chronoa Remote (phone keyboard)"}
        assert len(said) == 8, said
        assert all(not r.running for r in inv.items if r.stop), (
            "a stop that succeeded left its row reading running")

    def test_a_stop_that_succeeded_then_failed_does_not_read_stopped(self):
        """**Why the failed-stop path restores `running` at all.**

        The line looks redundant - the row was already running - and it is, for
        the *first* call. It is not for a retry: a stop that worked, and then a
        second attempt that fails, must leave the row reading running again.
        Without the restore the row reads "stopped" about a thing that is still
        up, which is the confident wrong answer in reverse.
        """
        calls = {"n": 0}

        def flaky():
            calls["n"] += 1
            if calls["n"] == 1:
                return "stopped the first time"
            raise RuntimeError("the retry failed")

        row = background.Running("x", "A thing", "your microphone", flaky,
                                 running=True)
        assert "first time" in row.request_stop()
        assert row.running is False, "the first stop did not take"
        assert "Could not stop" in row.request_stop()
        assert row.running is True, (
            "a second, failing stop left the row reading stopped about a thing "
            "that is still up")

    def test_the_wake_word_row_is_not_claimed_running_when_idle(self):
        """A listener object that is *not listening* must not be shown as
        holding the microphone. The first version read `bool(wake)`, which is
        true for every app that has ever constructed one - so the row would
        claim the microphone is held on a machine where it is not.
        """
        class _Idle:
            def is_listening(self):
                return False

            def stop(self):
                return "stopped"

        app = _app()
        app.wakeword = _Idle()
        row = background.inventory(app).by_key("wake-word")
        assert row.stop is not None, "an idle listener lost its stop"
        assert row.running is False, (
            "the row claims the microphone is held by a listener that is not "
            "listening")

    def test_force_stop_all_answers_for_a_stop_that_failed(self):
        """**The difference between the two ways a row can be unstoppable.**

        The first version only covered `stop is None`. The other way is a stop
        that exists, runs, and *fails* - and a force-stop that folds those into
        its success list is a green dashboard over a broken writer, which is the
        ECC #2463 lesson applied to the machine.
        """
        app = _app()

        def angry():
            raise RuntimeError("systemctl said no")

        app.message_watcher = type("Angry", (), {"stop": staticmethod(angry)})()
        inv = background.inventory(app)
        said, refused = inv.force_stop_all()
        # Ten things run here - the nine on the app plus the local model server,
        # which is genuinely answering on this box - and one of them fails.
        assert len(said) == 9, [each for each in said]
        assert [r.label for r in refused] == ["Message watcher"], (
            f"a stop that failed was reported as one that worked: {refused}")

    def test_the_summary_names_the_negative_half(self):
        """"Nothing is running" is a claim about *every* component the inventory
        knows, so a summary that omits the unstoppable ones is a lie by omission.

        **Built from a component that genuinely has no stop**, not from a stop
        this test removed afterwards: the first version set `stop = None` on the
        object `inventory(app)` returned and then called `summary(app)`, which
        builds its own - so the mutation was invisible and the test passed
        against a change it never made.
        """
        app = _app()
        # A companion that is connected and cannot be stopped from here.
        app.watch_companion = type("NoStop", (), {})()
        app.phone_remote = type("NoStop", (), {})()
        text = background.summary(app)
        assert "no way to stop" in text, (
            f"the summary does not name what it could not stop: {text!r}")
        assert "Watch companion" in text, (
            f"the summary says something is unstoppable without saying what: "
            f"{text!r}")

    def test_a_summary_that_omits_the_negative_half_is_refused(self):
        """The control: a summary that only ever lists what it stopped is the
        ECC #2463 dashboard, and this is the assertion that fails when someone
        drops the second half to make the sentence tidier.
        """
        app = _app()
        app.watch_companion = type("NoStop", (), {})()
        inv = background.inventory(app)
        stuck = [item.label for item in inv.items
                 if item.running and item.stop is None]
        assert stuck, "the fixture no longer has an unstoppable row"
        assert background.summary(app).count("no way to stop") == 1


class TestTheReaper:
    """`reap_hung()` against a **real** process, because a fake one cannot hang."""

    def _track(self, name: str, proc, stall: float) -> None:
        child_supervisor.ChildSupervisor().track_child(
            name, proc.poll, pid=proc.pid, stall_after=stall)

    def supervisor(self) -> None:
        pass

    def test_a_hung_child_is_seen_as_hung(self):
        proc = subprocess.Popen(["sleep", "300"])
        try:
            sup = child_supervisor.ChildSupervisor()
            sup.track_child("hung", proc.poll, pid=proc.pid, stall_after=0.2)
            deadline = time.monotonic() + 5
            while time.monotonic() < deadline:
                states = {s.name: s.state for s in sup.check_heartbeats()}
                if states.get("hung") is ChildState.DISCONNECTED:
                    break
                time.sleep(0.05)
            else:
                pytest.fail("a child past its silence budget was never reported "
                            "as hung")
        finally:
            proc.kill()
            proc.wait()

    def test_reaping_a_hung_child_really_kills_it(self):
        """Driven against a real `sleep` so the kill is a kill and not a stub's
        return value. `sleep` writes nothing to a pipe this code reads, so it is
        silent in exactly the way the budget measures.
        """
        proc = subprocess.Popen(["sleep", "300"])
        sup = child_supervisor.ChildSupervisor()
        sup.track_child("hung", proc.poll, pid=proc.pid, stall_after=0.2)
        try:
            deadline = time.monotonic() + 5
            while time.monotonic() < deadline:
                if any(s.state is ChildState.DISCONNECTED
                       for s in sup.check_heartbeats()):
                    break
                time.sleep(0.05)
            reaped = sup.reap_hung(grace=2.0)
            assert reaped == ["hung"], (
                f"a hung child was not reaped: {reaped}")
            proc.wait(timeout=5)
            assert proc.returncode is not None, "the reaped child is still alive"
        finally:
            if proc.poll() is None:
                proc.kill()
            proc.wait()

    def test_a_healthy_child_is_never_signalled(self):
        """**The over-fix.** Reaping everything that is silent would kill a slow
        child that is still working - and the reproduction of that here is a child
        that *has* spoken, so it must be left alone.
        """
        proc = subprocess.Popen(["sleep", "300"])
        sup = child_supervisor.ChildSupervisor()
        sup.track_child("healthy", proc.poll, pid=proc.pid, stall_after=30.0)
        try:
            sup.update_heartbeat("healthy")
            assert sup.reap_hung() == [], "a healthy child was signalled"
            assert proc.poll() is None, "a healthy child was killed"
        finally:
            proc.kill()
            proc.wait()

    def test_a_child_with_no_pid_is_left_alone(self):
        """A pid-less stand-in must never take an unrelated process down."""
        proc = subprocess.Popen(["sleep", "300"])
        sup = child_supervisor.ChildSupervisor()

        def alive_forever():
            return None

        sup.track_child("nopid", alive_forever, pid=None, stall_after=0.1)
        try:
            time.sleep(0.3)
            assert sup.reap_hung() == [], "a child with no pid was signalled"
            assert proc.poll() is None, "the unrelated process was killed"
        finally:
            proc.kill()
            proc.wait()

    def test_a_child_that_exits_before_the_signal_is_not_signalled(self, caplog):
        """**The pid-reuse guard.** One that died on its own between the check and
        the kill must not be signalled: the pid may already belong to a stranger.

        Asserted on the log, because the *observable* difference is otherwise
        nothing - `os.kill` on a dead pid raises OSError, which the reaper already
        catches, so `reap_hung()` returns the same empty list either way. The
        guard's whole value is that it declines before trying, and the only place
        that shows up is the sentence it logs.
        """
        calls = {"n": 0}

        def dies_between_the_check_and_the_signal():
            """Alive when `check_heartbeats` looks, gone a moment later.

            That is the pid-reuse race in one object: the supervisor has already
            decided this child is hung, and by the time it reaches for the signal
            the pid may belong to somebody else entirely.
            """
            calls["n"] += 1
            # **Alive for the first two questions, not the third.** The
            # supervisor checks once here, once more inside `reap_hung` before it
            # reaches for the signal, and it is the reaper's own re-probe that
            # must find the child gone. Returning dead on the *second* call makes
            # the reaper see EXITED and skip the guard entirely - which is a
            # fixture that cannot exercise the thing it names.
            return None if calls["n"] <= 2 else -9

        # **A real child, not this test's own pid.** The first version passed
        # `os.getpid()`, on the reasoning that the guard declines before any
        # signal - which is exactly what it does *while the guard is present*.
        # Remove the guard and the reaper SIGTERMs the pytest process: the run
        # dies with no summary and the mutation is reported as an unmeasurable
        # error rather than as the break it is. A throwaway `sleep` makes the
        # outcome observable - alive means the guard worked, dead means it did not.
        doomed = subprocess.Popen(["sleep", "300"])
        try:
            sup = child_supervisor.ChildSupervisor()
            sup.track_child("gone", dies_between_the_check_and_the_signal,
                            pid=doomed.pid, stall_after=0.2)
            # Let the silence budget pass, so the supervisor really reaches the
            # hung state the guard protects. `stall_after=0.0` looks equivalent
            # and is not: the branch is `stall_after and age > it`, so a zero
            # budget is falsy and this child is never called hung at all.
            time.sleep(0.35)
            assert any(s.state is ChildState.DISCONNECTED
                       for s in sup.check_heartbeats()), (
                "the fixture never reached the hung state, so the guard was not "
                "exercised at all")
            with caplog.at_level("INFO", logger="shani_chronoa.child_supervisor"):
                assert sup.reap_hung() == [], (
                    "a child that had exited was signalled anyway")
            assert "exited on its own" in caplog.text, (
                "the re-probe guard did not run: without it the reaper signals a "
                "pid that may already belong to a stranger")
            # **And the child is still alive**, which is the observable half. A
            # guard that logged the right sentence and signalled anyway would
            # satisfy the assertion above and still be the bug.
            assert doomed.poll() is None, (
                "the reaper signalled a child that had already exited on its own")
        finally:
            doomed.kill()
            doomed.wait()
