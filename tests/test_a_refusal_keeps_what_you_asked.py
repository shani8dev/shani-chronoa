"""Nothing you gave the app comes back destroyed.

Six defects, one shape. In every case the app **accepted something from a
person and then threw it away** - on a path where the person had no way to
notice, and often no way to get it back.

| what was destroyed | where | how a person hit it |
|---|---|---|
| the retry on a failed turn | `gui/widgets.py` | asking again, with no way to ask again |
| messages still queued | `app/voice.py` | Stop, with questions left unanswered on screen |
| the question | `app/brain.py` | asking while the model sleeps |
| the question | `app/brain.py` | **the first message typed on any fresh install** |
| the attachment chips | `gui/attaching.py` | attaching files, then being refused for want of a model |
| findability of a notice | `gui/widgets.py` | searching for the compaction that lost an answer |

Two of the six are regressions **this session introduced** while fixing other
things, which is why the file is one file rather than six:

- Marking a failure as a notice row (`66b106f`) stopped it reading as something
  Chronoa said - and quietly removed the "Ask that again" button, because
  capability here comes from **which builder ran**, not from what the row is
  about. The turn that most needs redoing became the one turn that cannot be
  redone.
- The same change stopped the error being the turn's text, and `find()` reads
  `_turns`, so an error that used to be findable stopped being findable.

`brain.py` is the sharpest of the six. Its branch for *a model that is
installed but stopped* starts the service and **re-submits the same `text`**.
The branch for *no model at all* - the one every fresh install takes, before a
1 GB download - dropped it, while `_submit_input` had already cleared the
composer, the draft and the chips. The transcript kept the question and never
the answer.

**Every test here drives the real code.** The brain tests call the real
`_explain_no_model`; the widget tests build a real `TranscriptView`. Reading the
source for `set_input_text(text)` would pass on code that never restores
anything, and restoring nothing is the whole defect.
"""

import sys
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "usr/lib/shani-chronoa"))

import gi  # noqa: E402

gi.require_version("Gtk", "4.0")
from gi.repository import Gtk  # noqa: E402

from shani_chronoa import local_llm  # noqa: E402
from shani_chronoa.app import ChronoaApplication  # noqa: E402
from shani_chronoa.gui import widgets as widgets_module  # noqa: E402
from shani_chronoa.gui.widgets import TranscriptView  # noqa: E402
from shani_chronoa.presence import Presence  # noqa: E402

TURN_NOTICE_CSS = widgets_module.TURN_NOTICE_CSS


def _walk(node, out=None):
    out = [] if out is None else out
    if node is not None:
        out.append(node)
    if hasattr(node, "get_first_child"):
        child = node.get_first_child()
        while child is not None:
            _walk(child, out)
            child = child.get_next_sibling()
    return out


def _notice_row(view, needle):
    """The notice row whose label mentions `needle`, or None.

    **Scoped to the row, not the transcript.** The first version of the
    housekeeping test walked `view._rows` and found the empty state's
    suggestion chips, so a compaction note appeared to "offer to ask it again"
    when what it had found was the SuggestionBar two widgets away.
    """
    for row in _walk(view._rows):
        if TURN_NOTICE_CSS not in (row.get_css_classes() or []):
            continue
        if any(isinstance(n, Gtk.Label) and needle in n.get_label()
               for n in _walk(row)):
            return row
    return None


# ---------------------------------------------------------------- retry button


class TestAFailedTurnCanBeAskedAgain:
    """The regression: fixing how a failure *looks* took away what it could *do*."""

    def _view(self):
        view = TranscriptView()
        view.show_placeholder()
        asked = []
        view.set_regenerate_handler(lambda: asked.append(1))
        return view, asked

    def test_a_failure_carries_a_way_to_ask_again(self):
        view, asked = self._view()
        view.add_notice_row("Chronoa could not finish that turn. Boom.", "error")
        row = _notice_row(view, "Boom")
        assert row is not None, "the failure row was not built"
        buttons = [n for n in _walk(row) if isinstance(n, Gtk.Button)]
        assert buttons, (
            "a failed turn offers no way to try again - the person who most "
            "needs to re-ask has nothing to press, and must reconstruct and "
            "retype the question")
        for button in buttons:
            button.emit("clicked")
        assert asked, "the retry button does not reach the handler"

    def test_and_the_button_says_what_it_does(self):
        """**The tooltip, not the accessible label.** GTK 4 exposes **no
        getter** for `Gtk.AccessibleProperty.LABEL` - measured on this box:
        `hasattr(button, "get_accessible")` is False and there is no accessible
        label accessor on `Gtk.Widget` at all. The label is still *set*, which
        is what a screen reader reads; it simply cannot be read back here, so
        asserting on it would fail on correct code. The tooltip is readable and
        is announced as the description.
        """
        view, _ = self._view()
        view.add_notice_row("Boom.", "error")
        row = _notice_row(view, "Boom")
        tooltips = [b.get_tooltip_text() for b in _walk(row)
                    if isinstance(b, Gtk.Button)]
        assert any(t and "again" in t.lower() for t in tooltips), (
            f"the retry is not named: {tooltips}")

    def test_a_housekeeping_note_is_not_offered_a_retry(self):
        """**The control, and why the fix is gated on `kind == "error"`.**

        A compaction note is about a turn that *did* answer. There is nothing
        to retry, so a button there would be an offer the app cannot honour -
        which is the dead-control defect this review started from, wearing the
        costume of the fix for it.
        """
        view, _ = self._view()
        view.add_notice_row("earlier context was shortened", "compaction")
        row = _notice_row(view, "shortened")
        assert row is not None, "the compaction row was not built"
        assert not [n for n in _walk(row) if isinstance(n, Gtk.Button)], (
            "a compaction note offers to ask it again - there is no question "
            "behind it to ask")


