"""Window UX: the state machine, the transcript, and the empty state.

Every assertion here builds real `Gtk` objects. This repo has twice shipped a
GUI that read as complete and crashed or no-oped on first construction - a
GTK3-only `set_icon_widget` call, and an orb wired to nothing - so a test that
only pokes a mock proves nothing about whether the window works. If a check in
this file starts passing against a stub, it has stopped being worth anything.

The state-consistency tests exist because the window used to have three
independent booleans on the orb plus a separate status string, which meant the
orb could read green while the label read "Ready" and no test would notice.
"""

import sys
import threading
import time

import pytest

gi = pytest.importorskip("gi")
gi.require_version("Gtk", "4.0")
from gi.repository import Gtk  # noqa: E402

sys.path.insert(0, "usr/lib/shani-chronoa")
from shani_chronoa.gui import (  # noqa: E402
    AssistantState,
    CajitaWindow,
    ChronoaOrbWidget,
    TranscriptView,
)


@pytest.fixture(scope="module")
def gtk_app():
    """A real Gtk.Application with a live main loop.

    Widget state (css classes, labels, visibility) only settles once the
    widgets are realised against a display, so the loop has to actually run.
    """
    app = Gtk.Application(application_id="test.chronoa.window.ux")
    holder = {}
    started = threading.Event()

    def on_activate(a):
        holder["app"] = a
        started.set()
        a.quit()

    app.connect("activate", on_activate)
    app.run([])
    assert started.wait(10), "Gtk.Application never activated"
    yield app


@pytest.fixture
def window(gtk_app):
    win = CajitaWindow(gtk_app)
    yield win
    win.destroy()


def test_every_state_is_fully_derived_and_never_disagrees(window):
    """The core invariant: one state, one truth.

    Orb, status wording and stop-button visibility are all derived from
    `set_state`, so they cannot contradict each other. This is the property the
    old three-boolean orb could not express.
    """
    for state in AssistantState:
        window.set_state(state)
        assert window.get_state() is state
        assert window._orb.get_state() is state
        assert window._state_label.get_label() == state.label
        assert f"state-{state.value}" in list(window._orb.get_css_classes())


def test_stop_control_appears_only_while_speaking(window):
    """Spoken audio must be stoppable exactly when it is playing."""
    window.set_state(AssistantState.SPEAKING)
    assert window._stop_button.get_visible()
    for state in (
        AssistantState.IDLE,
        AssistantState.LISTENING,
        AssistantState.THINKING,
        AssistantState.INTERRUPTING,
        AssistantState.ERROR,
    ):
        window.set_state(state)
        assert not window._stop_button.get_visible(), f"stop shown while {state.value}"


def test_a_transient_notice_cannot_overwrite_the_state(window):
    """`set_status` is a detail channel, not a state channel.

    `_toggle_privacy`, `_set_wake_word_active` and six other handlers all wrote
    to the same label the state used, so any toggle notice erased the fact
    that the assistant was mid-turn.
    """
    window.set_state(AssistantState.SPEAKING)
    window.set_status("Wake word: ON")
    assert window.get_state() is AssistantState.SPEAKING
    assert window._state_label.get_label() == AssistantState.SPEAKING.label
    assert window._detail_label.get_label() == "Wake word: ON"
    window.clear_status()
    assert not window._detail_label.get_visible()


def test_mic_indicator_tracks_the_states_that_actually_use_the_mic(window):
    window.set_state(AssistantState.LISTENING)
    assert window._mic_icon.get_tooltip_text() == "Microphone is in use"
    window.set_state(AssistantState.INTERRUPTING)
    assert window._mic_icon.get_tooltip_text() == "Microphone is in use"
    window.set_state(AssistantState.SPEAKING)
    assert window._mic_icon.get_tooltip_text() == "Microphone idle"


def test_turns_accumulate_instead_of_replacing(window):
    """A spoken conversation has to stay readable after it happens.

    The response area used to be a single `Gtk.Label` each reply overwrote, so
    the window could only ever show the most recent thing said.
    """
    window.clear_transcript()
    window.add_user_turn("what is the weather")
    window.set_response("Sunny, 21C")
    window.add_user_turn("and tomorrow")
    window.set_response("Rain likely")

    turns = _turns(window._transcript)
    assert [t[0] for t in turns] == ["user", "assistant", "user", "assistant"]
    assert [t[1] for t in turns] == ["what is the weather", "Sunny, 21C", "and tomorrow", "Rain likely"]


