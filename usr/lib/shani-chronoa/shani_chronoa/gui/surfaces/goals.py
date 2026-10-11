"""Goals: saved multi-step plans, and the controls that run them.

`goals.py` was a complete queue with no producer and no consumer, and the rail
and panels deliberately showed nothing for it - a card over a queue nothing can
fill is a dead control. `skills/manage_goals.py` is the producer now (it saves
a plan and runs nothing), and this panel is the consumer: it lists every saved
run and steps it forward.

**Every step runs through `tools.execute_tool`** (`goals.tool_executor`), the
chat path, so a step meets the same consent key, approval question and
destructive-tool rule it would have met as a chat request. A run is saved after
each step, so a crash loses at most the step in flight, and a run that parks -
a step waiting on a result nobody has produced - takes your answer here.
"""

from __future__ import annotations

import logging
import threading
from typing import Any, List, Optional

import gi

gi.require_version("Gtk", "4.0")
from gi.repository import GLib, Gtk  # noqa: E402

from shani_chronoa import goals  # noqa: E402
from shani_chronoa.gui.surfaces import common  # noqa: E402

logger = logging.getLogger(__name__)

TITLE = "Goals"
ICON = "view-list-ordered-symbolic"
SECTION = "Acting"
SUBTITLE = ("Plans Chronoa saved for later, one step at a time. Each step runs "
            "exactly as a chat request would, asking where chat would ask.")

_PHASE_WORD = {
    goals.Phase.PENDING: "not started",
    goals.Phase.RUNNING: "in progress",
    goals.Phase.AWAITING: "waiting for you",
    goals.Phase.COMPLETED: "done",
    goals.Phase.FAILED: "failed",
}

#: The phases a run can still move forward from.
LIVE = (goals.Phase.PENDING, goals.Phase.RUNNING)


def _when(at: float) -> str:
    """`at` as a sentence a person reads, not a timestamp they parse.

    Relative, because "30 days ago" answers the question the card is asking and
    an absolute epoch second does not. The threshold for the demotion is six
    hours, so anything reaching here is hours or more old - "just now" would be
    a run that has not been reconciled at all.
    """
    import time

    seconds = max(0.0, time.time() - (at or 0.0))
    if seconds < 3600:
        return "in the last hour"
    if seconds < 86400:
        return f"{int(seconds // 3600)} hour(s) ago"
    return f"{int(seconds // 86400)} day(s) ago"


def _phase_sentence(run: goals.GoalRun) -> str:
    """The card's subtitle: what this run is doing, honestly.

    **A run stamped stale is not "in progress", and saying it is the whole
    defect.** oh-my-openagent's `boulder-state` (#8413) is exactly this - a work
    whose session died stays `active` forever - and Chronoa had it unnamed: the
    measured case was a run last touched 30 days ago rendering as "in progress".

    The word changes but the *phase* does not: `Phase.AWAITING` means "parked,
    somebody outside has to answer", which is not what a stopped run is. So the
    stamp rides on `run.stale_since` and only the sentence reflects it - the
    same separation boulder draws between its `paused` status and its
    `stale_since` stamp.
    """
    word = _PHASE_WORD[run.phase]
    steps = f"{min(run.index, len(run.steps))} of {len(run.steps)} steps"
    if run.stale_since and run.phase is goals.Phase.RUNNING:
        return (f"stopped here {_when(run.stale_since)} - it was last moving "
                f"{_when(run.updated_at or run.created_at)}, {steps}")
    return f"{word} - {steps}"


def open_runs(store: Optional[goals.GoalStore] = None) -> List[goals.GoalRun]:
    """Saved runs that are not finished, newest first. [] when none or unreadable."""
    try:
        runs = (store or goals.GoalStore(goals.store_root())).list()
    except OSError:
        return []
    return sorted((r for r in runs if r.phase not in (goals.Phase.COMPLETED, goals.Phase.FAILED)),
                  key=lambda r: r.created_at, reverse=True)


