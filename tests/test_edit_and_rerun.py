"""Edit an earlier turn and rerun from there.

`regenerate` re-asks the *last* question. This is the one every assistant UI has
and this one did not: pick turn 7 of 20, change the question, and everything
after it stops existing.

**Two stores have to agree, and neither owns the other.** The transcript is a
list of widgets the window built; the history is a list of messages the
assistant built. The window can only reach its own, so it *asks* over a signal
and refuses to cut the screen when the answer is no. These tests drive both
halves, because a test that only truncates widgets would pass while the model
kept answering as though nothing had been removed.
"""

import itertools
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "usr/lib/shani-chronoa"))

import gi  # noqa: E402

gi.require_version("Gtk", "4.0")

from shani_chronoa.gui import widgets as widgets_module  # noqa: E402
from shani_chronoa.gui import window as window_module  # noqa: E402


def _transcript():
    view = widgets_module.TranscriptView()
    view.add_user_turn("first question")
    view.add_assistant_turn("first answer")
    view.add_user_turn("second question")
    view.add_assistant_turn("second answer")
    view.add_user_turn("third question")
    view.add_assistant_turn("third answer")
    return view


def _row_texts(view):
    out = []
    child = view._rows.get_first_child()
    while child is not None:
        out.append(child)
        child = child.get_next_sibling()
    return out


# --------------------------------------------------------------------------
# the transcript half


def test_rewinding_removes_the_turn_and_everything_after_it():
    view = _transcript()
    removed = view.truncate_from_turn(2)

    assert removed == [("user", "second question"), ("assistant", "second answer"),
                       ("user", "third question"), ("assistant", "third answer")]
    assert view.visible_turns() == [("user", "first question"),
                                    ("assistant", "first answer")]


def test_rewinding_to_a_turn_that_is_not_there_removes_nothing():
    """The dangerous direction is a bad index silently clearing the screen."""
    view = _transcript()
    for bad in (-1, 99, "two", None):
        assert view.truncate_from_turn(bad) is None, f"index {bad!r} cut the transcript"
    assert len(view.visible_turns()) == 6


def test_a_tool_card_is_inside_its_turn_so_it_cannot_be_orphaned():
    """**Tool cards are children of the assistant turn's own box, not rows of
    their own** - measured, and the reason it was worth measuring.

    The obvious worry about "rows are not turns" is a tool call left stranded
    above an answer that no longer follows it. That cannot happen here:
    `add_tool_call` appends to the current turn's block box, so cutting the turn
    cuts its tool cards with it by construction, with nothing to get wrong.

    This test pins that property rather than the one I assumed, because a future
    change that promoted tool cards to rows would need to make truncation keep
    track of them - and nothing else here would notice.
    """
    view = _transcript()
    view.add_tool_call("get_datetime", {}, "the time")
    blocks = view._blocks
    assert blocks is not None, "no current assistant turn to attach a tool call to"

    children = []
    child = blocks.get_first_child()
    while child is not None:
        children.append(child)
        child = child.get_next_sibling()
    assert len(children) > 1, (
        "the tool call did not land inside the turn's own box, so it is a row of "
        "its own and this test is now wrong for the wrong reason")

    view.truncate_from_turn(len(view.visible_turns()) - 1)
    assert blocks.get_parent() is None, (
        "the turn was removed from the transcript but its box is still parented")


def test_a_compaction_notice_belongs_to_the_turn_it_describes():
    """A notice is a note *about* a turn, so it lives inside that turn.

    Measured, having assumed the opposite: `add_notice_row` appends to the
    current assistant turn's block box rather than adding a row, exactly as a
    tool card does. So it cannot be left floating above a conversation that has
    been rewound past the turn it was describing.
    """
    view = _transcript()
    before = len(_row_texts(view))
    view.add_notice_row("earlier turns were summarised", kind="compaction")

    assert len(_row_texts(view)) == before, (
        "the notice became a row of its own, so it can outlive its turn")
    owner = view._blocks
    assert owner is not None

    view.truncate_from_turn(5)          # remove the "third answer" turn
    assert owner.get_parent() is None, (
        "the turn was cut but the notice inside it survived")


def test_rewinding_twice_from_the_same_view_is_consistent():
    view = _transcript()
    view.truncate_from_turn(4)          # drop q3, a3
    removed = view.truncate_from_turn(1)
    assert removed == [("assistant", "first answer"), ("user", "second question"),
                       ("assistant", "second answer")]
    assert view.visible_turns() == [("user", "first question")]
    assert len(_row_texts(view)) == 1, "a row outlived the turns that owned it"