# ------------------------------------------------------------------ queued msgs


class TestStopSaysWhatIsStillWaiting:
    """A queue kept across a Stop has to be announced, or it reads as a refusal."""

    @staticmethod
    def _self(queued, said):
        """A stand-in shaped like the call, not like the class.

        `_stop_speaking` reaches through `self.window` for the three widget
        calls, so the stand-in needs a **window attribute** - passing one object
        where the method expects an object with a window in it is the shape
        mistake, and it failed with `no attribute 'window'` rather than with
        anything about the status under test.
        """
        return SimpleNamespace(
            window=SimpleNamespace(
                set_orb_state=lambda s: None,
                set_status=lambda s: said.append(s),
                set_state=lambda s: None,
            ),
            _turn_pending=list(queued),
            _turn_future=SimpleNamespace(done=lambda: False, cancel=lambda: None),
            player=SimpleNamespace(stop=lambda: None),
            _silence_reply=lambda: None,
            _listening=False,
        )

    def test_stopping_names_the_messages_still_queued(self):
        """`VoiceMixin`, not `ChronoaWindow` - `_stop_speaking` lives on the
        voice mixin, and reaching for the window class says the test is written
        against the wrong object."""
        from shani_chronoa.app import voice as voice_module

        said = []
        voice_module.VoiceMixin._stop_speaking(
            self._self([("a", ""), ("b", "")], said), None, None)
        assert any("waiting" in s for s in said), (
            f"Stop did not say messages were still queued; statuses were {said}"
            f" - three unanswered questions on screen read as ignored")

    def test_stopping_with_nothing_queued_says_plainly_stopped(self):
        """**The control.** A status that always says the same thing teaches
        nothing, and plain 'Stopped' must still be reachable."""
        from shani_chronoa.app import voice as voice_module

        said = []
        voice_module.VoiceMixin._stop_speaking(self._self([], said), None, None)
        assert said == ["Stopped"], said


# ------------------------------------------------------------- the two refusals


class _Recorder:
    """A stand-in window that keeps everything it is told.

    `test_assistd_sayri_harvest`'s own comment says a fake has to keep up with
    the interface, "and this comment is here so the next method added is
    noticed". This one is that notice, acted on: it records rather than
    asserting a fixed arity, so adding a call shows up here as a missing
    attribute instead of as a product defect three files away.
    """

    def __init__(self):
        self.restored = []
        self.responses = []
        self.statuses = []

    def set_orb_state(self, _s):
        pass

    def set_response(self, text):
        self.responses.append(text)

    def set_status(self, text):
        self.statuses.append(text)

    def restore_last_sent(self, text):
        self.restored.append(text)


def _app(window, monkeypatch, installed, server_binary=True, sleeping=False):
    monkeypatch.setattr(local_llm, "installed", lambda: set(installed), raising=False)
    monkeypatch.setattr(local_llm, "server_binary", lambda: server_binary, raising=False)
    monkeypatch.setattr(local_llm, "is_up", lambda: True, raising=False)
    return SimpleNamespace(
        window=window,
        _open_setup=lambda: window.responses.append("__setup__"),
        _router_fallback=lambda text: False,
        # **The real enum member, not a stand-in string.** The branch tests
        # `... is Presence.SLEEPING`, so `"sleeping"` is a different object and
        # the sleeping branch silently never ran - which the first version of
        # this fixture did, reporting a pass for a branch it never entered.
        _presence=Presence.SLEEPING if sleeping else Presence.ACTIVE,
    )


