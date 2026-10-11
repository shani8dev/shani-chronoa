"""Editing a saved goal by hand, and the model being told.

The data half comes from `open-intelligent-ui` (harness-study): its route panel
lets a person remove stops and add suggestions, the state is saved with the
message, and the next turn rewrites it as a sentence the model reads - "(User's
edits to this route: removed stops (by id): belem; added stops: LX Factory.)".
Chronoa's Goals panel had "remove this goal" and nothing finer, so one wrong step
meant discarding a whole plan and asking again.

The part open-intelligent-ui never has to face: a `PlannedStep` names a
`variable_name` other steps depend on, so removing one can orphan a later step.
Here the refusal is the load-bearing half, and these tests assert it *names* the
step in the way rather than merely failing.
"""

from __future__ import annotations

import sys

sys.path.insert(0, "usr/lib/shani-chronoa")

import gi  # noqa: E402

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gtk  # noqa: E402

Adw.init()

from shani_chronoa import goals as G  # noqa: E402


def step(name, deps=()):
    return G.PlannedStep(thought=f"do {name}", tool_name="get_datetime",
                         tool_arguments={}, variable_name=name, depends_on=tuple(deps))


def chain(*names, deps=()):
    """A plan where each step needs the previous one's output."""
    built = []
    for i, n in enumerate(names):
        built.append(step(n, deps or ([names[i - 1]] if i else [])))
    return tuple(built)


def run_with(steps, **kw):
    return G.GoalRun(id="r1", goal="plan something", phase=G.Phase.RUNNING,
                     steps=tuple(steps), **kw)


class TestRemovingAStep:
    def test_a_leaf_step_comes_out(self):
        run = run_with(chain("a", "b", "c"))
        updated, why = G.remove_step(run, "c", now=5.0)
        assert why == ""
        assert [s.variable_name for s in updated.steps] == ["a", "b"]
        assert updated.index == 0

    def test_a_removed_steps_result_goes_with_it(self):
        """A result for a variable nothing produces any more is a fact about a
        plan that no longer exists, and a later step reading it would be running
        against nothing."""
        run = run_with(chain("a", "b"), results={"a": "done", "b": "also"})
        updated, why = G.remove_step(run, "b", now=1.0)
        assert why == ""
        assert updated.results == {"a": "done"}

    def test_an_earlier_step_cannot_be_removed_while_something_needs_it(self):
        run = run_with(chain("a", "b", "c"))
        updated, why = G.remove_step(run, "a")
        assert updated is run, "a refused removal returned a changed run"
        assert why, "removing a depended-on step was allowed silently"
        assert "$b" in why, f"the refusal does not name what is in the way: {why!r}"
        assert "$c" in why, (
            f"the refusal names the direct dependent only; the whole chain is "
            f"what would park: {why!r}")
        assert "Nothing was removed" in why, (
            "a refusal that does not say nothing happened reads as a success")

    def test_the_refusal_names_every_dependent(self):
        run = run_with(chain("a", "b", "c", "d"))
        _updated, why = G.remove_step(run, "a")
        for name in ("$b", "$c", "$d"):
            assert name in why, f"{name} is downstream of $a and the refusal omits it"

    def test_a_step_that_is_not_there_is_refused_by_name(self):
        """The list is offered because a refusal that says only "no such step"
        sends the person guessing which name it wanted."""
        run = run_with(chain("a", "b"))
        updated, why = G.remove_step(run, "zebra")
        assert updated is run
        assert "zebra" in why
        assert "$a" in why and "$b" in why, (
            f"the refusal does not say what the steps actually are: {why!r}")

    def test_removing_the_last_step_completes_a_running_plan(self):
        run = run_with(chain("a", "b"), index=1)
        updated, why = G.remove_step(run, "b", now=2.0)
        assert why == ""
        assert updated.phase is G.Phase.COMPLETED, (
            "a plan whose last step was removed still says RUNNING, so the "
            "panel's Run button stays live with nothing to run")

    def test_an_index_past_the_end_is_clamped(self):
        run = run_with(chain("a", "b", "c"), index=3)
        updated, why = G.remove_step(run, "c", now=2.0)
        assert why == ""
        assert updated.index == 2, f"index {updated.index} is past the new length 2"