def test_clearing_the_transcript_forgets_the_recorded_positions():
    """A stale row index would truncate rows belonging to a later conversation."""
    view = _transcript()
    view.truncate_from_turn(2)
    view.clear()
    view.add_user_turn("brand new")
    view.add_assistant_turn("new answer")
    assert view.truncate_from_turn(0) == [("user", "brand new"),
                                          ("assistant", "new answer")]


# --------------------------------------------------------------------------
# the assistant half


def _assistant_with_history():
    class _Fake:
        pass
    from shani_chronoa import assistant as assistant_module
    fake = _Fake()
    fake._history = [
        {"role": "system", "content": "you are Chronoa"},
        {"role": "user", "content": "first question"},
        {"role": "assistant", "content": "first answer"},
        {"role": "user", "content": "second question"},
        {"role": "assistant", "content": "second answer"},
        {"role": "tool", "content": "a tool result"},
        {"role": "assistant", "content": "second answer continued"},
    ]
    fake.rewind_to_user_text = (
        lambda text: assistant_module.Assistant.rewind_to_user_text(fake, text))
    return fake


def test_the_history_is_cut_at_the_right_user_turn():
    fake = _assistant_with_history()
    assert fake.rewind_to_user_text("second question") is True
    assert [m["content"] for m in fake._history] == [
        "you are Chronoa", "first question", "first answer"]


def test_the_system_prompt_is_never_rewound_away():
    """The system message is `_history[0]` and the loop starts at index 1."""
    fake = _assistant_with_history()
    fake.rewind_to_user_text("first question")
    assert fake._history[0] == {"role": "system", "content": "you are Chronoa"}


def test_the_tool_result_goes_with_the_turn_that_caused_it():
    """A dangling tool result is a protocol error, not just untidy history."""
    fake = _assistant_with_history()
    fake.rewind_to_user_text("second question")
    assert not any(m["role"] == "tool" for m in fake._history), (
        f"a tool result survived the rewind: {fake._history}")


def test_an_older_turn_wins_over_a_newer_one_of_the_same_text():
    """"yes" typed twice must rewind to the *second* one, not the first."""
    fake = _assistant_with_history()
    fake._history += [
        {"role": "user", "content": "yes"},
        {"role": "assistant", "content": "did the first"},
        {"role": "user", "content": "yes"},
        {"role": "assistant", "content": "did the second"},
    ]
    assert fake.rewind_to_user_text("yes") is True
    assert fake._history[-1]["content"] == "did the first"


def test_a_turn_with_attachments_is_matched_by_its_typed_text():
    """The transcript shows the words; the history has the files block after them.

    Matching on equality would miss every turn with an attachment on it, which is
    exactly the kind of thing that works in the test and fails on the day
    someone attaches a screenshot.
    """
    fake = _assistant_with_history()
    fake._history[3] = {"role": "user",
                        "content": "second question\n[attached: shot.png]"}
    assert fake.rewind_to_user_text("second question") is True


def test_a_rewind_that_cannot_be_done_changes_nothing():
    """After a compression the turn is gone; say so rather than cut wrongly."""
    fake = _assistant_with_history()
    before = [dict(m) for m in fake._history]
    assert fake.rewind_to_user_text("a question from three conversations ago") is False
    assert fake._history == before


def test_an_empty_request_is_refused_rather_than_matching_everything():
    """`""` would otherwise match any user message that starts with nothing."""
    fake = _assistant_with_history()
    before = [dict(m) for m in fake._history]
    assert fake.rewind_to_user_text("") is False
    assert fake.rewind_to_user_text("   ") is False
    assert fake._history == before

def test_one_row_per_turn_survives_a_rewind():
    """**The invariant two attempts both broke, and the first version of this
    test could not catch either.**

    `_turns` and `_rows` are counted separately, so a truncation can remove the
    wrong widgets while reporting exactly the right turns - and every test
    written against the turns alone stays green. Measured: an anchor taken as
    `get_first_child()` instead of `get_last_child()` leaves two turns sharing
    one row.

    **The first version of this test passed under that mutation because of its
    own loop.** It rewound at index 0, which empties the transcript, and then
    `if not visible_turns(): break` stopped it - so it performed exactly one
    cut, the one cut point where the mutation is invisible. A control that
    cannot fail is not a control.

    So: a fresh transcript per cut point, and the assertion is the *relation*
    between the two counts, since with no tool cards or notices a turn is a row.
    """
    for index in range(6):
        view = _transcript()
        assert len(_row_texts(view)) == 6, "the fixture did not build six rows"
        view.truncate_from_turn(index)
        rows, turns = len(_row_texts(view)), len(view.visible_turns())
        # Truncating at `index` keeps turns 0..index-1, so `index` of each.
        # Index 0 is included deliberately: that is the one cut point where the
        # anchor is None and the whole transcript goes, so it is the one place
        # the "everything is removed" path is actually exercised.
        assert rows == turns == index, (
            f"rewinding at {index} left {rows} rows for {turns} turns; "
            f"expected {index} of each")


