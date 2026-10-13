"""A draft belongs to the conversation it was written for.

The composer is **one widget** and the draft was **one file**, so a half-typed
question travelled with you: open another conversation and the box still held
what you were writing to the previous one, and Enter put it into the new one -
silently, because the transcript then shows your question in a conversation it
has nothing to do with, and the only way to notice is to scroll back.

`Ctrl+N` had already been fixed for exactly this misfire. `conversation.
_reset_conversation` clears the composer, and carries a comment describing the
bug in full - *"the next Enter sent a question about the previous conversation
into the new one, and nothing on screen said so"*. So the hole was only in
**switching**, which nobody had looked at, and the existing fix hid the shape
of it: the obvious conclusion from `clear_input()` on Ctrl+N is "the composer is
handled", and it is handled on exactly one of the two paths.

**Text and files move together, or not at all.** A draft is a sentence *and* the
attachments that sentence refers to. Restoring the words and leaving the chip bar
empty shows somebody reading "check these three files" above nothing, which is
the partial restore `composer_draft`'s own docstring calls worse than none.

**The drafts are switched, not cleared.** Going to a new conversation keeps what
you had typed against the old one, so going back finds it where you left it -
which is what `Ctrl+N` used to throw away.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "usr/lib/shani-chronoa"))

import gi  # noqa: E402

gi.require_version("Gtk", "4.0")

from shani_chronoa import composer_draft  # noqa: E402


def _store(tmp_path, monkeypatch):
    """The draft directory, with `draft_path(key)` real and pointed here."""
    monkeypatch.setattr(composer_draft, "session_dir_hook",
                        lambda: tmp_path, raising=False)
    real = composer_draft.draft_path

    def draft_path(key=None):
        # The real naming, only rooted in the test's directory.
        path = real(key)
        path = tmp_path / path.name
        return path
    monkeypatch.setattr(composer_draft, "draft_path", draft_path)
    return tmp_path


class TestEachConversationHasItsOwnDraftFile:

    def test_two_conversations_do_not_share_a_file(self, tmp_path, monkeypatch):
        _store(tmp_path, monkeypatch)
        one = composer_draft.draft_path("alpha")
        two = composer_draft.draft_path("beta")
        assert one != two, (
            "both conversations resolve to the same draft file, so one overwrites "
            "the other and the person loses whichever they typed first")
        assert composer_draft.draft_path("") != one, (
            "a keyed draft collides with the unkeyed one")

    def test_no_key_is_still_the_old_single_file(self, tmp_path, monkeypatch):
        """**Backwards compatibility, deliberately.** A draft written before
        this existed must still be found - otherwise the feature costs every
        existing draft on the machine."""
        _store(tmp_path, monkeypatch)
        assert composer_draft.draft_path().name == "composer.draft"

    def test_a_key_cannot_escape_the_session_directory(self, tmp_path, monkeypatch):
        """**The reason the key is sanitised at all.** It arrives from a session
        reference, and a reference containing `../` would otherwise write
        outside the directory whose `0o700` and `0600` are load-bearing - a
        draft holding what somebody was typing, written somewhere unprotected.
        """
        _store(tmp_path, monkeypatch)
        escaped = composer_draft.draft_path("../../etc/passwd")
        assert escaped.parent == tmp_path, (
            f"a key with a path separator escaped the session directory: "
            f"{escaped}")

    def test_saving_and_loading_round_trips_text_and_files(self, tmp_path,
                                                          monkeypatch):
        _store(tmp_path, monkeypatch)
        path = composer_draft.draft_path("alpha")
        assert composer_draft.save("look at these", path,
                                   files=["/tmp/a.pdf", "/tmp/b.png"])
        assert composer_draft.load_entry(path) == (
            "look at these", ["/tmp/a.pdf", "/tmp/b.png"])

    def test_empty_text_clears_the_files_too(self, tmp_path, monkeypatch):
        """**The control, and why `files` is conditioned on `text`.** A draft
        with no sentence but three attachments is not a draft, and leaving the
        files behind puts chips in the composer of a conversation with nothing
        typed in it."""
        _store(tmp_path, monkeypatch)
        path = composer_draft.draft_path("alpha")
        composer_draft.save("look at these", path, files=["/tmp/a.pdf"])
        composer_draft.save("", path, files=["/tmp/a.pdf"])
        assert composer_draft.load_entry(path) == ("", [])

    def test_a_draft_written_before_files_existed_still_loads(self, tmp_path,
                                                              monkeypatch):
        """**The control for `load_entry`.** An older file has no `files` key at
        all, and that is not an error - it restores its text and no
        attachments, which is exactly what it held."""
        _store(tmp_path, monkeypatch)
        path = composer_draft.draft_path("alpha")
        composer_draft.save("written by an older build", path)
        assert composer_draft.load_entry(path) == ("written by an older build", [])

    def test_load_still_returns_text_only(self, tmp_path, monkeypatch):
        """`load` has other callers that want the sentence and nothing else."""
        _store(tmp_path, monkeypatch)
        path = composer_draft.draft_path("alpha")
        composer_draft.save("hello", path, files=["/tmp/a.pdf"])
        assert composer_draft.load(path) == "hello"


class TestTheComposerFollowsTheConversation:

    def _window(self, tmp_path, monkeypatch, conversation="alpha"):
        """A real `ChronoaWindow`, draft store pointed at `tmp_path`.

        **`conversation` is opened on the way in, as the application does at
        startup.** `set_conversation` saves the *outgoing* draft under the key
        it is leaving, so a window that has never been named has nothing to
        leave - and the first flush writes to the legacy unkeyed file instead of
        to the conversation just opened. The first version of these tests typed
        first and named later, which the production call order never does, and
        it reported *"going back found 'something else entirely'"* for what is
        actually correct behaviour.

        **A fixture that does not reproduce the production call sequence
        describes a different application**, which is the `GSETTINGS_BACKEND`
        and `fake_bin` lesson again, in a third costume.
        """
        from shani_chronoa.gui.window import ChronoaWindow

        _store(tmp_path, monkeypatch)
        window = ChronoaWindow.__new__(ChronoaWindow)
        window._draft_timeout = None
        window._draft_source = False
        window._draft_restored = True          # set_conversation drives it now
        window._draft_key = ""
        window._attachments = []
        window._input_text = lambda: getattr(window, "_typed", "")
        window._set_input_text = lambda text: setattr(window, "_typed", text)
        window._refresh_attach_bar = lambda: None
        window._scroll_to_end = lambda: None
        from shani_chronoa.gui.window import ChronoaWindow
        ChronoaWindow.set_conversation(window, conversation)
        window._typed = ""
        window._attachments = []
        return window

    def test_switching_shows_the_other_conversations_draft(self, tmp_path,
                                                            monkeypatch):
        from shani_chronoa.gui.window import ChronoaWindow

        window = self._window(tmp_path, monkeypatch)
        window._typed = "a question for the first conversation"
        ChronoaWindow.set_conversation(window, "beta")
        assert window._typed == "", (
            f"the second conversation opened with the first one's draft still "
            f"in the box: {window._typed!r}")

        ChronoaWindow.set_conversation(window, "alpha")
        assert window._typed == "a question for the first conversation", (
            f"going back found {window._typed!r} - the first conversation's "
            f"draft was overwritten by the second")

    def test_the_files_come_back_with_the_words(self, tmp_path, monkeypatch):
        """**The control for the one above.** Restoring only the sentence would
        pass every other test in this file."""
        from shani_chronoa.gui.window import ChronoaWindow

        window = self._window(tmp_path, monkeypatch)
        window._typed = "what is in these"
        window._attachments = [Path("/tmp/report.pdf"), Path("/tmp/plot.png")]
        ChronoaWindow.set_conversation(window, "beta")
        assert window._attachments == [], (
            "beta opened showing alpha's attachments - the same partial "
            "restore, one level below the words")

        ChronoaWindow.set_conversation(window, "alpha")
        assert window._typed == "what is in these"
        assert window._attachments == [Path("/tmp/report.pdf"),
                                       Path("/tmp/plot.png")], (
            f"the words came back without the files: {window._attachments}")

    def test_a_pending_save_cannot_fire_into_the_new_conversation(
            self, tmp_path, monkeypatch):
        """**The silent one.** The debounced write is left armed by typing; if it
        is still pending when the conversation changes, it fires afterwards under
        the *new* key and writes the outgoing conversation's words into the
        incoming one's file. The misfire above, one level down, and nothing on
        screen shows it.

        **What you typed belongs to the conversation you were in.** The first
        version typed "belongs to beta" while still in alpha and then complained
        that alpha's file held it - which is the draft module working correctly.
        The person was in alpha when they typed it, so alpha is where it goes.
        """
        from gi.repository import GLib

        from shani_chronoa.gui.window import ChronoaWindow

        window = self._window(tmp_path, monkeypatch)
        # **A real GLib source, not a made-up handle.** `set_conversation` calls
        # `GLib.source_remove` on whatever is armed, and a fake id is not a
        # source at all - GTK logs a CRITICAL for it and the test fails on the
        # warning rather than on the behaviour. A real 30-second timeout is the
        # state being guarded against: typing arms the debounce, and switching
        # before it fires is ordinary.
        window._draft_timeout = GLib.timeout_add_seconds(30, lambda: False)
        window._typed = "belongs to alpha"
        ChronoaWindow.set_conversation(window, "beta")

        assert window._draft_timeout is None, (
            "the pending save was left armed across the switch - it fires "
            "later, under the new key, and writes the conversation being left "
            "into the one being entered")
        assert window._typed == "", (
            f"beta opened showing alpha's draft: {window._typed!r}")

        window._typed = "belongs to beta"
        ChronoaWindow.set_conversation(window, "alpha")
        assert window._typed == "belongs to alpha", (
            f"going back found {window._typed!r} - a draft was written into "
            f"the wrong conversation's file")

        if window._draft_timeout is not None:
            GLib.source_remove(window._draft_timeout)
        if window._draft_timeout is not None:
            GLib.source_remove(window._draft_timeout)