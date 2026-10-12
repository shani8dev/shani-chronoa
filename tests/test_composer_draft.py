"""The text you typed survives a crash.

Chronoa had no draft persistence: compose a long question, the daemon restarts
or the window closes, and it is gone. The store is tested here without a window,
and the wiring is tested against a real one - because "the store works" and
"the window calls it" are separate claims and only the second one is the feature.

**Nothing here asserts a file exists where a file should not.** The store is
private to the user, so the mode it is written at is part of its contract rather
than an implementation detail.
"""

import os
import stat
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "usr/lib/shani-chronoa"))

from shani_chronoa import composer_draft  # noqa: E402


# -- the store -------------------------------------------------------------


def test_a_draft_comes_back(tmp_path):
    path = tmp_path / "composer.draft"
    assert composer_draft.save("what did I want to ask about the backup?", path)
    assert composer_draft.load(path) == "what did I want to ask about the backup?"


def test_saving_empty_is_the_same_as_clearing(tmp_path):
    """The window saves on every keystroke, so a first run with nothing typed
    must not read as a failure."""
    path = tmp_path / "composer.draft"
    assert composer_draft.save("", path) is True
    assert composer_draft.load(path) == ""


def test_unicode_survives_the_round_trip(tmp_path):
    """A draft in a language the keyboard reports differently is still a draft."""
    path = tmp_path / "composer.draft"
    text = "backup चल रहा है — /usr/lib, 100% ✓\nsecond line"
    composer_draft.save(text, path)
    assert composer_draft.load(path) == text


def test_the_draft_is_not_world_readable(tmp_path):
    """It is whatever you were typing: as private as the conversation."""
    path = tmp_path / "composer.draft"
    composer_draft.save("a secret I was about to send", path)
    mode = stat.S_IMODE(os.stat(path).st_mode)
    assert mode == 0o600, f"the draft is {oct(mode)}; it should be 0o600"


def test_a_corrupt_draft_is_an_empty_draft_not_a_crash(tmp_path):
    """A draft is not worth failing a window constructor over."""
    path = tmp_path / "composer.draft"
    path.write_text("{not json at all", encoding="utf-8")
    assert composer_draft.load(path) == ""


def test_a_draft_of_the_wrong_shape_is_ignored(tmp_path):
    path = tmp_path / "composer.draft"
    path.write_text('["a list", "not a mapping"]', encoding="utf-8")
    assert composer_draft.load(path) == ""
    path.write_text('{"text": 42}', encoding="utf-8")
    assert composer_draft.load(path) == ""


def test_an_absent_draft_is_not_an_error(tmp_path):
    assert composer_draft.load(tmp_path / "never-written") == ""
    assert composer_draft.has_draft(tmp_path / "never-written") is False


def test_a_giant_paste_is_capped_rather_than_written_whole(tmp_path):
    """A runaway paste must not turn every keystroke into a megabyte write."""
    path = tmp_path / "composer.draft"
    huge = "x" * (composer_draft.MAX_DRAFT_CHARS + 5000)
    composer_draft.save(huge, path)
    back = composer_draft.load(path)
    assert len(back) == composer_draft.MAX_DRAFT_CHARS
    assert back == "x" * composer_draft.MAX_DRAFT_CHARS


def test_clearing_removes_it(tmp_path):
    path = tmp_path / "composer.draft"
    composer_draft.save("something", path)
    assert composer_draft.clear(path) is True
    assert composer_draft.load(path) == ""
    assert composer_draft.clear(path) is True, "clearing twice must not raise"


def test_a_save_leaves_no_temporary_file_behind(tmp_path):
    """`os.replace` is what makes the write atomic; a temp file left behind is
    the sign that it failed partway and the next save would trip over it."""
    path = tmp_path / "composer.draft"
    composer_draft.save("first", path)
    composer_draft.save("second", path)

    # **Only files matching this store's own prefix**, not "everything else in
    # `tmp_path`". The first version asserted the latter and failed on a `home/`
    # directory - created by `conftest`'s fake-HOME fixture, which shares this
    # temp dir. The test was reporting on a directory it does not own, which is
    # the same mistake as blaming the wrong process for a bus name.
    leftovers = sorted(p.name for p in tmp_path.iterdir()
                       if p.name.startswith(".composer-"))
    assert not leftovers, f"left behind: {leftovers}"
    assert sorted(p.name for p in tmp_path.iterdir()
                  if p.name.endswith(".draft") or p.name.startswith(".composer")) \
        == ["composer.draft"], "the atomic write left the draft in a temp name"