# --------------------------------------------------------------------------
# the window half: it must not cut the screen the model did not agree to cut


# A D-Bus name segment may not begin with a digit, so the counter is spelled in
# letters - measured: `test.chronoa.rewind.1` fails
# `g_application_id_is_valid` outright.
_DIGITS = str.maketrans("0123456789", "abcdefghij")
_APP = None
_NAMES = (str(i).zfill(6).translate(_DIGITS) for i in itertools.count())


def _window():
    """A real window - a signal's return value is a property of the tree, not
    of the source.

    **No `app.register(None)`.** Registering exports the application on D-Bus,
    and the second window in a process fails with *"An object is already
    exported for the interface org.gtk.Application"* - measured, and it took
    three windows built in a loop to find. An unregistered application builds a
    window perfectly well; what needs registering is the application itself.
    """
    from gi.repository import Gtk

    from shani_chronoa.gui.window import ChronoaWindow

    # **One application for the whole file, not one per test.** Each
    # `Gtk.Application` created here is another claim on the process's identity,
    # and building several is how `test_transcript_scroll` started failing when
    # this file grew past three windows - it passed alone, passed in its own
    # file, and failed only alongside these. Measured by removing this file's
    # tests and watching the failure go away, which is the shape of evidence
    # that says "interference" rather than "a defect in transcript_scroll".
    global _APP
    if _APP is None:
        _APP = Gtk.Application(
            application_id="test.chronoa.rewind." + next(_NAMES))
    return ChronoaWindow(_APP)


def test_a_refused_rewind_leaves_the_transcript_alone():
    """**The safety property, and it is the one worth having.**

    The window asks the model to give up the turns and only cuts the screen when
    it says yes. A refused rewind - after a compression, say - must change
    nothing, or the user edits a conversation the model no longer has and the
    next answer is built on a disagreement neither side can see.
    """
    window = _window()
    window._transcript.add_user_turn("first question")
    window._transcript.add_assistant_turn("first answer")
    window._transcript.add_user_turn("second question")

    window.connect("rewind-history", lambda _w, _t: False)
    window._transcript.set_rewind_handler(window._on_rewind_requested)

    window._on_rewind_requested(2, "second question")

    assert window._transcript.visible_turns() == [
        ("user", "first question"), ("assistant", "first answer"),
        ("user", "second question")], "a refused rewind cut the transcript"
    assert window.get_input_text() == "", "a refused rewind seeded the composer"


def test_an_agreed_rewind_cuts_the_screen_and_offers_the_edit():
    window = _window()
    window._transcript.add_user_turn("first question")
    window._transcript.add_assistant_turn("first answer")
    window._transcript.add_user_turn("second question")
    window._transcript.add_assistant_turn("second answer")

    window.connect("rewind-history", lambda _w, _t: True)
    # **The seam says yes.** Rewinding now asks before discarding, and the real
    # dialogue is asynchronous - so without this these tests exercise the
    # *refused* branch and pass by asserting nothing happened. That is exactly
    # what happened when the confirmation was added: both went red, and the
    # reason was that they had never been asked anything.
    window._rewind_confirm = lambda preview, text, on_yes: (on_yes(), True)[1]
    window._transcript.set_rewind_handler(window._on_rewind_requested)

    window._on_rewind_requested(2, "second question")

    assert window._transcript.visible_turns() == [
        ("user", "first question"), ("assistant", "first answer")]
    # The text goes to the composer rather than being resubmitted: rerunning it
    # unchanged would replace the question with itself.
    assert window.get_input_text() == "second question"
    assert window.get_input_text() == "second question"


def test_the_history_is_asked_before_the_screen_is_cut():
    """Ordering, because only one order is safe.

    Cutting first and rewinding second would leave a window showing fewer turns
    than the model remembers if the rewind then refused - answers built on turns
    the user cannot see.
    """
    window = _window()
    window._transcript.add_user_turn("first question")
    window._transcript.add_assistant_turn("first answer")
    window._transcript.add_user_turn("second question")

    order = []
    window._rewind_confirm = lambda preview, text, on_yes: (on_yes(), True)[1]
    window.connect("rewind-history", lambda _w, _t: (order.append("history"), True)[1])
    window._transcript.set_rewind_handler(window._on_rewind_requested)
    window._on_rewind_requested(2, "second question")

    assert order == ["history"], "the model was never asked"
    assert window._transcript.visible_turns() == [
        ("user", "first question"), ("assistant", "first answer")]


