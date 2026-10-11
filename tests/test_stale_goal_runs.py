"""A run that stopped without finishing, and the panel that said "in progress".

oh-my-openagent's `boulder-state` (packages/boulder-state, #8413) states the
defect exactly:

> `completeBoulder` is the only completion transition, so a work whose session
> died abnormally would stay `active` forever.

Chronoa had the identical hole with no name for it. A run mid-`advance` when the
process died keeps `phase=RUNNING` with a frozen `updated_at`, and the panel
rendered `_PHASE_WORD[RUNNING]` - "in progress" - forever. Measured before this
change, through the real store and the real panel: a run last touched **30 days
ago** still said "in progress - 1 of 2 steps".

**What is taken, and the four rules that make it safe.** Boulder demotes an
`active` work to `paused` and stamps `stale_since`; its readers are unchanged;
`selectActiveWork` returns a stamped work to `active` and drops the stamp; and
"a work with no activity evidence at all is stale". All four are here:

- only RUNNING runs are considered (`AWAITING` may be weeks old legitimately);
- a run already stamped is left exactly as it is, so re-stamping cannot move a
  run's stopped-time forward on every refresh;
- nothing stale means nothing written - the list comes back with the same
  objects, so a store write happens only when something changed;
- a threshold of zero or less is ignored, because it would mark every live run
  as stopped the instant it was created.

**The part that is Chronoa's own, and the reason `phase` was not reused.**
`Phase.AWAITING` means "parked, somebody outside has to answer", which is not
what a dead run is; a new `Phase` would ripple through `_PHASE_WORD`, `LIVE`,
`manage_goals` and every reader. So the stamp rides on a new `stale_since` field,
`phase` stays RUNNING, and only the sentence a person reads changes.

**And the trap the write itself sets.** `GoalStore.save` stamps `updated_at` -
which is the *activity* signal the reconciliation reads - so saving a demotion
would reset it and the card would read "last moving in the last hour" about a run
dead for a month. Measured without `stamp=False`. `advance` is the opposite: it
*is* activity, so it stamps.
"""

from __future__ import annotations

import sys

sys.path.insert(0, "usr/lib/shani-chronoa")

import time  # noqa: E402

import pytest  # noqa: E402

from shani_chronoa import goals as G  # noqa: E402

gi = pytest.importorskip("gi")
gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")

NOW = 1_000_000.0
DAY = 86400.0
HOUR = 3600.0
THRESHOLD = 6 * HOUR


def _step(name, depends=()):
    return G.PlannedStep(thought=name, tool_name="t", tool_arguments={},
                         variable_name=name, depends_on=depends)


def _run(index=1, phase=G.Phase.RUNNING, created=None, updated=None, stale=0.0):
    return G.GoalRun(
        id="r", goal="book a flight", phase=phase,
        steps=(_step("a"), _step("b")), index=index,
        created_at=NOW if created is None else created,
        updated_at=NOW if updated is None else updated,
        stale_since=stale,
    )


@pytest.fixture
def store(tmp_path, monkeypatch):
    monkeypatch.setattr(G, "store_root", lambda: tmp_path / "goals")
    return G.GoalStore(tmp_path / "goals")