def test_the_path_follows_the_data_home(tmp_path, monkeypatch):
    """Resolved at call time, never captured at import - a test that moves
    XDG_DATA_HOME must not write into the previous home's draft."""
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path))
    assert str(tmp_path) in str(composer_draft.draft_path()), (
        "the draft path was captured at import, so it points at the home that "
        "existed when this module loaded")


# -- the wiring ------------------------------------------------------------


_WINDOW_SEQ = 0


def _point_store_at(tmp_path):
    """Point the draft store at this test's own directory."""
    target = tmp_path / "composer.draft"
    composer_draft.draft_path = lambda: target
    return target


def _window():
    import gi
    gi.require_version("Gtk", "4.0")
    from gi.repository import Gtk

    from shani_chronoa.gui.window import ChronoaWindow

    global _WINDOW_SEQ
    _WINDOW_SEQ += 1
    # **A D-Bus name segment may not begin with a digit** - `id(object())` is
    # hex and usually does, which raises
    # `g_application_id_is_valid` outright. Measured on the first attempt.
    app = Gtk.Application(
        application_id="test.draft." + format(_WINDOW_SEQ, "x"))
    return ChronoaWindow(app)


def test_typing_marks_a_draft_to_save():
    """The debounce is armed on the first keystroke, not on send."""
    window = _window()
    try:
        window._set_input_text("half a question I have not sent yet")
        assert window._draft_timeout is not None, (
            "typing armed no save, so the text is only ever in memory")
    finally:
        if window._draft_timeout is not None:
            from gi.repository import GLib
            GLib.source_remove(window._draft_timeout)


def test_a_pending_draft_is_written_when_the_window_closes(tmp_path):
    """**The debounce must not be the last word.**

    Ten seconds of debounce means a window closed inside that window would
    discard exactly the text the feature exists to protect.

    **Driven through `_on_close_request`, not by calling `_flush_draft_now`.**
    The first version called the method directly and therefore passed with the
    call to it deleted from `close` - a mutation that survived all fifteen
    tests. This one closes the window for real and reads the file back.
    """
    window = _window()
    try:
        # **A stub config, not a shadowed property.** `wake_word_enabled` is a
        # read-only property over a gsetting: assigning to it raises
        # `AttributeError: property ... has no setter`, and so does setting it on
        # the *instance*, because a data descriptor on the class wins over
        # instance attributes. Both measured. `_on_close_request` only reads it,
        # so swapping the config for a two-line stand-in is enough - and says
        # exactly what the test means.
        class _Config:
            wake_word_enabled = False
        window._config = _Config()
        path = _point_store_at(tmp_path)
        window._set_input_text("typed and then closed immediately")
        assert not path.exists(), "the pre-condition: nothing is saved yet"
        window._on_close_request()
        assert path.exists(), (
            "the window closed and the draft was never written - the ten second "
            "debounce outlived the window")
        assert "typed and then closed" in composer_draft.load(path)
    finally:
        if window._draft_timeout is not None:
            from gi.repository import GLib
            GLib.source_remove(window._draft_timeout)


def test_sending_clears_the_draft(tmp_path):
    """**Asserts the file, not the composer.**

    A draft that outlives the turn would restore text that has already been
    asked, and the first version of this test only checked that the composer was
    empty - which `_set_input_text("")` does anyway, so it passed with
    `clear_draft()` deleted from `_submit_input`. That mutation survived all
    fifteen tests, the same way the close-flush one did.
    """
    window = _window()
    try:
        path = _point_store_at(tmp_path)
        window._draft_timeout = None
        sent = []
        window.connect("user-input", lambda _w, text: sent.append(text))
        window._set_input_text("this one is actually going out")
        # **The draft has to exist before the send, or this proves nothing.**
        # The debounce had not fired, so the file was never written and
        # `clear_draft()` was removing something that was not there - which is
        # why deleting the `clear_draft()` call from `_submit_input` still
        # passed. Written here by hand to stand in for the ten seconds elapsed.
        assert composer_draft.save(window._input_text(), path)
        assert path.exists()
        window._submit_input()
        assert sent, "nothing was sent, so this proves nothing"
        assert not path.exists(), (
            "the turn was sent and the draft is still on disk - reopening the "
            "window would offer a question that has already been asked")
    finally:
        pass


def test_restoring_does_not_immediately_save_it_again():
    """**The loop that would otherwise exist.**

    `restore_draft` calls `_set_input_text`, which fires `changed`, which calls
    `_note_draft`, which would save the text it just read - harmless here, but
    it means restoring always leaves a pending timer and the draft can never
    actually be cleared by someone who types and deletes.
    """
    window = _window()
    try:
        window._draft_source = True
        window._set_input_text("being restored")
        assert window._draft_timeout is None, (
            "restoring armed a save, so a draft can never be left alone")
    finally:
        window._draft_source = False