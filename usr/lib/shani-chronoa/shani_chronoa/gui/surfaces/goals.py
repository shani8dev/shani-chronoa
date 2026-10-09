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
        self.refresh()

    # -- drawing --------------------------------------------------------------

    def refresh(self) -> None:
        common.clear(self._body)
        try:
            runs = sorted(self.store.list(), key=lambda r: r.created_at, reverse=True)
        except OSError as exc:
            self._body.append(self.status_recorder.row(
                common.STATUS_ATTENTION, "The goal store could not be read", str(exc)))
            return
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
        group = common.group(run.goal, f"{_PHASE_WORD[run.phase]} - "
                                       f"{min(run.index, len(run.steps))} of {len(run.steps)} steps")
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
            _add(group, common.row(step.thought or step.tool_name, detail, suffix=mark))

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
        """Step a run forward off the main loop, saving after every step."""
        if self.running:
            return
        self.running = True
        self.refresh()

        def work() -> None:
            try:
                run = self.store.load(run_id)
                while run is not None and run.phase in LIVE:
                    run = self.store.save(goals.advance(run, self.executor))
                    if not to_the_end:
                        break
            except Exception:  # noqa: BLE001 - shown by the refresh, not raised
                logger.warning("goal %s could not be advanced", run_id, exc_info=True)

            def finish() -> bool:
                self.running = False
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
        try:
            goals._state_path(self.store.root, run_id).unlink()
        except OSError as exc:
            logger.warning("could not remove goal %s: %s", run_id, exc)
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