class TestMovingAStep:
    def test_a_legal_move_keeps_the_order(self):
        run = run_with(chain("a", "b", "c"))
        updated, why = G.move_step(run, "c", 2, now=1.0)
        assert why == ""
        assert [s.variable_name for s in updated.steps] == ["a", "b", "c"]

    def test_moving_a_step_above_its_dependency_is_refused(self):
        run = run_with(chain("a", "b", "c"))
        updated, why = G.move_step(run, "c", 0)
        assert updated is run
        assert "$b" in why, f"the refusal does not name the input: {why!r}"
        assert "Nothing was moved" in why

    def test_a_move_out_of_range_is_refused_with_the_range(self):
        run = run_with(chain("a", "b"))
        updated, why = G.move_step(run, "b", 7)
        assert updated is run
        assert "0 to 1" in why, f"the refusal does not state the range: {why!r}"

    def test_an_independent_step_moves_freely(self):
        """Two steps with no dependency between them are the case a reorder is
        for, and it has to stay possible."""
        run = run_with((step("a"), step("b")))
        updated, why = G.move_step(run, "b", 0)
        assert why == ""
        assert [s.variable_name for s in updated.steps] == ["b", "a"]

    def test_a_dependency_already_settled_does_not_block_a_move(self):
        """A result that exists is not an ordering constraint - the input is
        already in hand, so moving the consumer above the producer is harmless."""
        run = run_with(chain("a", "b"), index=1, results={"a": "done"})
        updated, why = G.move_step(run, "b", 0)
        assert why == ""
        assert [s.variable_name for s in updated.steps] == ["b", "a"]


class TestTheModelIsTold:
    def test_an_untouched_plan_says_nothing(self):
        assert G.describe_edits(run_with(chain("a", "b"))) == "", (
            "a plan nobody edited must read as untouched; a sentence that names "
            "no edits is indistinguishable from one that names few")

    def test_a_removed_step_is_named(self):
        run = run_with(chain("a", "b", "c"))
        updated, _why = G.remove_step(run, "c", now=9.0)
        sentence = G.describe_edits(updated)
        assert "$c" in sentence
        assert "edited this plan by hand" in sentence

    def test_the_sentence_says_not_to_put_it_back(self):
        """The whole point. Without this a model asked to continue a trimmed
        plan sees a plan and re-adds what the person took out."""
        run = run_with(chain("a", "b", "c"))
        updated, _why = G.remove_step(run, "c", now=9.0)
        assert "do not put" in G.describe_edits(updated)

    def test_edits_survive_a_json_round_trip(self):
        run = run_with(chain("a", "b", "c"))
        updated, _why = G.remove_step(run, "c", now=3.5)
        back = G.GoalRun.from_json(updated.to_json())
        assert back.edits == (("c", 3.5),)
        assert [s.variable_name for s in back.steps] == ["a", "b"]

    def test_an_old_run_without_the_field_still_loads(self):
        """A run saved before this field existed must not fail to load - the
        store is on disk and an unreadable run is a lost plan."""
        raw = ('{"id":"old","goal":"g","phase":"running","steps":['
               '{"thought":"t","tool_name":"x","tool_arguments":{},'
               '"variable_name":"a"}],"index":0}')
        run = G.GoalRun.from_json(raw)
        assert run.edits == ()
        assert G.describe_edits(run) == ""


class TestTheSkillReportsTheEdits:
    """The half that makes `describe_edits` worth writing.

    **A test of `goals.describe_edits` alone passes however `manage_goals`
    renders it**, which is this repo's "patch the name where it is read, not
    where it is re-exported" rule in a new place: the mutation that deleted
    `line += goals.describe_edits(run)` from the skill left the whole suite
    green. So this drives the *skill's* `describe`, which is the only thing a
    model ever sees.
    """

    def _describe(self, run):
        sys.path.insert(0, "usr/lib/shani-chronoa")
        from shani_chronoa.skills import manage_goals
        return manage_goals.describe(run)

    def test_the_skills_description_carries_the_edit(self):
        run = run_with(chain("a", "b", "c"))
        updated, _why = G.remove_step(run, "c", now=9.0)
        text = self._describe(updated)
        assert "$c" in text, (
            f"manage_goals.describe does not mention the removed step, so a "
            f"model reading a saved plan learns nothing about it: {text!r}")
        assert "edited this plan by hand" in text

    def test_the_skills_description_of_an_untouched_plan_barely_changes(self):
        """The count and phase are the substance; nothing new is bolted on."""
        plain = self._describe(run_with(chain("a", "b")))
        assert "edited this plan by hand" not in plain
        assert "(0/2 steps)" in plain

    def test_the_waiting_reason_survives_the_edit_sentence(self):
        """Both facts are wanted: why it is parked, and what a person changed."""
        run = G.GoalRun(id="r1", goal="plan something", phase=G.Phase.AWAITING,
                        steps=chain("a", "b"),
                        awaiting_reason="waiting for: ['a']")
        updated, _why = G.remove_step(run, "b", now=1.0)
        text = self._describe(updated)
        assert "waiting for: ['a']" in text and "$b" in text, (
            f"one of the two facts was lost: {text!r}")