# --------------------------------------------------------------------------
# the gesture, in the order the app actually uses


def test_a_user_turn_grows_a_gesture_when_the_handler_is_attached_first():
    """**The order the app uses, and the one every other test here got wrong.**

    `set_rewind_handler` only *enables* the gesture; the gesture is built in
    `_append_turn`, so a turn added *before* the handler is attached never gets
    one. Every test in this file did it that way - build the conversation, then
    attach - and so never constructed the gesture at all.

    Measured: handler-after gives **0** gestures attached, handler-before gives
    **1**. The production path is handler-first: `attach_regenerate()` runs when
    the window is shown, long before anyone types.

    That gap hid a live crash. The committed gesture called
    `Gtk.BUTTON_SECONDARY`, which is a **GTK 3** constant GTK 4 removed -
    `hasattr(Gtk, "BUTTON_SECONDARY")` is False - so every user turn in the
    shipped app raised `AttributeError` out of `add_user_turn`, and thirteen
    tests in this file were green throughout.

    It also asserts the *mechanism*, not just the count: a gesture with no
    button filter would be built by the same line and fail the same way.
    """
    import gi
    gi.require_version("Gtk", "4.0")
    from gi.repository import Gdk

    view = widgets_module.TranscriptView()
    view.set_rewind_handler(lambda index, text: None)
    view.add_user_turn("a question typed into a live app")

    gestures = []
    child = view._rows.get_first_child()
    while child is not None:
        gestures.extend(getattr(child, "_rewind_gestures", ()))
        child = child.get_next_sibling()

    assert len(gestures) == 1, (
        f"{len(gestures)} gestures on the first user turn; the app attaches the "
        f"handler before anyone types, so this is the path that runs")
    assert gestures[0].get_button() == Gdk.BUTTON_SECONDARY, (
        f"the gesture listens on button {gestures[0].get_button()}, not the "
        f"secondary one - a left-click popover would fight text selection")


def test_the_gesture_is_removed_when_the_handler_is_detached():
    """No assistant means no gesture; and a gesture left behind is a control
    whose handler is gone."""
    view = widgets_module.TranscriptView()
    view.set_rewind_handler(lambda index, text: None)
    view.add_user_turn("a question")
    view.set_rewind_handler(None)

    left = []
    child = view._rows.get_first_child()
    while child is not None:
        left.extend(getattr(child, "_rewind_gestures", ()))
        child = child.get_next_sibling()
    assert not left, f"{len(left)} gestures survived detaching the handler"


# --------------------------------------------------------------------------
# rewinding is destructive, so it says what it discards first


def _built_transcript():
    view = widgets_module.TranscriptView()
    view.add_user_turn("first question")
    view.add_assistant_turn("first answer")
    view.add_user_turn("second question")
    view.add_assistant_turn("second answer")
    return view


def _filled(window):
    """A window with four turns on it and a cleared transcript."""
    window._transcript.clear()
    window._transcript._turn_start.clear()
    window._transcript.add_user_turn("first question")
    window._transcript.add_assistant_turn("first answer")
    window._transcript.add_user_turn("second question")
    window._transcript.add_assistant_turn("second answer")
    return window


def test_the_preview_counts_what_a_rewind_would_discard(tmp_path):
    """**Turns are not the whole of what goes.**

    A tool call is a widget inside its own turn, and a made file is inside a
    tool card - so "4 turns" said over a rewind that also throws away a tool
    call and a file understates it, and understating a destructive action is
    the direction that makes it feel cheaper than it is.
    """
    import gi
    gi.require_version("Gtk", "4.0")
    from gi.repository import GdkPixbuf

    view = _built_transcript()
    made = tmp_path / "cat.png"
    buf = GdkPixbuf.Pixbuf.new(GdkPixbuf.Colorspace.RGB, False, 8, 32, 32)
    buf.fill(0x2266CCFF)
    assert buf.savev(str(made), "png", [], []) is True
    view.add_tool_call("generate_image", {}, "Saved it.", True, (made,))
    view.add_user_turn("third question")
    view.add_assistant_turn("third answer")

    preview = view.rewind_preview(1)
    assert preview["ok"]
    # Six turns in total, rewinding from index 1 discards the five after it.
    # (The first version asserted 6 - the count *including* the turn kept.)
    assert preview["turns"] == 5, preview
    assert preview["tool_calls"] == 1, preview
    assert preview["artifacts"] == 1, preview
    assert "tool call" in preview["sentence"], preview["sentence"]
    assert "file" in preview["sentence"], preview["sentence"]