class TestARunThatStoppedIsStamped:
    """`reconcile_stale_runs`, and the measured case that started this."""

    def test_a_run_dead_a_month_is_stamped(self):
        """**The measured defect.** Before this, nothing in the tree aged it out
        and the panel said "in progress"."""
        dead = _run(created=NOW - 30 * DAY, updated=NOW - 30 * DAY)
        out = G.reconcile_stale_runs([dead], now=NOW + 7 * HOUR, threshold=THRESHOLD)
        assert out[0].stale_since == NOW + 7 * HOUR

    def test_a_run_that_just_saved_is_left_alone(self):
        """**The over-fix, and the reason the threshold is six hours.** A live run
        must never be at risk; this is the case that proves the demotion is not
        accidentally marking everything.

        Both timestamps are recent because `_last_activity` is the newer of the
        two - a run created an hour ago and never stepped is *running*, and the
        first version of this test set `created_at=NOW`, which made it live for
        the wrong reason and passed against a threshold of zero."""
        live = _run(created=NOW - 60, updated=NOW - 60)
        out = G.reconcile_stale_runs([live], now=NOW, threshold=THRESHOLD)
        assert out[0] is live, "a live run was demoted"

    def test_a_run_at_exactly_the_threshold_is_stamped(self):
        """Boulder's own wording is "at least threshold old", so the boundary is
        inclusive. A test that asserted strictly-greater would pass on a
        threshold that never fires."""
        at = _run(created=NOW - THRESHOLD, updated=NOW - THRESHOLD)
        out = G.reconcile_stale_runs([at], now=NOW, threshold=THRESHOLD)
        assert out[0].stale_since == NOW

    def test_one_second_inside_the_threshold_is_not(self):
        out = G.reconcile_stale_runs(
            [_run(created=NOW - THRESHOLD + 1, updated=NOW - THRESHOLD + 1)],
            now=NOW, threshold=THRESHOLD)
        assert out[0] is not None and out[0].stale_since == 0.0

    def test_a_parked_run_a_month_old_is_untouched(self):
        """**AWAITING is the load-bearing exclusion.** "Parked, somebody outside
        has to answer" is legitimately weeks old - a run waiting for a person's
        answer is not a run that died."""
        parked = _run(phase=G.Phase.AWAITING, created=NOW - 30 * DAY,
                      updated=NOW - 30 * DAY)
        out = G.reconcile_stale_runs([parked], now=NOW + 7 * HOUR, threshold=THRESHOLD)
        assert out[0] is parked

    @pytest.mark.parametrize("phase", [G.Phase.COMPLETED, G.Phase.FAILED, G.Phase.PENDING])
    def test_a_settled_run_is_untouched(self, phase):
        """COMPLETED and FAILED are settled and PENDING has not started; marking
        any of them "stopped here" would rewrite a state that already says
        everything a person needs."""
        settled = _run(phase=phase, created=NOW - 99 * DAY, updated=NOW - 99 * DAY)
        out = G.reconcile_stale_runs([settled], now=NOW + 7 * HOUR, threshold=THRESHOLD)
        assert out[0] is settled

    def test_a_run_with_a_lost_updated_at_is_young(self):
        """**A run whose `updated_at` field was lost, but whose `created_at` is
        recent, is not dead.** `from_json` defaults a missing `updated_at` to 0.0
        while keeping `created_at`, so this is what a partial file reads as - and
        the whole point of `_last_activity` taking the *newer* of the two.

        This is the witness for the mutation that drops `created_at` from the
        max, and the first version of it used two zeros, which that mutation
        leaves unchanged.
        """
        young = _run(created=NOW - 60, updated=0.0)
        out = G.reconcile_stale_runs([young], now=NOW, threshold=THRESHOLD)
        assert out[0] is young, (
            "a run created a minute ago was declared dead because its "
            "updated_at field was missing")

    def test_a_run_with_no_activity_evidence_is_stamped(self):
        """Boulder: *"a work with no activity evidence at all is stale."*
        `from_json` defaults both timestamps to 0.0, so a run written by an older
        build has none - and it is the one most likely to be stuck."""
        unknown = _run(created=0.0, updated=0.0)
        out = G.reconcile_stale_runs([unknown], now=NOW, threshold=THRESHOLD)
        assert out[0].stale_since == NOW

    def test_an_already_stamped_run_is_not_restamped(self):
        """Re-stamping would move the stopped-time forward on every refresh, so a
        run that stopped last week would look like it stopped today."""
        stamped = _run(created=NOW - 30 * DAY, updated=NOW - 30 * DAY,
                       stale=NOW - 3 * DAY)
        out = G.reconcile_stale_runs([stamped], now=NOW, threshold=THRESHOLD)
        assert out[0] is stamped, "the stamp was moved"

    def test_nothing_stale_means_nothing_written(self):
        """The same-object rule, which is what lets a caller skip the store write.
        A list of equal-but-new objects would write on every refresh."""
        runs = [_run(created=NOW - 60, updated=NOW - 60),
                _run(created=NOW - 61, updated=NOW - 61)]
        out = G.reconcile_stale_runs(runs, now=NOW, threshold=THRESHOLD)
        assert out == runs
        assert all(a is b for a, b in zip(runs, out))

    def test_only_the_stale_one_changes(self):
        live = _run(created=NOW - 60, updated=NOW - 60)
        dead = _run(created=NOW - 30 * DAY, updated=NOW - 30 * DAY)
        out = G.reconcile_stale_runs([live, dead], now=NOW, threshold=THRESHOLD)
        assert out[0] is live, "the live one was demoted"
        assert out[1] is not dead and out[1].stale_since == NOW, (
            "the dead one was not demoted, or was demoted to the wrong time")

    def test_a_threshold_of_zero_does_not_demote_a_live_run(self):
        """A zero threshold would stamp a run the moment it was created - the same
        failure as no reconciliation at all, in the other direction.

        **The witness has to be a live run.** The first version asserted a
        30-day-dead one, which a zero threshold also stamps, so the test passed
        against the mutation that removes the fallback.
        """
        live = _run(created=NOW - 60, updated=NOW - 60)
        for threshold in (0, -1, 0.0):
            out = G.reconcile_stale_runs([live], now=NOW, threshold=threshold)
            assert out[0] is live, (
                f"a threshold of {threshold} demoted a live run instead of "
                "falling back to the default")

    def test_the_threshold_comes_from_the_environment(self, monkeypatch):
        monkeypatch.setenv("CHRONOA_GOAL_STALE_SECONDS", "60")
        recent = _run(created=NOW - 120, updated=NOW - 120)
        out = G.reconcile_stale_runs([recent], now=NOW)
        assert out[0].stale_since == NOW, (
            "CHRONOA_GOAL_STALE_SECONDS was ignored, so a person debugging a run "
            "cannot watch it go stale without editing code")

    def test_an_unparseable_environment_value_uses_the_default(self, monkeypatch):
        monkeypatch.setenv("CHRONOA_GOAL_STALE_SECONDS", "very soon")
        live = _run(created=NOW - 60, updated=NOW - 60)
        out = G.reconcile_stale_runs([live], now=NOW)
        assert out[0] is live, "an unparseable threshold demoted a live run"

    def test_the_default_threshold_is_six_hours(self):
        assert G.STALE_RUN_THRESHOLD_SECONDS == 6 * 3600