class TestThePanelShowsTheRefusal:
    def _surface(self, tmp_path, monkeypatch):
        """The real panel class, with the store pointed at a temp dir.

        A real `Gtk.Box` for the body, not a stub: `common.clear` calls
        `get_first_child()`, and a fake list that does not implement it fails for
        a reason that has nothing to do with the step-editing behaviour under
        test. GTK builds headless here, which is what every other panel test
        does.
        """
        monkeypatch.setattr(G, "store_root", lambda: tmp_path / "goals")
        import gi
        gi.require_version("Gtk", "4.0")
        from gi.repository import Gtk

        from shani_chronoa.gui.surfaces import goals as panel

        class _Surface(panel._GoalsSurface):
            def __init__(self):
                self._app = None
                self.store = G.GoalStore(G.store_root())
                self.executor = G.tool_executor()
                self.status_recorder = panel.common.StatusRecorder()
                self._body = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
                self.running = False
                self._notice = ""
                # `_run_error` is part of the panel's contract now, and the
                # doubles that skip it fail on the first refresh. Set here so
                # the double cannot drift from the thing it is standing in for.
                self._run_error = {}
                self.refresh()

        return _Surface()

    def _text(self, widget) -> str:
        """Every title and subtitle in the built tree, so a refusal that is
        computed but never rendered cannot pass."""
        import gi
        gi.require_version("Gtk", "4.0")
        gi.require_version("Adw", "1")
        from gi.repository import Adw

        out = []

        def walk(node):
            if isinstance(node, Adw.PreferencesRow):
                out.append(node.get_title() or "")
                try:
                    out.append(node.get_subtitle() or "")
                except Exception:  # noqa: BLE001 - not every row has a subtitle
                    pass
            child = node.get_first_child()
            while child is not None:
                walk(child)
                child = child.get_next_sibling()

        walk(widget)
        return " ".join(out)

    def test_dropping_a_depended_on_step_renders_the_refusal(self, tmp_path, monkeypatch):
        surface = self._surface(tmp_path, monkeypatch)
        run = run_with(chain("a", "b"))
        surface.store.save(run)
        surface.drop_step(run, run.steps[0])
        text = self._text(surface._body)
        assert "$b" in text, (
            f"the refusal never reached the screen, so the person is told "
            f"nothing and cannot tell why the step is still there: {text[:200]!r}")

    def test_dropping_a_leaf_step_says_so(self, tmp_path, monkeypatch):
        surface = self._surface(tmp_path, monkeypatch)
        run = run_with(chain("a", "b"))
        surface.store.save(run)
        surface.drop_step(run, run.steps[1])
        text = self._text(surface._body)
        assert "Removed $b" in text, (
            f"a successful removal that says nothing reads as a click that did "
            f"nothing: {text[:200]!r}")

    def test_the_control_is_reachable_in_the_built_tree(self, tmp_path, monkeypatch):
        """A handler that nothing can press is the dead-control defect.

        `test_dropping_a_depended_on_step_renders_the_refusal` calls
        `drop_step` directly, which passes however the button was wired - so the
        built tree is walked for the per-step tooltip, which is what a person
        actually sees and hovers.
        """
        surface = self._surface(tmp_path, monkeypatch)
        run = run_with(chain("a", "b", "c"))
        surface.store.save(run)
        # The panel renders on refresh, not on save: the store is written to
        # disk and nothing re-reads it until something asks. Saving first and
        # never refreshing builds the empty state, which is what the first
        # version of this test walked and found nothing in.
        surface.refresh()
        tips = []

        def walk(widget):
            tip = widget.get_tooltip_text()
            if tip:
                tips.append(tip)
            child = widget.get_first_child()
            while child is not None:
                walk(child)
                child = child.get_next_sibling()

        walk(surface._body)
        per_step = [t for t in tips if t.startswith("Remove the step ")]
        assert len(per_step) == len(run.steps), (
            f"{len(run.steps)} steps but {len(per_step)} per-step remove "
            f"controls in the built tree: {tips}")
        assert any("Remove this goal" in t for t in tips), (
            "the goal-level remove disappeared while adding the per-step one")

    def test_a_done_step_keeps_no_remove_button(self, tmp_path, monkeypatch):
        """A step that already ran has a result others read; taking it out would
        strand them. The row is not offered a control that must refuse."""
        surface = self._surface(tmp_path, monkeypatch)
        run = run_with(chain("a", "b"), index=1, results={"a": "done"})
        surface.store.save(run)
        surface.refresh()
        tips = []

        def walk(widget):
            tip = widget.get_tooltip_text()
            if tip:
                tips.append(tip)
            child = widget.get_first_child()
            while child is not None:
                walk(child)
                child = child.get_next_sibling()

        walk(surface._body)
        per_step = [t for t in tips if t.startswith("Remove the step ")]
        assert not any("$a" in t for t in per_step), (
            f"the step that already ran was offered a remove control, which "
            f"would strand the step reading its result: {per_step}")
        assert any("$b" in t for t in per_step), (
            f"the step that has not run yet lost its remove control too, so "
            f"there is no way to correct the rest of the plan: {per_step}")