def test_the_preview_of_a_turn_that_is_not_there_is_not_zero():
    """A caller must not be able to ask about nothing and be told "nothing"."""
    view = _built_transcript()
    for bad in (-1, 99, "two", None):
        assert view.rewind_preview(bad)["ok"] is False


def test_one_file_declared_twice_is_counted_once(tmp_path):
    """Double-counting makes a rewind look *more* destructive than it is, which
    teaches people to confirm without reading."""
    import gi
    gi.require_version("Gtk", "4.0")
    from gi.repository import GdkPixbuf

    view = _built_transcript()
    made = tmp_path / "cat.png"
    buf = GdkPixbuf.Pixbuf.new(GdkPixbuf.Colorspace.RGB, False, 8, 32, 32)
    buf.fill(0x2266CCFF)
    buf.savev(str(made), "png", [], [])
    view.add_tool_call("a", {}, "", True, (made,))
    view.add_tool_call("b", {}, "", True, (made,))

    assert view.rewind_preview(0)["artifacts"] == 1


def test_a_declined_rewind_removes_nothing():
    """**The property the whole confirmation exists for.**

    The window cuts only what the model has already agreed to give up, and only
    after a yes. A "no" must leave the conversation exactly as it was - and the
    history must not have been rewound either, or the two are out of step in
    the direction this codebase keeps paying for.
    """
    window = _filled(_window())
    asked = []
    window._rewind_confirm = lambda preview, text, on_yes: (
        asked.append(preview["sentence"]) or False)
    rewinds = []
    window.connect("rewind-history", lambda _w, text: rewinds.append(text) or True)

    window._on_rewind_requested(2, "second question")

    assert asked, "nothing was asked, so nothing was decided"
    assert rewinds == [], "the history was rewound after a declined rewind"
    assert len(window._transcript.visible_turns()) == 4, (
        f"a declined rewind changed the transcript: "
        f"{window._transcript.visible_turns()}")
    assert window.get_input_text() == "", (
        "a declined rewind put the question back in the composer")


def test_an_accepted_rewind_cuts_the_screen_and_the_history():
    window = _filled(_window())
    window._rewind_confirm = lambda preview, text, on_yes: (on_yes(), True)[1]
    rewinds = []
    window.connect("rewind-history", lambda _w, text: rewinds.append(text) or True)

    window._on_rewind_requested(2, "second question")

    assert rewinds == ["second question"], "the history was not rewound"
    assert window._transcript.visible_turns() == [
        ("user", "first question"), ("assistant", "first answer")]
    assert window.get_input_text() == "second question"


def test_a_dialog_that_cannot_ask_refuses():
    """**"Could not ask" must never read as "yes".**

    A guard that treats an unavailable dialogue as consent is the same defect as
    the one this file keeps fixing, in the direction that discards work.
    """
    window = _filled(_window())

    def _broken(preview, text, on_yes):
        raise RuntimeError("no dialog available")

    window._rewind_confirm = _broken
    rewinds = []
    window.connect("rewind-history", lambda _w, text: rewinds.append(text) or True)
    try:
        window._on_rewind_requested(2, "second question")
    except RuntimeError:
        pass      # a seam that raises is the caller's problem, not consent
    assert rewinds == [], "a dialogue that failed still rewound the history"
    assert len(window._transcript.visible_turns()) == 4


def test_the_dialog_acts_on_a_yes_and_only_on_a_yes():
    """**The load-bearing claim, and it was untested for a while.**

    The answer handling lived in a closure inside `_ask_rewind_dialog`; changing
    the comparison made the rewind impossible in the shipped app and every test
    stayed green, because the only test on it checked the *signature* of the
    method. Checking that the action is handed over is not the same claim as
    checking that it is ever called.
    """
    import inspect

    signature = inspect.signature(window_module.ChronoaWindow._ask_rewind_dialog)
    assert list(signature.parameters) == ["self", "preview", "text", "on_yes"], (
        f"the dialog must be handed the action: {list(signature.parameters)}")

    ran = []
    window = _window()
    window_module.ChronoaWindow._rewind_responded(window, "rewind",
                                                  lambda: ran.append("yes"))
    assert ran == ["yes"], f"a Rewind answer did not rewind: {ran}"

    for cancel in ("cancel", "", "close"):
        ran.clear()
        window_module.ChronoaWindow._rewind_responded(window, cancel,
                                                      lambda: ran.append("yes"))
        assert not ran, f"{cancel!r} rewound anyway: {ran}"