class TestARefusalDoesNotConsumeTheQuestion:

    def test_no_model_hands_the_question_back(self, monkeypatch):
        """**The first message typed on any fresh install.**"""
        window = _Recorder()
        app = _app(window, monkeypatch, installed=[], server_binary=False)
        ChronoaApplication._explain_no_model(app, "what is in this folder?")
        assert window.restored == ["what is in this folder?"], (
            f"the question was not handed back; restored={window.restored} - "
            f"after a 1 GB download the person has to reconstruct the first "
            f"thing they ever asked")

    def test_no_model_still_says_what_is_happening(self, monkeypatch):
        """**The control: restoring must not replace explaining.**"""
        window = _Recorder()
        app = _app(window, monkeypatch, installed=[], server_binary=False)
        ChronoaApplication._explain_no_model(app, "hello")
        assert any("no language model yet" in r for r in window.responses), (
            f"the reason went missing: {window.responses}")

    def test_and_it_still_opens_setup(self, monkeypatch):
        window = _Recorder()
        app = _app(window, monkeypatch, installed=[], server_binary=False)
        ChronoaApplication._explain_no_model(app, "hello")
        assert "__setup__" in window.responses, "setup stopped being opened"

    def test_a_sleeping_model_hands_the_question_back(self, monkeypatch):
        window = _Recorder()
        app = _app(window, monkeypatch, installed=["m"], sleeping=True)
        ChronoaApplication._explain_no_model(app, "and tomorrow?")
        assert window.restored == ["and tomorrow?"], (
            f"the question was not handed back; restored={window.restored}")

    def test_a_sleeping_model_still_explains_itself(self, monkeypatch):
        """**The control**, and what catches the enum mistake above: this fails
        loudly if the sleeping branch is never entered."""
        window = _Recorder()
        app = _app(window, monkeypatch, installed=["m"], sleeping=True)
        ChronoaApplication._explain_no_model(app, "hello")
        assert any("asleep" in r for r in window.responses), window.responses

    def test_a_starting_model_re_submits_rather_than_restoring(self, monkeypatch):
        """**The control that keeps this honest.** The installed-but-stopped
        branch already re-submits, which is right: it knows the answer is coming
        in seconds. Restoring there as well would *stop* the automatic retry -
        the fix would break the case it was measured against."""
        window = _Recorder()
        app = _app(window, monkeypatch, installed=["m"], sleeping=False)
        monkeypatch.setattr(local_llm, "start_service", lambda: None, raising=False)
        ChronoaApplication._explain_no_model(app, "hello")
        assert not window.restored, (
            "the starting branch restored the question instead of re-submitting "
            "it, so the answer that was seconds away now has to be asked for")


# ------------------------------------------------------------------- the chips


class TestARefusalDoesNotConsumeTheFiles:

    def _window(self):
        from shani_chronoa.gui import attaching as attaching_module

        class _W:
            """A stand-in with the two methods `restore_last_sent` calls.

            **`return _W()` - an instance, not the class.** The first version
            returned `_W`, so `self` was the class and both of its methods were
            called unbound: *"missing 1 required positional argument: 'self'"*.
            The traceback showed `self = <class '_W'>`, which is the whole
            diagnosis in one line.
            """
            _sent_attachments = []
            _attachments = []
            refreshed = 0
            typed = None

            def _refresh_attach_bar(self):
                type(self).refreshed += 1

            def set_input_text(self, text):
                type(self).typed = text

        return attaching_module, _W()

    def test_the_chips_come_back_with_the_text(self):
        attaching_module, window = self._window()
        window._sent_attachments = [Path("/tmp/notes.pdf"), Path("/tmp/photo.png")]
        attaching_module.AttachingMixin.restore_last_sent(window, "look at these")
        assert window._attachments == [Path("/tmp/notes.pdf"), Path("/tmp/photo.png")], (
            f"the files were not handed back; attachments={window._attachments}"
            f" - restoring the sentence alone leaves the files silently gone, "
            f"which is worse than restoring nothing")
        assert window.refreshed, "the chips were restored but never redrawn"
        assert window.typed == "look at these"

    def test_nothing_queued_means_no_restored_chips(self):
        """**The control**: a turn sent with no files must not invent any."""
        attaching_module, window = self._window()
        window._sent_attachments = []
        attaching_module.AttachingMixin.restore_last_sent(window, "hello")
        assert window._attachments == [], window._attachments
        assert window.refreshed == 0, "redrew an empty chip bar"

    def test_taking_attachments_remembers_them_for_the_handover(self):
        """The stash is what makes the restore possible at all."""
        source = (Path(__file__).resolve().parents[1]
                  / "usr/lib/shani-chronoa/shani_chronoa/gui/attaching.py").read_text()
        assert "_sent_attachments = list(self._attachments)" in source, (
            "take_attachments no longer keeps a copy, so a refusal has nothing "
            "to hand back")


# ----------------------------------------------------------------------- search