class TestTheGoalLevelRemoveStillWorks:
    """A method a later edit deleted, and a commit shipped.

    `drop_step` was written by *replacing* the body of `remove` rather than
    being added beside it, so "Remove this goal" was left calling a method that
    no longer existed. Measured on the built surface before the fix:
    `hasattr(surface, 'remove')` -> **False**, and
    `surface.remove('r1')` -> **AttributeError**.

    **No test failed, because no test clicked it.** The tests drove `drop_step`
    directly - which is this repo's "a test that calls the function is not a
    test that the skill is reachable" rule, missed by one entry point. Two
    methods, one entry point each, and this class is the other one.
    """

    def _surface(self, tmp_path, monkeypatch):
        monkeypatch.setattr(G, "store_root", lambda: tmp_path / "goals")
        from shani_chronoa.gui.surfaces import goals as panel

        class _Surface(panel._GoalsSurface):
            def __init__(self):
                self._app = None
                self.store = G.GoalStore(G.store_root())
                self.executor = G.tool_executor()
                self.status_recorder = panel.common.StatusRecorder()
                self._body = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
                self.running = False
                self._notice = ""
                self._run_error = {}
                self.refresh()

        return _Surface()

    def _text(self, widget) -> str:
        out, stack = [], [widget]
        while stack:
            node = stack.pop()
            for getter in ("get_title", "get_subtitle", "get_text"):
                try:
                    value = getattr(node, getter)()
                except Exception:  # noqa: BLE001 - not every widget has each
                    value = None
                if isinstance(value, str) and value:
                    out.append(value)
            child = node.get_first_child()
            while child is not None:
                stack.append(child)
                child = child.get_next_sibling()
        return " ".join(out)

    def test_the_goals_remove_method_exists(self, tmp_path, monkeypatch):
        """The panel calls `self.remove` for every goal-level remove button, so a
        missing method is a button that raises, not one that is absent."""
        surface = self._surface(tmp_path, monkeypatch)
        assert hasattr(surface, "remove"), (
            "the Goals panel calls self.remove for every 'Remove this goal' "
            "button, and the method is gone")

    def test_it_deletes_the_run(self, tmp_path, monkeypatch):
        surface = self._surface(tmp_path, monkeypatch)
        run = run_with(chain("a", "b"))
        surface.store.save(run)
        assert [r.id for r in surface.store.list()] == ["r1"]
        surface.remove(run.id)
        assert surface.store.list() == [], "the run survived its removal"

    def test_a_removal_names_what_went(self, tmp_path, monkeypatch):
        surface = self._surface(tmp_path, monkeypatch)
        run = run_with(chain("a", "b"))
        surface.store.save(run)
        surface.refresh()
        surface.remove(run.id)
        assert "Removed the goal" in self._text(surface._body), (
            "a removal that says nothing reads as a click that did nothing")

    def test_a_removed_goals_error_is_dropped_too(self, tmp_path, monkeypatch):
        """An error for a run that no longer exists must not outlive it."""
        surface = self._surface(tmp_path, monkeypatch)
        run = run_with(chain("a", "b"))
        surface.store.save(run)
        surface._run_error[run.id] = "OSError: boom"
        surface.remove(run.id)
        assert surface._run_error == {}


