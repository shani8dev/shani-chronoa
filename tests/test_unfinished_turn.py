"""Telling the user their turn never finished, and only when it really did.

Chronoa already repairs an interrupted turn for the *provider*:
`Assistant.build_messages()` runs `history_repair.clean_history()` on every
request, so a dangling tool call can never reach a model. That repair is silent,
and correctly so for the model - but it leaves the person looking at their own
unanswered question with no reason given.

On this platform that is not an edge case. ShaniOS keeps `/var` on tmpfs, so a
reboot or a blue/green slot switch kills the app mid-turn routinely while the
transcript on `@home` survives intact. The turns that end that way are the
ordinary ones.

The hard half is not detecting the crash; it is **not** accusing the user of
losing a turn they deliberately cancelled. `close_interrupted_turn()`
synthesizes and persists a tool result for a cancelled, out-of-budget or
malformed turn, so those transcripts end in a `tool` message - a shape a naive
"does this end on a user message" check would report as a crash. That false
positive is the reason this file exists as mostly negative tests.
"""

import json
import pathlib

import pytest

from shani_chronoa import assistant as assistant_mod
from shani_chronoa import conversation_store, history_repair
from shani_chronoa.history_repair import (
    UNFINISHED_AWAITING_ANSWER,
    UNFINISHED_MID_TOOLS,
    unfinished_notice,
    unfinished_turn,
)


USER = {"role": "user", "content": "what is on my calendar?"}
CALL = {"role": "assistant", "content": "",
        "tool_calls": [{"id": "c1", "function": {"name": "calendar_events", "arguments": {}}}]}
SYNTHESIZED = {"role": "tool", "tool_call_id": "c1",
               "content": "not executed: the turn was interrupted before its result was recorded"}
ANSWER = {"role": "assistant", "content": "You have three meetings."}


# --- the detector ----------------------------------------------------------


@pytest.mark.parametrize("messages,expected,why", [
    ([], "", "no transcript"),
    ([USER], UNFINISHED_AWAITING_ANSWER, "died before the model replied"),
    ([USER, CALL], UNFINISHED_MID_TOOLS, "died after the calls, before any result"),
    ([USER, CALL, SYNTHESIZED], "", "cancelled on purpose - the user did this"),
    ([USER, ANSWER], "", "a finished turn"),
    ([ANSWER], "", "a reply with no question is still a finished turn"),
    ([{"role": "system", "content": "..."}], "", "a system prompt is not a turn"),
    ([{"role": "system", "content": "..."}, USER], UNFINISHED_AWAITING_ANSWER,
     "the system prompt must not hide the turn after it"),
])
def test_unfinished_turn_reads_the_tail(messages, expected, why):
    assert unfinished_turn(messages) == expected, why


def test_a_deliberately_cancelled_turn_is_never_reported_as_a_crash():
    """The false positive this whole feature could have shipped.

    Barge-in, the time budget and three malformed calls all end a turn through
    `close_interrupted_turn()`, which records a synthesized result *on disk*
    precisely so the repair survives the process. So a cancelled transcript
    ends in a `tool` message and is indistinguishable from a completed tool
    call by role alone. Telling a user their turn was lost when they pressed
    stop themselves is the confident wrong answer this repository documents at
    length.
    """
    assert unfinished_notice([USER, CALL, SYNTHESIZED]) == ""


def test_the_notice_says_the_machine_stopped_not_that_the_question_failed():
    """A person reading this has to know nothing is wrong with their question."""
    for shape in ([USER], [USER, CALL]):
        notice = unfinished_notice(shape)
        assert notice, shape
        assert "stopped" in notice
        # It must not read as a failure of the request or a scolding.
        assert "error" not in notice.lower()
        assert "invalid" not in notice.lower()


def test_junk_in_the_transcript_does_not_crash_the_detector():
    """`conversation_store.load()` replays whatever parses, so a hand-edited or
    half-written line must not take the window down on restore."""
    assert unfinished_turn(["not a dict", None, USER]) == UNFINISHED_AWAITING_ANSWER
    assert unfinished_turn([{"role": "user"}]) == UNFINISHED_AWAITING_ANSWER
    assert unfinished_turn([{}]) == ""


@pytest.fixture
def store_root(tmp_path, monkeypatch):
    """The real session dir, redirected - `_follow_active_conversation` reads it
    through `conversation_store.session_dir()`, so a tmp path passed by hand is
    simply not where the app looks. This is why the first version of the window
    test announced nothing and still looked like a wiring problem."""
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "data"))
    return conversation_store.session_dir()


def _app_over(assistant):
    """A stand-in app carrying the real `_follow_active_conversation`."""
    from types import SimpleNamespace

    from shani_chronoa.app import ChronoaApplication

    statuses = []
    window = SimpleNamespace(show_conversation=lambda turns: None,
                             set_status=lambda text: statuses.append(text),
                             set_response=lambda *_: None)
    app = SimpleNamespace(assistant=assistant, window=window)
    app._follow_active_conversation = (
        lambda force=False: ChronoaApplication._follow_active_conversation(app, force))
    return app, statuses


# --- end to end, through a real Assistant ---------------------------------


def _assistant(tmp_path, messages):
    """A real Assistant over a transcript written the way `append()` writes one."""
    path = pathlib.Path(tmp_path) / "ses.jsonl"
    path.write_text("".join(json.dumps(m) + "\n" for m in messages), encoding="utf-8")
    return assistant_mod.Assistant(llm=None, session_path=path), path