class TestTheStampSurvivesTheDisk:
    """`stale_since` is persisted state, so it has to round-trip."""

    def test_the_round_trip_keeps_it(self):
        run = _run(stale=NOW)
        assert G.GoalRun.from_json(run.to_json()).stale_since == NOW

    def test_an_older_file_without_the_field_is_not_stale(self):
        """A run written by the previous build has no `stale_since`. Reading it as
        anything other than 0.0 would demote every existing run on first open."""
        import json

        run = _run()
        data = json.loads(run.to_json())
        del data["stale_since"]
        assert G.GoalRun.from_json(json.dumps(data)).stale_since == 0.0

    def test_a_garbage_stamp_is_not_a_stamp(self, store):
        """`stale_since` is a float in the type. A hand-edited file holding a
        string would make `_when()` raise on the next render - which would blank
        the whole panel, not just one card."""
        store.save(_run(), stamp=False)
        path = [p for p in store.root.glob("*.json")][0]
        import json

        data = json.loads(path.read_text())
        data["stale_since"] = "yesterday"
        path.write_text(json.dumps(data))
        loaded = store.load("r")
        assert loaded is not None, "a corrupt timestamp removed the run"
        assert loaded.stale_since == 0.0, (
            "a non-numeric stamp was read as something other than 'no evidence'")
        assert loaded.created_at == _run().created_at, (
            "the coercion touched a field that was still valid")


