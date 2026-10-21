"""One line saying what changed while you were not looking.

The rail has seven sections and all of them report *current* state. None of them
says what happened during the hour the window was closed, which is the moment
they exist for: someone comes back to a machine that did things unattended and
has no way to find out what.

**Counted as a delta, because nothing here is timestamped.** The sentence
therefore must not imply otherwise, and the tests below check what it says as
well as what it counts.

**The silences matter as much as the sentence.** A catch-up line on every focus,
on the first focus, or when nothing happened, turns a useful notice into a
permanent tax on attention - and a feature that says "0 files and 0 rules fired"
makes the reader check whether they are looking at the right window.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "usr/lib/shani-chronoa"))

import gi  # noqa: E402

gi.require_version("Gtk", "4.0")
from gi.repository import Gtk  # noqa: E402

_APP = None


def _window():
    """One application for the whole file.

    Each `Gtk.Application` is another claim on the process's identity, and
    building several has already produced a cross-file failure in this
    repository - a test that passed alone and failed only when run alongside
    another. The window is still per-test; the application is not.
    """
    global _APP
    from shani_chronoa.gui.window import ChronoaWindow

    if _APP is None:
        _APP = Gtk.Application(application_id="test.chronoa.awayline")
    return ChronoaWindow(_APP)


class _Events:
    """Stands in for the ambient scheduler: a list that can grow between reads."""

    def __init__(self, results=None):
        self._results = list(results or [])

    def event_results(self):
        return list(self._results)

    def fire(self, count=1):
        self._results.extend(range(count))


def _notices(window):
    from collections import deque

    found, queue = [], deque([window._transcript])
    while queue:
        node = queue.popleft()
        if isinstance(node, Gtk.Label) and node.get_label().startswith("While you were"):
            found.append(node.get_label())
        if hasattr(node, "get_first_child"):
            child = node.get_first_child()
            while child is not None:
                queue.append(child)
                child = child.get_next_sibling()
    return found


def _lookup(window):
    """Whatever the transcript currently shows as an away notice."""
    return _notices(window)


def test_the_first_look_says_nothing():
    """There is no "since" to report before there has been a first look."""
    window = _window()
    window._transcript.clear()
    window._away_snapshot = None
    window._artifacts = []
    window.set_event_source(_Events(["a rule fired"]))

    window._note_what_happened_while_away()
    assert _lookup(window) == [], (
        f"the first focus announced something: {_lookup(window)}")


def test_a_file_made_while_away_is_announced():
    window = _window()
    window._transcript.clear()
    window._artifacts = []
    window.set_event_source(_Events())
    window._note_what_happened_while_away()      # establishes the baseline

    window._artifacts = ["one.png", "two.png"]
    window._away_snapshot = (window._away_snapshot[0], window._away_snapshot[1] - 3600)
    window._note_what_happened_while_away()

    said = _lookup(window)
    assert said, "two files made while away were not announced"
    assert "2 files" in said[0], said[0]
    assert "1h away" in said[0], said[0]


def test_a_rule_firing_while_away_is_announced():
    window = _window()
    window._transcript.clear()
    window._artifacts = []
    events = _Events()
    window.set_event_source(events)
    window._note_what_happened_while_away()

    events.fire(3)
    window._away_snapshot = (window._away_snapshot[0], window._away_snapshot[1] - 7200)
    window._note_what_happened_while_away()

    said = _lookup(window)
    assert said and "3 rules fired" in said[0], said
    assert "2h away" in said[0], said[0]


def test_both_kinds_are_reported_together():
    window = _window()
    window._transcript.clear()
    window._artifacts = []
    events = _Events()
    window.set_event_source(events)
    window._note_what_happened_while_away()

    window._artifacts = ["one.png"]
    events.fire(1)
    window._away_snapshot = (window._away_snapshot[0], window._away_snapshot[1] - 300)
    window._note_what_happened_while_away()

    said = _lookup(window)
    assert said, "nothing said about either change"
    assert "1 file made" in said[0] and "1 rule fired" in said[0], said[0]


def test_nothing_happened_means_nothing_is_said():
    """"0 files and 0 rules fired" is worse than silence - it makes the reader
    check whether they are looking at the right window."""
    window = _window()
    window._transcript.clear()
    window._artifacts = []
    window.set_event_source(_Events())
    window._note_what_happened_while_away()
    window._away_snapshot = (window._away_snapshot[0], window._away_snapshot[1] - 3600)

    window._note_what_happened_while_away()
    assert _lookup(window) == [], (
        f"an empty catch-up line was shown: {_lookup(window)}")


def test_a_quick_trip_away_is_not_a_catch_up():
    """Alt-tabbing to a terminal for ten seconds is not "coming back"."""
    window = _window()
    window._transcript.clear()
    window._artifacts = []
    window.set_event_source(_Events())
    window._note_what_happened_while_away()

    window._artifacts = ["made-while-you-were-here.png"]
    window._note_what_happened_while_away()

    assert _lookup(window) == [], (
        f"a ten-second absence produced a notice: {_lookup(window)}")


def test_the_count_is_a_delta_not_a_total():
    """Ten files made since you left is not "you have ten files"."""
    window = _window()
    window._transcript.clear()
    window._artifacts = [f"old-{i}.png" for i in range(7)]
    window.set_event_source(_Events())
    window._note_what_happened_while_away()

    window._artifacts.append("new.png")
    window._away_snapshot = (window._away_snapshot[0], window._away_snapshot[1] - 3600)
    window._note_what_happened_while_away()

    said = _lookup(window)
    assert said and "1 file made" in said[0], (
        f"reported the total rather than the change: {said}")


def test_a_scheduler_that_cannot_be_read_does_not_break_the_focus():
    """The rail already swallows this; the window must not be the one that
    raises on focus."""
    window = _window()
    window._transcript.clear()
    window._artifacts = []

    class _Broken:
        def event_results(self):
            raise RuntimeError("the scheduler is gone")

    window.set_event_source(_Broken())
    window._note_what_happened_while_away()      # baseline
    window._away_snapshot = (window._away_snapshot[0], window._away_snapshot[1] - 3600)
    window._note_what_happened_while_away()
    assert _lookup(window) == [], "a broken source produced a notice"


def test_the_away_notice_gets_its_own_icon():
    """A third kind of housekeeping note gets a third icon, not a chain of
    `if kind ==` in every caller - and the icon has to exist."""
    import pathlib
    from shani_chronoa.gui import widgets as W

    adwaita = pathlib.Path("/usr/share/icons/Adwaita")
    for name in ("preferences-system-time-symbolic",
                 "network-transmit-receive-symbolic",
                 "format-justify-fill-symbolic"):
        assert any(adwaita.rglob(f"{name}.svg")) or any(adwaita.rglob(f"{name}.png")), (
            f"{name} does not ship in Adwaita, so the notice would render as a "
            f"blank square")

    view = W.TranscriptView()
    view.add_user_turn("a question")
    view.add_assistant_turn("an answer")
    before = view.visible_turns()
    view.add_notice_row("While you were 1h away: 1 file(s) made.", kind="away")
    assert view.visible_turns() == before, (
        "an away notice became a turn, so it would be searched and answered as "
        f"though the assistant had said it: {view.visible_turns()}")

    # **On an empty transcript a notice does create a turn**, because it has to
    # live inside one - `add_notice_row` anchors to the current assistant turn and
    # makes one if there is none. That is existing behaviour and the first version
    # of this test asserted the opposite, failing on a transcript that had
    # nothing to anchor to.
    empty = W.TranscriptView()
    empty.add_notice_row("While you were 1m away: 1 rule fired.", kind="away")
    assert empty.visible_turns() == [("assistant", "")], empty.visible_turns()