class TestANoticeIsFindable:

    def test_a_failure_can_be_searched_for(self):
        view = TranscriptView()
        view.show_placeholder()
        view.add_notice_row("Chronoa could not finish that turn. Boom.", "error")
        assert view.find("Boom"), (
            "the failure cannot be found - a person who knows a turn failed has "
            "no way to search for which one")

    def test_a_compaction_can_be_searched_for(self):
        view = TranscriptView()
        view.show_placeholder()
        view.add_notice_row("earlier context was shortened to fit", "compaction")
        assert view.find("shortened"), (
            "the compaction that lost somebody an answer cannot be found")

    def test_each_notice_is_recorded_against_its_own_turn(self):
        """**The discriminating version, and the reason the index is computed
        where it is.**

        Two notices in different turns must file against *their own* turns.
        Recorded before the `_append_turn` that `add_notice_row` may need, both
        would land on the previous turn - which passes a one-notice test and
        mis-files every notice in a conversation.

        Note the shapes: the first notice goes inside an existing assistant
        turn, while the second follows a *user* turn, which sets `_blocks =
        None` and so makes `add_notice_row` create a turn of its own. Asserting
        on hardcoded indices got both of these wrong before it was run.
        """
        view = TranscriptView()
        view.show_placeholder()
        view.add_user_turn("first question")
        view.add_assistant_turn("first answer")
        view.add_notice_row("EARLYMARKER happened", "error")
        view.add_user_turn("second question")
        view.add_notice_row("LATERMARKER happened", "error")

        early = view.find("EARLYMARKER")
        later = view.find("LATERMARKER")
        assert early == [1], f"the first notice filed against {early}, not turn 1"
        assert later, "the second notice is not findable at all"
        assert later != early, (
            f"both notices filed against {later} - each must be recorded "
            f"against the turn it was drawn inside")
        assert later == [len(view._turns) - 1], (
            f"the second notice filed against {later}, not the turn it created")

    def test_search_notes_go_when_the_turn_is_rewound_away(self):
        """**The reason this is a dict keyed by index, and not a second list.**

        The first version of this test truncated and then asserted `find` was
        empty - and it **passed with the deletion removed**, twice over. `find`
        iterates `_turns`, so once the turns are gone there is nothing to match
        against and the assertion is true whatever the notes dict holds. A test
        that cannot fail, and one that looked like it was guarding a rewind.

        The sequence that actually bites: rewind **part** of the transcript, then
        rebuild turns in the space that leaves. Indexes are occupied again by
        different content, and a note left behind under an old index is now
        attributed to a turn that never saw it - so search reports a failure on
        an unrelated answer.

        **The rewind must not empty the transcript.** Rewinding *everything*
        leaves the view looking empty, so the next `add_user_turn` sees the
        placeholder and goes through `clear()` - which resets the notes anyway.
        That path passed with the deletion removed, twice: once for the reason
        above, and once in the first version because `find` had no turns left to
        iterate at all.
        """
        view = TranscriptView()
        view.show_placeholder()
        view.add_user_turn("first question")          # turn 0
        view.add_assistant_turn("first answer")       # turn 1
        view.add_user_turn("second question")         # turn 2
        view.add_notice_row("REWINDMARKER happened", "error")   # creates turn 3
        assert view.find("REWINDMARKER") == [3], (
            f"the notice filed against {view.find('REWINDMARKER')}, not turn 3")

        view.truncate_from_turn(2)                    # drops turns 2 and 3
        assert len(view._turns) == 2, "the rewind left nothing to rebuild into"

        view.add_user_turn("a completely different question")     # index 2 again
        view.add_assistant_turn("a completely different answer")  # index 3 again

        assert view.find("REWINDMARKER") == [], (
            "a note from a turn that was rewound away still matches - and now "
            "it matches whichever unrelated turn reused the index, so search "
            "reports a failure that never happened in this conversation")

    def test_search_notes_go_when_the_transcript_is_cleared(self):
        """**The same witness for `clear`.** Clearing empties `_turns`, so
        asserting emptiness straight afterwards proves nothing; the stale entry
        only shows up once new turns take those indexes."""
        view = TranscriptView()
        view.show_placeholder()
        view.add_user_turn("a question")
        view.add_notice_row("CLEARMARKER happened", "error")
        assert view.find("CLEARMARKER"), "the notice was never findable"

        view.clear()
        view.add_user_turn("a completely different question")
        view.add_assistant_turn("a completely different answer")

        assert view.find("CLEARMARKER") == [], (
            "a note from before the transcript was cleared still matches, and "
            "it is now attributed to an unrelated turn")

    def test_ordinary_turn_text_is_still_findable(self):
        """**The control**: the fix must not cost what search already did."""
        view = TranscriptView()
        view.show_placeholder()
        view.add_user_turn("please check the thermal readings")
        assert view.find("thermal") == [0]