class TestTheWriteDoesNotEraseTheEvidence:
    """The trap: `save` stamps `updated_at`, which is what the demotion reads."""

    def test_the_demotion_write_keeps_updated_at(self, store):
        dead = _run(created=NOW - 30 * DAY, updated=NOW - 30 * DAY)
        store.save(dead, stamp=False)
        stamped = G.reconcile_stale_runs([dead], now=NOW + 7 * HOUR,
                                         threshold=THRESHOLD)[0]
        store.save(stamped, stamp=False)
        back = store.load("r")
        assert back.stale_since == NOW + 7 * HOUR
        assert back.updated_at == dead.updated_at, (
            "the write that recorded the demotion reset updated_at, so the card "
            "reads 'last moving in the last hour' about a run dead for a month")

    def test_a_save_still_stamps_by_default(self, store):
        """Every existing caller keeps the stamp: a step that ran *is* activity,
        and the panel's run loop relies on it."""
        run = _run(updated=NOW - 30 * DAY)
        back = store.save(run)
        assert back.updated_at != run.updated_at
        assert abs(back.updated_at - time.time()) < 5

    def test_stamp_false_writes_exactly_what_it_is_given(self, store):
        run = _run(updated=NOW - 5 * DAY)
        back = store.save(run, stamp=False)
        assert back is run and back.updated_at == NOW - 5 * DAY


class TestSteppingClearsTheStamp:
    """Boulder's `selectActiveWork` returns a stamped work to active and drops the
    stamp, so resuming is the same operation as starting."""

    def test_advance_clears_it(self):
        """Index 0, so step `a` - which has no dependencies - is the one that runs.
        The first version used index 1, where step `b` needs a result nothing has
        produced, so the run *parked* instead of advancing and the assertion was
        about the wrong transition."""
        run = _run(index=0, stale=NOW - HOUR)
        out = G.advance(run, lambda s, r: "ok", now=NOW)
        assert out.stale_since == 0.0
        assert out.phase is G.Phase.RUNNING
        assert out.index == 1

    def test_advance_stamps_updated_at(self):
        """It was frozen before: `advance` returned a `_replace` with no timestamp,
        and the store's stamp was the only thing recording progress. Stated here
        so it cannot be dropped again - `updated_at` is the activity signal the
        demotion reads, so a step that ran and did not record it is invisible."""
        run = _run(updated=NOW - 30 * DAY)
        out = G.advance(run, lambda s, r: "ok", now=NOW)
        assert out.updated_at == NOW

    def test_a_step_that_fails_keeps_the_stamp(self):
        """The run did stop. A FAILED phase says how, and the stamp still says
        when - clearing it would make a failure read as a live run."""
        run = _run(stale=NOW - HOUR)

        def boom(step, results):
            raise RuntimeError("nope")

        out = G.advance(run, boom, now=NOW)
        assert out.phase is G.Phase.FAILED
        assert out.stale_since == NOW - HOUR

    def test_a_parked_run_moves_on_once_its_inputs_exist(self):
        """AWAITING is not a suspended thread - the state says somebody outside has
        to answer, and once they have, `advance` carries on from exactly where it
        stopped. That is the "nothing in the state is a suspended thread" line in
        `Phase`'s own docstring, so the first version of this test asserting the
        phase was left alone was asserting the opposite of the design."""
        run = _run(phase=G.Phase.AWAITING, index=0, updated=NOW - 30 * DAY)
        out = G.advance(run, lambda s, r: "ok", now=NOW)
        assert out.phase is G.Phase.RUNNING and out.index == 1