class _GoalsSurface:
    def __init__(self, app: Any, set_content) -> None:
        self._app = app
        self.store = goals.GoalStore(goals.store_root())
        self.executor = goals.tool_executor()
        self.status_recorder = common.StatusRecorder()
        self._body = common.page_body()
        for side in ("start", "end", "top", "bottom"):
            getattr(self._body, f"set_margin_{side}")(12)
        set_content(common.scrolled(self._body))
        self.running = False
        self._notice = ""
        #: Why the last step of each run failed, by run id. Absent means the last
        #: attempt either succeeded or was never made.
        #:
        #: A *store* failure was silent: `load`/`save` raising left the run
        #: exactly as it was, so the refresh showed it unchanged and the person
        #: pressed a button that did nothing. A *step* failure never was — see
        #: `run`'s docstring.
        self._run_error: dict = {}
        self.refresh()

    # -- drawing --------------------------------------------------------------

    def refresh(self) -> None:
        common.clear(self._body)
        # The last thing a click did, said out loud. A refusal that is computed
        # and never rendered is the dead-control defect this repo has recorded
        # four times - `drop_step` can refuse (a later step needs this one) and
        # a refusal nobody can see is a refusal nobody will act on.
        if self._notice:
            self._body.append(common.row("", self._notice))
            self._notice = ""
        try:
            runs = sorted(self.store.list(), key=lambda r: r.created_at, reverse=True)
        except OSError as exc:
            self._body.append(self.status_recorder.row(
                common.STATUS_ATTENTION, "The goal store could not be read", str(exc)))
            return
        # **Age out the runs that stopped without finishing, then save only those.**
        # This is the panel's half of the boulder-state transfer: the store keeps
        # `stale_since` and this is where it is first written, because refresh is
        # the only thing that runs on every open of the panel.
        #
        # **A reconciliation that fails is not a failed panel.** A read-only disk
        # still has to show the person their goals; it just cannot record the
        # demotion, so the next refresh tries again. Swallowing it here is the
        # opposite of the `_run_error` rule above - that one reports a store
        # failure on a *click*, because a button that did nothing and said so is
        # the defect. This runs unprompted, so there is no click to answer for.
        try:
            reconciled = goals.reconcile_stale_runs(runs)
            for before, after in zip(runs, reconciled):
                if after is before:
                    continue          # nothing changed, nothing written
                self.store.save(after, stamp=False)
            runs = reconciled
        except Exception:  # noqa: BLE001 - a demotion must never blank the panel
            logger.warning("could not reconcile stale goal runs", exc_info=True)
        live = [r for r in runs if r.phase not in (goals.Phase.COMPLETED, goals.Phase.FAILED)]
        waiting = [r for r in runs if r.phase == goals.Phase.AWAITING]
        if not runs:
            self._body.append(self.status_recorder.row(
                common.STATUS_OK, "No goals saved",
                "nothing is queued, so nothing here can run"))
            self._body.append(common.empty_state(
                ICON, "No goals yet",
                "Ask Chronoa to plan something in steps and save it as a goal "
                "(Settings, Privacy: 'Let Chronoa keep multi-step goals'). It is "
                "listed here, and nothing runs until you start it."))
            return
        self._body.append(self.status_recorder.row(
            common.STATUS_ATTENTION if waiting else common.STATUS_OK,
            f"{len(live)} open goal(s)" + (f", {len(waiting)} waiting for you" if waiting else ""),
            f"{len(runs)} saved in {self.store.root}"))
        for run in runs:
            self._body.append(self._card(run))

    def _card(self, run: goals.GoalRun) -> Gtk.Widget:
        group = common.group(run.goal, _phase_sentence(run))
        group.add_css_class("goal-card")
        for i, step in enumerate(run.steps):
            done = i < run.index
            mark = Gtk.Image.new_from_icon_name(
                "object-select-symbolic" if done else
                ("media-playback-start-symbolic" if i == run.index and run.phase in LIVE
                 else "radio-symbolic"))
            result = run.results.get(step.variable_name)
            detail = f"{step.tool_name} -> ${step.variable_name}"
            if result is not None:
                detail += f": {str(result)[:120]}"
            row = common.row(step.thought or step.tool_name, detail, suffix=mark)
            # **A step can be taken out of a plan, and this is the only control
            # that ever could.** open-intelligent-ui's route panel lets a person
            # remove a stop; Chronoa's Goals panel had "remove this goal" and
            # nothing finer, so one wrong step meant throwing the whole plan away
            # and asking again. The refusal when a later step needs this one is
            # shown on the row that caused it, because a refusal nobody can
            # locate is a refusal nobody will fix.
            if not done:
                drop = Gtk.Button(icon_name="user-trash-symbolic")
                drop.add_css_class("flat")
                drop.set_tooltip_text(f"Remove the step ${step.variable_name} from this plan")
                drop.update_property(
                    [Gtk.AccessibleProperty.LABEL],
                    [f"Remove step {step.variable_name} of {run.goal}"])
                drop.connect("clicked", lambda _b, r=run, s=step: self.drop_step(r, s))
                _add(group, row)
                holder = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, hexpand=True)
                holder.set_hexpand(True)
                holder.append(drop)
                holder.set_halign(Gtk.Align.END)
                _add(group, holder)
            else:
                _add(group, row)

        # **Removed steps are drawn, dimmed, with a way back.**
        # `travel.css`: `.tv-stop.is-removed { opacity: .45 }` over its photo,
        # time, title and description — open-intelligent-ui dims a removed stop
        # rather than deleting it, so the person keeps seeing what they took out
        # and can put it back. Chronoa threw it away, and a step removed by
        # mistake had no route back at all: the only recovery was to ask the
        # model to plan the whole goal again.
        #
        # The dim is the accessible form: opacity *and* the words "removed",
        # because colour or fade alone is the one-dimension cue this repo's
        # `test_window_ux` already refuses elsewhere.
        for step in run.removed:
            row = common.row(f"{step.thought or step.tool_name} (removed)",
                             f"{step.tool_name} -> ${step.variable_name}",
                             suffix=Gtk.Image.new_from_icon_name("edit-undo-symbolic"))
            try:
                row.set_opacity(0.45)
                row.set_sensitive(False)
            except Exception:  # noqa: BLE001 - a styling miss must not break the row
                logger.debug("could not dim the removed-step row", exc_info=True)
            _add(group, row)
            back = Gtk.Button(icon_name="edit-undo-symbolic")
            back.add_css_class("flat")
            back.set_tooltip_text(f"Put the step ${step.variable_name} back")
            back.update_property(
                [Gtk.AccessibleProperty.LABEL],
                [f"Restore step {step.variable_name} of {run.goal}"])
            back.connect("clicked", lambda _b, r=run, s=step: self.restore_step(r, s))
            holder = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, hexpand=True)
            holder.set_hexpand(True)
            holder.append(back)
            holder.set_halign(Gtk.Align.END)
            _add(group, holder)

        # **A store failure, on the card.** A run whose step raised is handled
        # one layer down (`goals.advance` -> `Phase.FAILED`, already drawn). This
        # is the other case: `load`/`save` raising leaves the run unchanged, so
        # nothing else about it moved and this is the only place the person could
        # learn why the button did nothing.
        stuck = self._run_error.get(run.id)
        if stuck:
            _add(group, common.row("Could not step this goal",
                                   stuck + " The step stopped here; nothing was skipped."))

        actions = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6, halign=Gtk.Align.END)
        actions.set_margin_top(6)
        if run.phase == goals.Phase.AWAITING:
            entry = Gtk.Entry(hexpand=True, placeholder_text=run.awaiting_reason or "Your answer")
            entry.update_property([Gtk.AccessibleProperty.LABEL], [f"Answer for {run.goal}"])
            answer = Gtk.Button(label="Answer")
            answer.add_css_class("suggested-action")
            answer.connect("clicked", lambda _b: self.answer(run.id, entry.get_text()))
            actions.append(entry)
            actions.append(answer)
        if run.phase in LIVE:
            once = Gtk.Button(label="Run next step")
            once.connect("clicked", lambda _b: self.run(run.id, to_the_end=False))
            all_ = Gtk.Button(label="Run to the end")
            all_.add_css_class("suggested-action")
            all_.connect("clicked", lambda _b: self.run(run.id, to_the_end=True))
            for b in (once, all_):
                b.set_sensitive(not self.running)
                actions.append(b)
        remove = Gtk.Button(icon_name="user-trash-symbolic")
        remove.add_css_class("flat")
        remove.set_tooltip_text("Remove this goal")
        remove.update_property([Gtk.AccessibleProperty.LABEL], [f"Remove goal {run.goal}"])
        remove.connect("clicked", lambda _b: self.remove(run.id))
        actions.append(remove)
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        box.append(group)
        box.append(actions)
        box._goal_id = run.id
        return box

    # -- actions --------------------------------------------------------------

    def run(self, run_id: str, to_the_end: bool, on_done=None) -> None:
        """Step a run forward off the main loop, saving after every step.

        **A store failure is shown, not only logged.** The comment this replaced
        said "shown by the refresh, not raised", and that is true of a *step*
        failing — `goals.advance` turns a step exception into `Phase.FAILED` and
        this panel already draws `_PHASE_WORD[FAILED]` as "failed - n of m
        steps". Measured by writing the tests the wrong way round first: they
        asserted a step exception reached `_run_error`, and running them produced
        `WARNING goals.py ... failed at step a` with an empty map.

        What *was* silent is a **store** failure: `self.store.load` or `.save`
        raising leaves the run exactly as it was, so the refresh shows it
        unchanged and the person presses a button that does nothing.

        open-intelligent-ui's `useStreetRoute` returns three states — `undefined`
        while loading, `null` when the request failed, points otherwise — so a
        caller cannot confuse them. Same distinction here, and it is drawn on the
        run's own card rather than only in the log.
        """
        if self.running:
            return
        self.running = True
        self.refresh()

        def work() -> None:
            failure = ""
            try:
                run = self.store.load(run_id)
                while run is not None and run.phase in LIVE:
                    run = self.store.save(goals.advance(run, self.executor))
                    if not to_the_end:
                        break
            except Exception as exc:  # noqa: BLE001 - put on the card, not raised
                logger.warning("goal %s could not be advanced", run_id, exc_info=True)
                failure = f"{type(exc).__name__}: {exc}"[:200]

            def finish() -> bool:
                self.running = False
                if failure:
                    self._run_error[run_id] = failure
                else:
                    self._run_error.pop(run_id, None)
                self.refresh()
                if on_done is not None:
                    on_done()
                return False

            GLib.idle_add(finish)

        threading.Thread(target=work, daemon=True).start()

    def answer(self, run_id: str, text: str) -> None:
        if text.strip():
            self.store.resume(run_id, text.strip())
        self.refresh()

    def remove(self, run_id: str) -> None:
        """Delete a saved goal.

        **This was deleted once and shipped.** `drop_step` was written by
        replacing the body of this method rather than being added beside it, so
        "Remove this goal" was left calling `self.remove` with no such method —
        `AttributeError` on every click, found only by building the panel and
        calling it. No test caught it, because no test clicked it. Two methods,
        one entry point each.
        """
        label = self._label(run_id)
        try:
            goals._state_path(self.store.root, run_id).unlink()
        except OSError as exc:
            logger.warning("could not remove goal %s: %s", run_id, exc)
        self._notice = f"Removed the goal {label}."
        self._run_error.pop(run_id, None)
        self.refresh()

    def _label(self, run_id: str) -> str:
        """The goal's description, so a sentence names what went."""
        for run in self.store.list():
            if run.id == run_id:
                return str(run.goal)
        return run_id

    def drop_step(self, run: goals.GoalRun, step: goals.PlannedStep) -> None:
        """Take one step out of a plan, and say so when it cannot be.

        The refusal is put in front of the person rather than swallowed:
        `remove_step` names the step that still needs this one, and that
        sentence is what the row would otherwise be missing. The store is
        re-read rather than patched from the stale card, so two clicks in a row
        cannot drop two steps on the strength of one.

        The step is **kept**, not deleted — open-intelligent-ui dims a removed
        stop (`.tv-stop.is-removed { opacity: .45 }`) rather than removing it, so
        the person can see what they took out and put it back with
        `restore_step`.
        """
        import time

        current = self.store.load(run.id) or run
        updated, why = goals.remove_step(current, step.variable_name, now=time.time())
        if why:
            self._notice = why
            logger.info("goal %s: step %s not removed: %s", run.id, step.variable_name, why)
        else:
            self._notice = (f"Removed ${step.variable_name} from {run.goal}. It is "
                            "still listed, dimmed, and can be put back.")
            self.store.save(updated)
        self.refresh()

    def restore_step(self, run: goals.GoalRun, step: goals.PlannedStep) -> None:
        """Put a step back, and say so when it cannot be.

        The reason the dimmed row is worth drawing at all: without a way back, a
        step removed by mistake meant planning the whole goal again.
        """
        import time

        current = self.store.load(run.id) or run
        updated, why = goals.restore_step(current, step.variable_name, now=time.time())
        if why:
            self._notice = why
        else:
            self._notice = f"Put ${step.variable_name} back in {run.goal}."
            self.store.save(updated)
        self.refresh()


def _add(group: Gtk.Widget, child: Gtk.Widget) -> None:
    adder = getattr(group, "add", None)
    (adder or group.append)(child)


def build(app: Any) -> Gtk.Widget:
    page, set_content = common.surface(TITLE, SUBTITLE)
    surface = _GoalsSurface(app, set_content)
    page.surface = surface
    page.status = surface.status_recorder.status
    # Re-read on every showing: the skill saves runs from a chat turn while this
    # page sits built and cached in the window.
    if hasattr(page, "connect") and common.adw_ready():
        page.connect("showing", lambda *_a: surface.refresh())
    return page


__all__ = ["TITLE", "ICON", "SECTION", "build", "open_runs"]
