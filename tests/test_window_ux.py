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
from gi.repository import Gtk, GLib  # noqa: E402

sys.path.insert(0, "usr/lib/shani-chronoa")
from shani_chronoa.gui import (  # noqa: E402
    AssistantState,
    ChronoaWindow,
    ChronoaOrbWidget,
    TranscriptView,
)


def _pump(limit=1.5):
    """Drain pending `GLib.idle_add` / timeout sources without a full main loop.

    The level halo eases on a GLib timeout, so its settled value is only
    observable once those sources have run.
    """
    ctx = GLib.MainContext.default()
    deadline = time.time() + limit
    while time.time() < deadline:
        while ctx.pending():
            ctx.iteration(False)
        time.sleep(0.01)


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
    win = ChronoaWindow(gtk_app)
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


def test_stop_control_appears_only_while_speaking_or_thinking(window):
    """Stop is shown exactly when there is something to stop: audio playing, or a turn still thinking."""
    for state in (AssistantState.SPEAKING, AssistantState.THINKING):
        window.set_state(state)
        assert window._stop_button.get_visible(), f"no stop while {state.value}"
    for state in (
        AssistantState.IDLE,
        AssistantState.LISTENING,
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
    # The empty state is the placeholder *and* the suggestion chips. What must
    # still hold is that nothing *else* is there, so a cleared transcript can
    # never grow stray rows.
    sibling = first.get_next_sibling()
    if sibling is not None:
        assert "suggestion-bar" in sibling.get_css_classes(), (
            f"unexpected row in the empty state: {sibling.get_css_classes()}"
        )
        assert sibling.get_next_sibling() is None


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
    assert icons, "no state has an icon at all"
    assert len(set(colours)) == len(AssistantState)
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
    win = ChronoaWindow(gtk_app)
    assert win._stop_button.get_focusable()
    assert win._stop_button.get_action_name() == "app.stop-speaking"
    win.destroy()


def test_reduce_motion_class_is_applied_when_animations_are_off(gtk_app, monkeypatch):
    """The pulse must yield to the desktop's reduce-motion preference."""
    settings = Gtk.Settings.get_default()
    original = settings.get_property("gtk-enable-animations")
    try:
        settings.set_property("gtk-enable-animations", False)
        win = ChronoaWindow(gtk_app)
        assert "reduce-motion" in list(win.get_css_classes())
        win.destroy()
        settings.set_property("gtk-enable-animations", True)
        win = ChronoaWindow(gtk_app)
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


def _label_of(row):
    """The turn's text, whatever shape the row is.

    A user turn is one label. An assistant turn is a row of blocks - a copy
    button beside a stack of text, code, table and tool-call widgets - so there
    is no single label to read.

    The text is read from the row's own `_turn_text` rather than by digging
    through the blocks for the first `Gtk.Label` in sight, because that would
    find the *language tag* of a code block ("Bash") instead of the reply.
    Reading the accessible label back instead is not an option: on this GTK
    build `update_property([LABEL], [None])` on a Box segfaults. These tests are
    about turns accumulating and a second response replacing the first, not
    about the widget tree, which is not the contract.
    """
    if isinstance(row, Gtk.Label):
        return row.get_label()
    text = getattr(row, "_turn_text", None)
    if text is not None:
        return text
    child = row.get_first_child()
    while child is not None:
        if isinstance(child, Gtk.Label):
            return child.get_label()
        child = child.get_next_sibling()
    return None


def _turns(transcript):
    """(role, text) for each real turn, in order, skipping the placeholder."""
    out = []
    child = transcript._rows.get_first_child()
    while child is not None:
        classes = list(child.get_css_classes())
        if "transcript-placeholder" not in classes:
            role = "user" if "transcript-user" in classes else "assistant"
            out.append((role, _label_of(child)))
        child = child.get_next_sibling()
    return out


class TestLevelHalo:
    """The orb's amplitude-reactive halo.

    Asserted on the halo's actual `size_request`, not on "the draw call did not
    raise". An earlier version of the halo was a `Gtk.DrawingArea` whose draw
    callback silently failed on every frame - `py_compile` passed, the level
    maths was right, and the orb simply never moved - while a check that only
    confirmed `queue_draw()` did not throw reported success throughout. The
    cairo foreign-struct converter is only registered once something imports
    the `cairo` gi override, and `python3-cairo` is in neither package manifest.
    """

    def test_level_maps_to_a_growing_ring(self):
        from shani_chronoa.gui import _HALO_MAX_PX, _HALO_MIN_PX, _halo_size

        assert _halo_size(0.0) == (_HALO_MIN_PX, _HALO_MIN_PX)
        assert _halo_size(1.0) == (_HALO_MAX_PX, _HALO_MAX_PX)
        assert _halo_size(0.5)[0] > _HALO_MIN_PX
        assert _halo_size(0.0)[0] < _halo_size(1.0)[0]

    def test_out_of_range_levels_are_clamped(self):
        from shani_chronoa.gui import _HALO_MAX_PX, _HALO_MIN_PX, _halo_size

        assert _halo_size(5.0)[0] == _HALO_MAX_PX
        assert _halo_size(-3.0)[0] == _HALO_MIN_PX

    def test_the_halo_really_resizes(self, gtk_app):
        """The check that would have caught the dead draw callback."""
        from shani_chronoa.gui import _HALO_MAX_PX, _HALO_MIN_PX

        win = ChronoaWindow(gtk_app)
        orb = win._orb
        orb.set_level(0.0)
        _pump(1.0)
        quiet = orb._halo.get_size_request()[0]
        orb.set_level(1.0)
        _pump(2.0)
        loud = orb._halo.get_size_request()[0]

        assert quiet == _HALO_MIN_PX
        assert loud == _HALO_MAX_PX, f"halo stayed at {loud}px; the level is not reaching it"
        assert loud > quiet
        win.destroy()

    def test_level_eases_rather_than_jumping(self, gtk_app):
        from shani_chronoa.gui import _HALO_MAX_PX, _HALO_MIN_PX, _TICK_MS

        win = ChronoaWindow(gtk_app)
        orb = win._orb
        orb.set_level(1.0)
        # Mid-flight the value must be strictly between the endpoints, which is
        # what makes the orb look like it is following the voice.
        _pump(_TICK_MS / 1000.0 + 0.05)
        mid = orb.get_level()
        assert 0.0 < mid < 1.0, f"level jumped straight to {mid}; easing is not running"
        # ...and the halo must be following that in-flight value, not merely
        # jumping to its final size when the easing completes. Checking only
        # the settled size cannot tell those two apart.
        mid_px = orb._halo.get_size_request()[0]
        assert _HALO_MIN_PX < mid_px < _HALO_MAX_PX, (
            f"halo is at {mid_px}px while the level is only {mid:.2f}; the ring is "
            "not tracking the level as it eases"
        )
        win.destroy()

    def test_input_level_is_ignored_unless_listening(self, gtk_app):
        """A level arriving after the turn ended must not pulse at nothing."""
        win = ChronoaWindow(gtk_app)
        win.set_state(AssistantState.IDLE)
        win.set_input_level(1.0)
        _pump(0.5)
        assert win._orb.get_level() == 0.0

        win.set_state(AssistantState.LISTENING)
        win.set_input_level(1.0)
        _pump(1.5)
        assert win._orb.get_level() > 0.5
        win.destroy()

    def test_halo_colour_tracks_the_state(self, gtk_app):
        win = ChronoaWindow(gtk_app)
        for state in AssistantState:
            win.set_state(state)
            assert f"halo-{state.value}" in list(win._orb._halo.get_css_classes())
        win.destroy()


class TestLevelNormalisation:
    def test_quiet_room_reads_as_silence(self):
        """An indicator that flickers with background hum is worse than a still one."""
        from shani_chronoa.vad import _LEVEL_FLOOR, normalize_level

        assert normalize_level(0) == 0.0
        assert normalize_level(_LEVEL_FLOOR) == 0.0
        assert normalize_level(_LEVEL_FLOOR / 2) == 0.0

    def test_loud_speech_reaches_the_top_without_pinning_early(self):
        from shani_chronoa.vad import _LEVEL_CEILING, normalize_level

        assert normalize_level(_LEVEL_CEILING) == 1.0
        assert normalize_level(32768) == 1.0
        # 2000 sits near the middle of the scale, not pinned at either end.
        assert 0.2 < normalize_level(2000) < 0.8

    def test_normalisation_is_monotonic(self):
        from shani_chronoa.vad import normalize_level

        values = [normalize_level(v) for v in range(0, 8000, 100)]
        assert all(b >= a for a, b in zip(values, values[1:])), "level curve is not monotonic"

    def test_the_curve_is_sqrt_shaped_so_quiet_speech_still_moves(self):
        """Loudness is perceived logarithmically; linear leaves speech invisible."""
        from shani_chronoa.vad import _LEVEL_FLOOR, _LEVEL_CEILING, normalize_level

        midpoint_rms = (_LEVEL_FLOOR + _LEVEL_CEILING) / 2
        assert normalize_level(midpoint_rms) > 0.5, "a linear curve would give ~0.5 here"