class TestThePanelSaysWhatHappened:
    """The words a person reads, through the real panel.

    **These live on the real clock, and that is deliberate.** `_when()` renders a
    *relative* sentence ("30 day(s) ago"), which is the only thing that answers
    the question the card is asking - so a run built on a synthetic epoch renders
    "20755 day(s) ago" and the test measures the clock instead of the code. The
    reconciliation tests above take an injected `now`; the rendering tests cannot
    honestly do the same.
    """

    CLOCK = time.time()

    def _aged(self, age, phase=G.Phase.RUNNING, index=1, stale=0.0):
        return G.GoalRun(
            id="r", goal="book a flight", phase=phase,
            steps=(_step("a"), _step("b")), index=index,
            created_at=self.CLOCK - age, updated_at=self.CLOCK - age,
            stale_since=stale,
        )

    def _panel(self, store):
        from shani_chronoa.gui.surfaces import goals as panel
        surface = panel._GoalsSurface.__new__(panel._GoalsSurface)
        surface._app = None
        surface.store = store
        surface.executor = G.tool_executor()
        surface.status_recorder = panel.common.StatusRecorder()
        surface._body = _box()
        surface.running = False
        surface._notice = ""
        surface._run_error = {}
        return panel, surface

    def test_a_dead_run_no_longer_says_in_progress(self, store):
        """**The measured case.** Before: "in progress - 1 of 2 steps" for a run
        last touched 30 days ago."""
        panel, surface = self._panel(store)
        dead = self._aged(30 * DAY)
        store.save(dead, stamp=False)
        assert panel._phase_sentence(dead).startswith("in progress"), (
            "the un-reconciled run should still say in progress; this is the "
            "before-state of the very next assertion")
        surface.refresh()
        sentence = panel._phase_sentence(store.load("r"))
        assert "stopped" in sentence and "in progress" not in sentence, sentence
        assert "30 day" in sentence, (
            f"the card does not say how long ago it stopped: {sentence!r}")

    def test_a_live_run_still_says_in_progress(self, store):
        panel, surface = self._panel(store)
        store.save(self._aged(30), stamp=False)
        surface.refresh()
        assert panel._phase_sentence(store.load("r")).startswith("in progress")

    def test_a_parked_run_still_says_waiting(self, store):
        panel, surface = self._panel(store)
        store.save(self._aged(30 * DAY, phase=G.Phase.AWAITING), stamp=False)
        surface.refresh()
        assert panel._phase_sentence(store.load("r")).startswith("waiting for you")

    def test_the_refresh_writes_the_demotion(self, store):
        """The stamp reaches disk, so it survives the panel being closed."""
        panel, surface = self._panel(store)
        store.save(self._aged(30 * DAY), stamp=False)
        surface.refresh()
        assert store.load("r").stale_since > 0

    def test_the_write_does_not_reset_the_activity(self, store):
        """The trap the write itself sets: `save` stamps `updated_at` by default,
        and that stamp *is* the activity signal. Measured without `stamp=False`:
        the card read "last moving in the last hour" about a run dead for a
        month."""
        panel, surface = self._panel(store)
        dead = self._aged(30 * DAY)
        store.save(dead, stamp=False)
        surface.refresh()
        sentence = panel._phase_sentence(store.load("r"))
        assert "30 day" in sentence, (
            f"the demotion write reset the activity, so the card lost it: "
            f"{sentence!r}")

    def test_a_read_only_disk_still_shows_the_goals(self, store, monkeypatch):
        """**A reconciliation that fails is not a failed panel.** The demotion
        cannot be recorded, but a person must still see their goals - the next
        refresh tries again. This is the opposite of the `_run_error` rule: that
        one reports a store failure on a *click*, because a button that did
        nothing and said nothing is the defect. This runs unprompted.
        """
        panel, surface = self._panel(store)
        store.save(self._aged(30 * DAY), stamp=False)
        monkeypatch.setattr(store, "save",
                            lambda run, stamp=True: (_ for _ in ()).throw(
                                OSError("read-only file system")))

        surface.refresh()
        drawn = " | ".join(_texts(surface._body))
        assert "book a flight" in drawn, (
            f"the panel went blank on an unrecordable demotion: {drawn!r}")

    def test_resuming_through_a_step_clears_the_stamp(self, store):
        """Resuming is the same operation as starting: the panel's loop goes
        through `advance`, and `advance` clears the stamp."""
        panel, surface = self._panel(store)
        run = self._aged(30 * DAY, index=0, stale=self.CLOCK - HOUR)
        store.save(run, stamp=False)
        stepped = store.save(G.advance(run, lambda s, r: "ok", now=self.CLOCK))
        assert stepped.stale_since == 0.0
        assert panel._phase_sentence(stepped).startswith("in progress")


def _texts(widget, out=None):
    """Every string a widget tree renders, in walk order.

    `get_accessible_property` does not exist on this PyGObject, so the readable
    strings are read through each widget's own getters.
    """
    if out is None:
        out = []
    for getter in ("get_title", "get_description", "get_subtitle", "get_label"):
        try:
            value = getattr(widget, getter)()
        except Exception:  # noqa: BLE001 - not every widget has every getter
            value = None
        if isinstance(value, str) and value:
            out.append(value)
    child = widget.get_first_child()
    while child is not None:
        _texts(child, out)
        child = child.get_next_sibling()
    return out


def _box():
    from gi.repository import Gtk

    return Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