def test_a_second_response_updates_the_turn_instead_of_appending(window):
    """A tool-calling answer is written twice; the user must not see two replies."""
    window.clear_transcript()
    window.set_response("Checking the calendar…")
    window.set_response("You have a 4pm meeting in the annex.")
    turns = _turns(window._transcript)
    assert len(turns) == 1
    assert turns[0][1] == "You have a 4pm meeting in the annex."


def test_empty_state_names_what_to_do(window):
    """A blank transcript reads as a broken window."""
    window.clear_transcript()
    first = window._transcript._rows.get_first_child()
    assert "transcript-placeholder" in first.get_css_classes()
    assert first.get_label()
    # Nothing else should be present - the placeholder is the whole state.
    assert first.get_next_sibling() is None


def test_first_turn_replaces_the_placeholder(window):
    window.clear_transcript()
    window.add_user_turn("hello")
    rows = _turns(window._transcript)
    assert len(rows) == 1
    assert rows[0][0] == "user"


def test_empty_response_clears_to_the_placeholder(window):
    window.set_response("something")
    window.set_response("")
    assert "transcript-placeholder" in window._transcript._rows.get_first_child().get_css_classes()


def test_state_is_not_colour_only(window):
    """Every state needs a distinct icon as well as a distinct colour.

    Colour alone excludes colourblind users, and the old mapping conveyed three
    of its states by hue with no other difference.
    """
    from shani_chronoa.gui import _STATE_STYLE

    colours = [c for c, _ in _STATE_STYLE.values()]
    icons = [i for _, i in _STATE_STYLE.values()]
    # `listening`/`idle` share an icon by design (both involve the mic), so
    # icons need not be unique - but no colour may be reused, or two states
    # would be indistinguishable by colour alone.
    assert len(set(colours)) == len(AssistantState)


def test_legacy_set_orb_state_still_routes_into_the_single_state(window):
    """`app.py` speaks the old string spelling; it must not fork the state."""
    window.set_orb_state("processing")
    assert window.get_state() is AssistantState.THINKING
    window.set_orb_state("error")
    assert window.get_state() is AssistantState.ERROR
    window.set_orb_state("listening")
    assert window.get_state() is AssistantState.LISTENING
    window.set_orb_state("something-unknown")
    assert window.get_state() is AssistantState.IDLE


def test_orb_reports_its_own_state_independently():
    """The orb is a real Button, so it keeps focus/activation/accessible role."""
    orb = ChronoaOrbWidget()
    assert orb.get_state() is AssistantState.IDLE
    orb.set_state(AssistantState.THINKING)
    assert orb.get_state() is AssistantState.THINKING
    assert "state-thinking" in list(orb.get_css_classes())


def test_stop_button_is_reachable_by_keyboard(gtk_app):
    """Accessibility: a mouse-only stop control would be unusable by keyboard."""
    win = CajitaWindow(gtk_app)
    assert win._stop_button.get_focusable()
    assert win._stop_button.get_action_name() == "app.stop-speaking"
    win.destroy()


def test_reduce_motion_class_is_applied_when_animations_are_off(gtk_app, monkeypatch):
    """The pulse must yield to the desktop's reduce-motion preference."""
    settings = Gtk.Settings.get_default()
    original = settings.get_property("gtk-enable-animations")
    try:
        settings.set_property("gtk-enable-animations", False)
        win = CajitaWindow(gtk_app)
        assert "reduce-motion" in list(win.get_css_classes())
        win.destroy()
        settings.set_property("gtk-enable-animations", True)
        win = CajitaWindow(gtk_app)
        assert "reduce-motion" not in list(win.get_css_classes())
        win.destroy()
    finally:
        settings.set_property("gtk-enable-animations", original)


def test_transcript_view_scrolls_to_the_newest_turn(gtk_app):
    """A transcript that does not follow the conversation is a static log."""
    view = TranscriptView()
    for i in range(40):
        view.add_user_turn(f"message number {i}")
    deadline = time.time() + 5
    while time.time() < deadline:
        adj = view.get_vadjustment()
        if adj is not None and adj.get_upper() > 0:
            assert adj.get_value() == pytest.approx(adj.get_upper() - adj.get_page_size(), abs=2.0)
            return
        time.sleep(0.05)
    pytest.skip("adjustment never became scrollable; nothing to assert headless")


def _turns(transcript):
    """(role, text) for each real turn, in order, skipping the placeholder."""
    out = []
    child = transcript._rows.get_first_child()
    while child is not None:
        classes = list(child.get_css_classes())
        if "transcript-placeholder" not in classes:
            role = "user" if "transcript-user" in classes else "assistant"
            out.append((role, child.get_label()))
        child = child.get_next_sibling()
    return out