class TestARemovedStepStaysVisibleAndComesBack:
    """open-intelligent-ui dims a removed stop rather than deleting it.

    `travel.css`:
    ```css
    .tv-stop.is-removed > .tv-photo, .tv-stop.is-removed .tv-stop-time,
    .tv-stop.is-removed .tv-stop-title, .tv-stop.is-removed .tv-stop-description
        { opacity: .45; }
    ```
    Its route panel also has an add/remove *toggle*, so a stop can go back.

    Chronoa's `remove_step` deleted the row outright, which the data half fixed
    (`GoalRun.removed`) and these tests pin: a step taken out is still listed,
    it is visibly not a live step, and there is a control that puts it back.
    """

    def _surface(self, tmp_path, monkeypatch):
        monkeypatch.setattr(G, "store_root", lambda: tmp_path / "goals")
        from shani_chronoa.gui.surfaces import goals as panel

        class _Surface(panel._GoalsSurface):
            def __init__(self):
                self._app = None
                self.store = G.GoalStore(G.store_root())
                self.executor = G.tool_executor()
                self.status_recorder = panel.common.StatusRecorder()
                self._body = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
                self.running = False
                self._notice = ""
                self._run_error = {}
                self.refresh()

        return _Surface()

    def _labels(self, widget) -> "list[str]":
        out, stack = [], [widget]
        while stack:
            node = stack.pop()
            for getter in ("get_title", "get_subtitle", "get_text"):
                try:
                    value = getattr(node, getter)()
                except Exception:  # noqa: BLE001 - not every widget has each
                    value = None
                if isinstance(value, str) and value:
                    out.append(value)
            child = node.get_first_child()
            while child is not None:
                stack.append(child)
                child = child.get_next_sibling()
        return out

    def _tooltips(self, widget) -> "list[str]":
        out, stack = [], [widget]
        while stack:
            node = stack.pop()
            try:
                tip = node.get_tooltip_text()
            except Exception:  # noqa: BLE001
                tip = None
            if tip:
                out.append(tip)
            child = node.get_first_child()
            while child is not None:
                stack.append(child)
                child = child.get_next_sibling()
        return out

    def test_the_step_is_kept_in_the_run(self, tmp_path, monkeypatch):
        """Data first: the step must survive `remove_step` for anything to draw."""
        run = run_with(chain("a", "b", "c"))
        updated, why = G.remove_step(run, "c", now=1.0)
        assert why == ""
        assert [s.variable_name for s in updated.steps] == ["a", "b"]
        assert [s.variable_name for s in updated.removed] == ["c"], (
            "the removed step was thrown away, so there is nothing to draw and "
            "nothing to put back")

    def test_a_removed_step_is_drawn_but_dimmed(self, tmp_path, monkeypatch):
        surface = self._surface(tmp_path, monkeypatch)
        run = run_with(chain("a", "b", "c"))
        updated, why = G.remove_step(run, "c", now=1.0)
        surface.store.save(updated)
        surface.refresh()
        labels = self._labels(surface._body)
        assert any("removed" in t for t in labels), (
            f"a removed step is not shown at all, so the person cannot see what "
            f"they took out: {labels}")

    def test_a_removed_row_is_not_a_live_row(self, tmp_path, monkeypatch):
        """Dimmed *and* marked, because opacity alone is a one-dimension cue."""
        surface = self._surface(tmp_path, monkeypatch)
        run = run_with(chain("a", "b", "c"))
        updated, _why = G.remove_step(run, "c", now=1.0)
        surface.store.save(updated)
        surface.refresh()
        rows = []
        stack = [surface._body]
        while stack:
            node = stack.pop()
            title = getattr(node, "get_title", lambda: None)()
            if isinstance(title, str) and "removed" in title:
                rows.append(node)
            child = node.get_first_child()
            while child is not None:
                stack.append(child)
                child = child.get_next_sibling()
        assert rows, "no row is marked removed"
        for row in rows:
            assert row.get_opacity() < 1.0, (
                "the removed row is at full opacity, so a dim alone does not "
                "distinguish it from a live step")
            assert "(removed)" in (row.get_title() or ""), (
                "the row is dimmed but does not say so in words")

    def test_there_is_a_way_back(self, tmp_path, monkeypatch):
        """The point of the dimmed row: a step removed by mistake is recoverable
        without planning the whole goal again."""
        surface = self._surface(tmp_path, monkeypatch)
        run = run_with(chain("a", "b", "c"))
        updated, _why = G.remove_step(run, "c", now=1.0)
        surface.store.save(updated)
        surface.refresh()
        tips = self._tooltips(surface._body)
        assert any("back" in t for t in tips), (
            f"a removed step offers no way back: {tips}")
        # The button's tooltip, which is what a sighted user sees. The
        # `AccessibleProperty.LABEL` carries "Restore step c" and that is a
        # different string — asserting the wrong one of the two is a test that
        # passes on a label nobody reads.
        assert any("$c" in t and "back" in t for t in tips), tips

    def test_restoring_through_the_panel_puts_it_back(self, tmp_path, monkeypatch):
        surface = self._surface(tmp_path, monkeypatch)
        run = run_with(chain("a", "b", "c"))
        updated, _why = G.remove_step(run, "c", now=1.0)
        surface.store.save(updated)
        surface.refresh()
        surface.restore_step(updated, updated.removed[0])
        saved = surface.store.load(run.id)
        assert [s.variable_name for s in saved.steps] == ["a", "b", "c"], (
            "the panel's restore control did not put the step back")
        assert saved.removed == (), "the step is still marked removed after restore"

    def test_restore_names_the_refusal(self, tmp_path, monkeypatch):
        surface = self._surface(tmp_path, monkeypatch)
        run = run_with(chain("a", "b", "c"))
        surface.store.save(run)
        surface.restore_step(run, run.steps[0])
        # `refresh()` draws a notice and then clears it, by design — it is a
        # one-shot, and the method calls refresh on its way out. So the rendered
        # body after the call is the assertion, and a *second* refresh has
        # nothing left to draw. The first version of this test read the `_notice`
        # attribute after it had already been cleared and passed on nothing.
        assert "$a is already in this plan" in " ".join(self._labels(surface._body)), (
            "restoring a step that is already there said nothing useful")

    def test_the_drop_notice_says_it_can_come_back(self, tmp_path, monkeypatch):
        """A removal that reads as final is a removal nobody will question."""
        surface = self._surface(tmp_path, monkeypatch)
        run = run_with(chain("a", "b", "c"))
        surface.store.save(run)
        surface.drop_step(run, run.steps[2])
        # Rendered by the method's own refresh; a second one would draw nothing.
        rendered = " ".join(self._labels(surface._body))
        assert "dimmed" in rendered and "put back" in rendered, (
            f"the removal notice does not say the step can be restored: "
            f"{rendered!r}")

    def test_every_subset_restores_to_the_original_order(self):
        """The property that makes restore trustworthy rather than lucky.

        Take out any prefix of the tail, in order, then put it all back: the
        result must be the original plan, with every dependency before its user.
        """
        original = chain("a", "b", "c", "d")
        run = run_with(original)
        for name in ("d", "c", "b"):          # reverse: each is a leaf when taken
            updated, why = G.remove_step(run, name, now=1.0)
            assert why == "", why
            run = updated
        for name in ("b", "c", "d"):
            updated, why = G.restore_step(run, name, now=2.0)
            assert why == "", why
            run = updated
        assert [s.variable_name for s in run.steps] == ["a", "b", "c", "d"]
        assert run.removed == ()
        settled = [s.variable_name for s in run.steps]
        for i, step in enumerate(run.steps):
            for dep in step.depends_on:
                assert dep in settled[:i], (
                    f"${step.variable_name} was restored before ${dep}")

    def test_a_json_round_trip_keeps_the_removed_step(self):
        run = run_with(chain("a", "b", "c"))
        updated, _why = G.remove_step(run, "c", now=1.0)
        back = G.GoalRun.from_json(updated.to_json())
        assert [s.variable_name for s in back.removed] == ["c"]
        restored, why = G.restore_step(back, "c", now=2.0)
        assert why == ""
        assert [s.variable_name for s in restored.steps] == ["a", "b", "c"]

    def test_an_old_run_without_the_field_loads(self):
        """The store is on disk; a run saved before the field existed must not
        become unreadable."""
        raw = ('{"id":"old","goal":"g","phase":"running","steps":['
               '{"thought":"t","tool_name":"x","tool_arguments":{},'
               '"variable_name":"a"}],"index":0}')
        run = G.GoalRun.from_json(raw)
        assert run.removed == ()
