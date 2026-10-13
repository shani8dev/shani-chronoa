"""The task card shows the plan, in the chat, where you are looking.

The card reads `todo_list` and nothing else, so a plan plan mode made - the
run that `goals.plan_from_refusals` stores when you answer "keep planning" -
was visible in the Goals panel and the rail and **nowhere in the conversation**.
Both of those are one scroll away from where a person is actually looking while
the assistant is still talking, and a plan you cannot see is a plan you cannot
correct before it runs.

**The state is `awaiting`, not `pending`.** A plan waiting for a person and a
todo waiting to be done are different things; drawing them the same way is how
a card says "in hand" about something nobody has agreed to.
"""

import sys
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "usr/lib/shani-chronoa"))

import pytest  # noqa: E402

from shani_chronoa import goals  # noqa: E402
from shani_chronoa.gui import task_card  # noqa: E402


@pytest.fixture(autouse=True)
def _isolated_store(tmp_path, monkeypatch):
    """**Every test gets its own goals store, whether or not it asked for one.**

    Without this, `_plans_waiting` reads `goals.store_root()`, which resolves
    from `$XDG_STATE_HOME` at call time - so a test that never takes the `store`
    fixture still reads whatever the previous test left behind. Measured: twelve
    plans accumulated, and two tests asserted an empty list against it.

    Autouse rather than per-test because the function under test does not take a
    store; it finds one. A fixture only the *aware* tests ask for is not a
    fixture.
    """
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state"))


@pytest.fixture
def store():
    return goals.GoalStore(goals.store_root())


#: Real tool names, and deliberately **not** `tool0`, `tool1`. A fixture whose
#: every step names a different tool can never exercise the dedup, so the test
#: written for it passes whether or not the collapse exists - the same trap as
#: `"first, second, third"` already being alphabetical, which the Compare
#: panel's own tests hit.
_TWO_WRITES = [("write_text_file", {"path": "/a"}),
               ("write_text_file", {"path": "/c"}),
               ("delete_file", {"path": "/b"})]


def _plan(store, goal, steps=2, refusals=None):
    """A stored plan, in this test's own store.

    **No `run_id`.** `plan_from_refusals` takes the goal and the refusals and
    nothing else - it owns the id, which is right, because a caller naming the
    id is a caller naming its way into someone else's run.
    """
    return goals.plan_from_refusals(
        goal, refusals if refusals is not None else
        [(f"tool{i}", {"path": f"/f{i}"}) for i in range(steps)])


# -- a plan shows up --------------------------------------------------------


def test_a_stored_plan_is_in_the_card(store):
    _plan(store, "clean up my scratch files", steps=3)
    rows = task_card._plans_waiting()
    assert len(rows) == 1, rows
    assert "clean up my scratch files" in rows[0]["content"], rows[0]


def test_the_row_says_how_many_steps_and_that_nothing_happened():
    """**"3 steps, nothing has been done"** is the claim the card exists to
    make. A row reading just the goal would sit above a machine that has been
    untouched and look like work already under way."""
    _plan(store, "clean up my scratch files", steps=3)
    content = task_card._plans_waiting()[0]["content"]
    assert "3 steps" in content, content
    assert "nothing has been done" in content, content


def test_one_step_is_not_plural():
    """The property is the pluralisation, so it is asserted as the count and
    the word rather than as a comma.

    It was `"1 step," in content` - which pinned the punctuation between the
    count and whatever follows it, so adding the tool names failed a test about
    grammar. **A test that reads the shape of the sentence stops working when
    the sentence gets more informative.**
    """
    _plan(store, "rename one file", steps=1)
    content = task_card._plans_waiting()[0]["content"]
    assert "1 step" in content, content
    assert "1 steps" not in content and "steps:" not in content, content


def test_a_plan_names_what_it_would_do():
    """**A count is not a plan.** Three steps and nothing else is the sentence
    for a run whose shape cannot be seen, and approving it means saying yes to
    three things nobody was shown. The tools are named, in the order the model
    asked for them."""
    _plan(store, "tidy up", refusals=_TWO_WRITES)
    content = task_card._plans_waiting()[0]["content"]
    assert "write_text_file" in content, content
    assert "delete_file" in content, content


