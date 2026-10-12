"""Plan mode asks you, rather than describing what it would have done.

Plan mode's refusal used to tell the model to *"describe the steps instead"* -
which hands the decision to the one party with an interest in being wrong
about what you want. Now the person is asked, with the actual tool and the
actual arguments, and a yes runs that call through the ordinary dispatch path.

**Everything that is not an explicit yes is a refusal.** No presenter, a
dismissed prompt, a timeout, an empty answer - all of them mean *no answer was
given*, and this app's rule is that an unanswered question is never consent.
That is the whole reason this file exists: the feature adds a way for a tool to
run that did not run before.
"""

import sys
import threading
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "usr/lib/shani-chronoa"))

import pytest  # noqa: E402

from shani_chronoa import ask_bridge, planmode  # noqa: E402


@pytest.fixture(autouse=True)
def _isolate():
    """Plan mode off and no presenter left behind.

    A leaked `_keep_planning` flag would silently refuse the next test's
    questions, and a leaked presenter would make a test that expects a refusal
    get whatever answer another test left in it.
    """
    previous = ask_bridge._presenter
    planmode.begin_turn()
    planmode.set_enabled(False)
    yield
    ask_bridge.set_presenter(previous)
    planmode.begin_turn()
    planmode.set_enabled(False)


def _answers_with(reply, log=None):
    """A presenter that always answers `reply`, recording the questions."""
    def presenter(question, options):
        if log is not None:
            log.append((question, list(options)))
        event = threading.Event()
        event.chronoa_answer = reply
        return event
    return presenter


# -- the two answers --------------------------------------------------------


def test_do_it_now_runs_the_call():
    ask_bridge.set_presenter(_answers_with("Do it now"))
    assert planmode.ask_to_proceed("write_text_file", {"path": "/a"}) is True


def test_keep_planning_refuses_and_records_the_step():
    ask_bridge.set_presenter(_answers_with("Keep planning"))
    assert planmode.ask_to_proceed("delete_file", {"path": "/b"}) is False
    assert [t for t, _a in planmode.refusals()] == ["delete_file"]


def test_a_refusal_still_records_the_step():
    """A refused step that is not recorded is a step the stored plan does not
    mention - which is the failure this whole change exists to remove."""
    ask_bridge.set_presenter(_answers_with("Keep planning"))
    planmode.ask_to_proceed("edit_file", {"path": "/c", "find": "x"})
    tool, arguments = planmode.refusals()[0]
    assert tool == "edit_file"
    assert arguments == {"path": "/c", "find": "x"}


# -- everything else is a refusal ------------------------------------------


def test_no_answer_given_is_never_consent():
    """Dismissed, timed out, or empty - all the same, and all a refusal."""
    ask_bridge.set_presenter(_answers_with(""))
    assert planmode.ask_to_proceed("write_text_file", {"path": "/a"}) is False
    assert planmode.refusals(), "a refusal with no record"


def test_an_answer_that_is_not_one_of_the_two_words_is_refused():
    """A presenter that returns something unexpected must not become a yes."""
    ask_bridge.set_presenter(_answers_with("yes"))
    assert planmode.ask_to_proceed("write_text_file", {"path": "/a"}) is False


def test_nobody_to_ask_is_not_permission():
    ask_bridge.set_presenter(None)
    assert planmode.ask_to_proceed("write_text_file", {"path": "/a"}) is False
    assert planmode.refusals(), (
        "with nobody to ask, the step vanished instead of being refused")


# -- one answer stands for the turn ----------------------------------------


def test_keep_planning_silences_the_rest_of_the_turn():
    """**Why this exists.** Asking again for every remaining step turns "what
    would you do about this?" into ten dialogs - the opposite of what they asked
    for. The silence is the answer; the steps still land in the plan."""
    log = []
    ask_bridge.set_presenter(_answers_with("Keep planning", log))
    results = [planmode.ask_to_proceed(f"tool{i}", {}) for i in range(5)]
    assert results == [False] * 5, results
    assert len(log) == 1, f"asked {len(log)} times for one answer"
    assert len(planmode.refusals()) == 5, (
        "the silenced steps are not in the plan, so the plan is incomplete")


def test_do_it_now_does_not_silence_the_next_one():
    """The answer was about that call, not about the turn."""
    log = []
    ask_bridge.set_presenter(_answers_with("Do it now", log))
    results = [planmode.ask_to_proceed(f"tool{i}", {}) for i in range(3)]
    assert results == [True, True, True], results
    assert len(log) == 3, log


def test_a_new_turn_asks_again():
    """Otherwise the first answer would follow you around the whole session."""
    ask_bridge.set_presenter(_answers_with("Keep planning"))
    planmode.ask_to_proceed("tool0", {})
    planmode.begin_turn()
    log = []
    ask_bridge.set_presenter(_answers_with("Do it now", log))
    assert planmode.ask_to_proceed("tool1", {}) is True
    assert len(log) == 1, "the new turn was still silenced"


# -- the question itself ----------------------------------------------------


def test_the_question_names_the_tool_and_what_it_wanted():
    """A dialog reading \"Run it now?\" for an unknown call is a consent prompt
    for something the user cannot see."""
    log = []
    ask_bridge.set_presenter(_answers_with("Do it now", log))
    planmode.ask_to_proceed("write_text_file",
                             {"path": "/tmp/report.txt", "text": "hi"})
    question, options = log[0]
    assert "write_text_file" in question, question
    assert "path" in question and "text" in question, question
    assert options == list(planmode.ASK_OPTIONS), options


def test_do_it_now_is_the_first_option():
    """The common answer to \"what would you do?\" followed by silence is yes,
    and a dialog leading with \"no\" teaches people that plan mode is a wall."""
    assert planmode.ASK_OPTIONS[0] == "Do it now", planmode.ASK_OPTIONS


def test_a_call_with_no_arguments_still_asks():
    log = []
    ask_bridge.set_presenter(_answers_with("Keep planning", log))
    planmode.ask_to_proceed("some_tool", {})
    question, _options = log[0]
    assert "some_tool" in question, question
    assert "It wanted:" not in question, (
        "a call with no arguments should not claim it wanted some")


# -- nothing changes when plan mode is off ---------------------------------


def test_nothing_is_asked_when_plan_mode_is_off():
    """**The gate is `blocked_reason`, and it is empty when plan mode is off** -
    so the dispatch site never reaches the ask at all. The first version of
    this test asserted that `ask_to_proceed` returns True with plan mode off,
    which is simply wrong: it asks and obeys the answer whatever the mode is,
    and the thing that stops it being a way to run anything unasked is the
    refusal check in front of it."""
    log = []
    ask_bridge.set_presenter(_answers_with("Do it now", log))
    assert planmode.is_enabled() is False
    assert planmode.blocked_reason("write_text_file") == "", (
        "plan mode is off but the call is blocked")
    assert not log, "a question was asked while plan mode was off"