def test_restoring_a_cut_off_turn_is_reported(tmp_path):
    assistant, _ = _assistant(tmp_path, [USER, CALL])
    assert assistant.restored_interruption == (
        history_repair.UNFINISHED_NOTICE[UNFINISHED_MID_TOOLS])


def test_restoring_a_finished_turn_reports_nothing(tmp_path):
    assistant, _ = _assistant(tmp_path, [USER, ANSWER])
    assert assistant.restored_interruption == ""


def test_switching_sessions_reports_each_one_on_its_own_terms(tmp_path):
    """Two conversations, one cut off and one not - the point of switching."""
    assistant, cut = _assistant(tmp_path, [USER, CALL])
    whole = pathlib.Path(tmp_path) / "whole.jsonl"
    whole.write_text(json.dumps(USER) + "\n" + json.dumps(ANSWER) + "\n", encoding="utf-8")

    assistant.switch_session(whole)
    assert assistant.restored_interruption == ""

    assistant.switch_session(cut)
    assert assistant.restored_interruption != ""


def test_a_new_turn_clears_the_notice(tmp_path):
    """Otherwise a caller reading it mid-session reports a turn already answered.

    Driven through the real `handle()`, because the first version of this test
    set the attribute by hand and asserted it was empty - which passes whether or
    not `handle()` clears it. That is the "a control that cannot fail" trap: 16
    green tests and this behaviour unverified.
    """
    import asyncio

    assistant, _ = _assistant(tmp_path, [USER, CALL])
    assert assistant.restored_interruption

    # `llm=None`, so the turn cannot answer and raises on its way out. The clear
    # happens before the first model call precisely so it still happens here -
    # asserting through the failure is what makes this a test of the ordering.
    with pytest.raises(Exception):
        asyncio.run(assistant.handle("and what time is it?"))

    assert assistant.restored_interruption == ""


def test_the_window_is_told(store_root):
    """The status line is the surface; nothing else makes this visible.

    Drives the real `ChronoaApplication._follow_active_conversation`, so this
    exercises the wiring rather than the helper it calls.
    """
    sid = conversation_store.new_session(store_root)
    (store_root / f"{sid}.jsonl").write_text(
        json.dumps(USER) + "\n" + json.dumps(CALL) + "\n", encoding="utf-8")

    app, statuses = _app_over(assistant_mod.Assistant(llm=None))   # let the switch supply it
    app._follow_active_conversation(force=True)

    assert statuses, "the status line was never set at all"
    assert any("stopped" in text for text in statuses), (
        f"the interruption never reached the window: {statuses!r}")


def test_a_finished_conversation_leaves_the_status_empty(store_root):
    """The negative control for the window test above.

    Without it, a `_follow_active_conversation` that announced something
    unconditionally would pass that one.
    """
    sid = conversation_store.new_session(store_root)
    (store_root / f"{sid}.jsonl").write_text(
        json.dumps(USER) + "\n" + json.dumps(ANSWER) + "\n", encoding="utf-8")

    app, statuses = _app_over(assistant_mod.Assistant(llm=None))
    app._follow_active_conversation(force=True)

    assert statuses == [""], f"a finished conversation announced something: {statuses!r}"


def test_the_conversations_list_carries_it_too(store_root):
    """So a damaged conversation can be spotted without opening each one.

    The transcript is already loaded there for the search haystack, so this costs
    nothing. Asserted on a real button's tooltip rather than on rendered pixels,
    because this repository has already deleted a visual suite that could not
    fail - and a tooltip is text with no layout cost, which is why the notice is
    not an `Adw.ActionRow` subtitle (libadwaita 1.5 does not wrap those).
    """
    import gi

    gi.require_version("Gtk", "4.0")
    gi.require_version("Adw", "1")
    from gi.repository import Adw

    from shani_chronoa.gui.surfaces import conversations as conversations_surface

    Adw.init()
    sid = conversation_store.new_session(store_root)
    (store_root / f"{sid}.jsonl").write_text(
        json.dumps(USER) + "\n" + json.dumps(CALL) + "\n", encoding="utf-8")

    # `__new__` rather than a real surface build: this is about what `_add_row`
    # puts on the button, and constructing the whole view would drag in the model
    # list, the search entry and the window for no extra signal.
    view = conversations_surface._ConversationsView.__new__(
        conversations_surface._ConversationsView)
    view._group = Adw.PreferencesGroup()
    view._row_widgets = []

    view._add_row({"id": sid, "title": "cut short", "updated": 0, "active": True},
                  "haystack",
                  history_repair.UNFINISHED_NOTICE[UNFINISHED_MID_TOOLS])
    (_row, open_btn, _delete, _hay) = view._row_widgets[0]
    tooltip = open_btn.get_tooltip_text()

    assert "stopped" in tooltip, f"the notice never reached the button: {tooltip!r}"
    assert "Open this conversation" in tooltip, "and it displaced the plain tooltip"

    # And a whole conversation must not claim anything happened to it.
    view._row_widgets.clear()
    view._add_row({"id": sid, "title": "whole", "updated": 0, "active": True},
                  "haystack", "")
    (_r2, open_btn2, _d2, _h2) = view._row_widgets[0]
    assert open_btn2.get_tooltip_text() == "Open this conversation"