def test_the_same_tool_asked_three_times_is_named_once():
    """**Three writes are three steps and one line to read.**

    The count stays three, because the plan really does perform three writes -
    collapsing that would understate what approval means. The *names* collapse,
    because a model that asks for the same write five times has still described
    one write, and a list of it is noise.

    **The control that gives this teeth:** `test_a_plan_names_what_it_would_do`
    uses the same fixture with the same repeated `write_text_file`, so a card
    that simply dropped duplicates with no count at all would fail here and pass
    there."""
    _plan(store, "tidy up", refusals=[
        ("write_text_file", {"path": "/a"}),
        ("write_text_file", {"path": "/b"}),
        ("write_text_file", {"path": "/c"}),
    ])
    content = task_card._plans_waiting()[0]["content"]
    assert content.count("write_text_file") == 1, content
    assert "3 steps" in content, content


def test_a_step_with_no_name_reads_as_a_count_not_as_a_colon():
    """The empty branch has a witness, because a mutation removing it left the
    file **fully green**.

    `_what_it_would_do` guards on `if not tools` because `tools` is built from
    `getattr(step, "tool_name", "")`, so a step with no name gives `tools == []`
    and `", ".join([])` is `""` - the row would read *"goal - : , nothing has
    been done"*, a colon and nothing after it.

    **Driven through the helper, and that is the honest part.** The only producer
    of these runs, `goals.plan_from_refusals`, does `if not tool: continue`, so
    it *refuses* to build a nameless step - writing the test through it gave
    `IndexError` on an empty list, which is what sent me here. So the guard is
    reachable from a `PlannedStep` a caller built by another route, which is
    exactly the case a guard is for: the row must not render as a dangling colon
    because some producer was more permissive than this one.

    The mutation that removed the branch is in this file's header's list of what
    has to keep failing.
    """
    import types

    nameless = types.SimpleNamespace(tool_name="")
    said = task_card._what_it_would_do([nameless, nameless])
    assert said == "2 steps", said
    assert ":" not in said, said


def test_a_plan_with_more_tools_than_fit_still_says_how_many():
    """Three shown, a count for the rest - and **the four hidden ones are
    actually hidden**.

    The third assertion is the one the mutation run asked for. It asserted only
    that "and 4 more" appeared, which a card that printed all seven *and then*
    the count also satisfies - so widening `tools[:3]` to `tools` left the file
    **fully green**. The list is built by joining all of `tools` before the slice
    is applied, so the check has to be on what is **not** there.

    Why the bound exists at all: a plan naming twenty tools renders a row two
    hundred characters wide on a card that has a footer to get to.
    """
    _plan(store, "sweep", refusals=[
        (f"tool{i}", {"path": f"/f{i}"}) for i in range(7)])
    content = task_card._plans_waiting()[0]["content"]
    assert "and 4 more" in content, content
    assert "7 steps" in content, content
    assert "tool3" not in content, content


def test_a_plan_reads_as_waiting_for_you_not_as_todo():
    _plan(store, "tidy up")
    assert task_card._plans_waiting()[0]["status"] == "awaiting", (
        "a plan waiting for a person must not be drawn as a todo")


def test_the_awaiting_state_has_a_word_and_an_icon():
    """A state with no row entry falls through to the fallback icon, so the
    card would show a warning triangle for a plan that is simply waiting."""
    assert "awaiting" in task_card._STATES
    icon, prefix = task_card._STATES["awaiting"]
    assert prefix, "the awaiting state has no word beside it"
    theme = Path("/usr/share/icons/Adwaita")
    if theme.is_dir():
        assert any(theme.rglob(f"{icon}.svg")), f"{icon} is not in Adwaita"


# -- what must not show up --------------------------------------------------


def test_a_todo_run_is_not_a_plan():
    """A run waiting on something else is the Goals panel's business; the card
    is for the plan and the todos."""
    run = goals.new_run("something else", ())
    run = run._replace(phase=goals.Phase.AWAITING,
                       awaiting_reason="waiting on a download")
    goals.GoalStore(goals.store_root()).save(run)
    assert task_card._plans_waiting() == []


def test_a_plan_that_is_under_way_is_not_waiting_for_you(store):
    _plan(store, "already approved")
    for run in goals.GoalStore(goals.store_root()).list():
        goals.GoalStore(goals.store_root()).save(
            run._replace(phase=goals.Phase.RUNNING))
    assert task_card._plans_waiting() == [], (
        "a plan already running still claimed to be waiting for you")


def test_an_empty_store_yields_no_rows(store):
    assert task_card._plans_waiting() == []


def test_a_store_that_cannot_be_read_yields_no_rows(monkeypatch):
    """A plan that cannot be read is not a reason to take the chat down, and it
    is certainly not the same as having no plan - so it logs and returns
    nothing."""
    import shani_chronoa.goals as goals_module

    class _Broken:
        @staticmethod
        def list():
            raise OSError("the disk is gone")

    monkeypatch.setattr(goals_module, "GoalStore", lambda _r: _Broken())
    assert task_card._plans_waiting() == []


# -- the two sources together ----------------------------------------------


def _gate(monkeypatch, on):
    """Turn the `todo-list-enabled` setting on or off, for real."""
    from shani_chronoa import config as config_mod

    monkeypatch.setattr(
        config_mod, "ChronoaConfig",
        lambda: SimpleNamespace(get_bool=lambda key, default=False: (
            on if key == "todo-list-enabled" else default)))
    return monkeypatch


def test_the_card_shows_todos_and_plans_together(monkeypatch, store):
    """One card, two stores - and the todo half still works, because the change
    must not have replaced it."""
    from shani_chronoa.skills import todo_list

    _gate(monkeypatch, True)
    _plan(store, "a plan", steps=1)
    monkeypatch.setattr(
        todo_list, "open_tasks",
        lambda: ([{"status": "pending", "content": "buy milk"}], ""))

    rows = task_card._outstanding()
    assert len(rows) == 2, rows
    assert {item["status"] for item in rows} == {"awaiting", "pending"}, rows
    assert any("buy milk" in r["content"] for r in rows), rows


def test_a_completed_todo_does_not_come_back_alongside_the_plan(
        monkeypatch, store):
    _gate(monkeypatch, True)
    _plan(store, "a plan", steps=1)
    from shani_chronoa.skills import todo_list

    monkeypatch.setattr(
        todo_list, "open_tasks",
        lambda: ([{"status": "completed", "content": "done already"}], ""))
    rows = task_card._outstanding()
    assert all("done already" not in r["content"] for r in rows), rows


def test_a_plan_shows_even_with_the_task_list_switched_off(monkeypatch,
                                                              store):
    """**The bug this test used to assert the opposite of.** It required the
    plan to disappear when `todo-list-enabled` was off - which is its default -
    so a plan was invisible in every chat until you changed an unrelated
    setting. Measured by rendering the card: `refresh()` reported "Nothing
    outstanding" with a plan in the store.

    The gate is right for todos and wrong for plans: nobody opts into the task
    list by *asking the assistant to work out what it would do*. A plan is the
    answer to a question you asked, so hiding it behind that setting hides the
    answer.
    """
    _gate(monkeypatch, False)
    _plan(store, "a plan", steps=1)
    rows = task_card._outstanding()
    assert len(rows) == 1, rows
    assert "a plan" in rows[0]["content"], rows


def test_the_list_switched_off_still_hides_todos(monkeypatch, store):
    _gate(monkeypatch, False)
    from shani_chronoa.skills import todo_list

    monkeypatch.setattr(
        todo_list, "open_tasks",
        lambda: ([{"status": "pending", "content": "buy milk"}], ""))
    rows = task_card._outstanding()
    assert all("buy milk" not in r["content"] for r in rows), rows


def test_an_unreadable_todo_list_does_not_hide_a_plan(monkeypatch, store):
    """**The name said this and the body said the opposite.** It asserted
    `rows == []` while claiming not to hide the plan - written before the gate
    was moved, and left asserting the old behaviour after.

    The two sources fail differently on purpose now. A todo store that cannot
    be read is a todo store that loses its half of the card; a plan is still
    worth showing, because it was produced by a question the person asked and
    the answer is on disk whether the todo list is readable or not."""
    _gate(monkeypatch, True)
    _plan(store, "a plan", steps=1)
    from shani_chronoa.skills import todo_list

    monkeypatch.setattr(todo_list, "open_tasks",
                        lambda: (None, "the list is corrupt"))
    rows = task_card._outstanding()
    assert len(rows) == 1, (
        f"a plan was hidden by a broken todo list: {rows}")
    assert "a plan" in rows[0]["content"], rows