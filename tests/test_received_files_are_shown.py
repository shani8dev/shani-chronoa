"""Files that arrived from a phone, shown rather than described.

**The gap.** `obex_receive` saves a file into Downloads by name, never
overwriting, and `free_path` is what makes that true - so the folder *is* the
store. The Phone panel's Files tab said "share them to this computer over
Bluetooth" and stopped: a person who shared something had no way to see what was
saved or where, and the only confirmation was a notification they might have
missed. The sentence had been the whole of it since the receiver landed.

**The list is the folder, not an index of it.** A second list would be a way for
the panel to disagree with the files it is describing, which is the failure this
repo keeps recording under other names.
"""

from __future__ import annotations

import os
import pathlib
import time

import pytest

from shani_chronoa.gui.surfaces import phone as panel


class _Config:
    def __init__(self, **flags):
        self._flags = flags

    def get_bool(self, key, default=False):
        return bool(self._flags.get(key, default))

    def get(self, key, default=None):
        return default

    def get_string(self, key, default=""):
        return self._flags.get(key, default)


@pytest.fixture(autouse=True)
def _isolated_downloads(tmp_path, monkeypatch):
    """A Downloads of our own. `obex_receive.downloads()` reads
    `GLib.get_user_special_dir` and falls back to `~/Downloads`, so the real one
    is stubbed rather than written to - and a test that wrote into a person's
    Downloads would be the worst kind of leak."""
    folder = tmp_path / "Downloads"
    folder.mkdir()
    monkeypatch.setattr(panel, "_in_thread", lambda work, done: done(work()))
    import gi
    from gi.repository import GLib
    monkeypatch.setattr(GLib, "get_user_special_dir",
                        lambda _which: str(folder))
    return folder


class _Panel(panel.PhonePanel):
    """The panel without its phone: the Files tab does not need one."""

    def __init__(self, app):
        self._app = app
        self.config = app.config


def _tab(folder: pathlib.Path) -> "panel.PhonePanel":
    view = _Panel(type("A", (), {"config": _Config(**{panel.CONTROL_KEY: True})})())
    widget = view.files_tab()
    return view, widget


class TestWhatArrived:
    def test_the_real_downloads_folder_is_the_store(self, _isolated_downloads):
        """`_received_work` reads the folder `obex_receive.deliver()` writes into,
        rather than a second list that could disagree with it."""
        folder = _isolated_downloads
        for name, when in (("holiday.png", 1_700_000_000), ("notes.txt", 1_600_000_000)):
            path = folder / name
            path.write_bytes(b"x" * 100)
            os.utime(path, (when, when))
        view, _widget = _tab(folder)
        found_folder, found = view._received_work()
        assert found_folder == folder, found_folder
        assert [p.name for p in found] == ["holiday.png", "notes.txt"], found

    def test_the_same_instant_is_not_left_to_directory_order(
            self, _isolated_downloads, monkeypatch):
        """**The same instant is not rare** - two files the receiver moved
        quickly, or two a test seeds together, share a millisecond, and a sort on
        mtime alone then leaves the order to whatever `iterdir()` felt like.

        And it *cannot* be tested through the folder on this box: measured,
        ext4 returns `iterdir()` in reverse-name order here, so the mtime-only
        sort produces the same list as the tiebreak does. A mutation removing the
        tiebreaker stayed green against a folder-based test, which is why this
        drives the key directly - the same reason `_deemphasis_pole` and
        `_capture_time` are named functions elsewhere in this tree.
        """
        folder = _isolated_downloads
        same = 1_700_000_000
        for name in ("a.txt", "b.txt", "c.txt"):
            path = folder / name
            path.write_bytes(b"z")
            os.utime(path, (same, same))
        # `iterdir()` forced to the *other* order, so the key is what decides it
        # and not the filesystem. The first attempt subclassed `Path` and called
        # it with no arguments, which is `Path(".")` - it listed the repository
        # root and the assertion failed against `AGENTS.md`; the second assigned
        # a callable *object* to the class, and `path.iterdir()` then called it
        # with no arguments at all. A closure over the real function is the shape
        # that works, and `monkeypatch` is what restores it.
        import pathlib as _pathlib

        real_iterdir = _pathlib.Path.iterdir
        seen = []

        def ascending(self):
            entries = list(real_iterdir(self))
            if not seen:
                seen.extend(p.name for p in entries)
            return iter(sorted(entries, key=lambda p: p.name))

        monkeypatch.setattr(_pathlib.Path, "iterdir", ascending)

        view, _widget = _tab(folder)
        _folder, found = view._received_work()
        assert [p.name for p in found] == ["c.txt", "b.txt", "a.txt"], (
            "the mtime tie was resolved by directory order rather than by the "
            "name; on another filesystem this is the shelf in the wrong order")

    def test_newest_first(self, _isolated_downloads):
        folder = _isolated_downloads
        for name, when in (("old.txt", 1_600_000_000),
                           ("new.txt", 1_700_000_000)):
            path = folder / name
            path.write_bytes(b"z")
            os.utime(path, (when, when))
        _view, _widget = _tab(folder)
        _folder, found = _view._received_work()
        assert [p.name for p in found] == ["new.txt", "old.txt"], found

    def test_an_empty_folder_is_an_empty_shelf(self, _isolated_downloads):
        view, _widget = _tab(_isolated_downloads)
        _folder, found = view._received_work()
        assert found == [], found

    def test_the_answer_names_the_folder(self, _isolated_downloads):
        """A list of names with no place is "what", not "where" - and the folder
        is where a person goes to open one."""
        folder = _isolated_downloads
        (folder / "a.txt").write_bytes(b"1")
        view, _widget = _tab(folder)
        view._received_done(view._received_work())
        note = view.received_note.get_label()
        assert str(folder) in note, note
        assert "1 file" in note, note

    def test_a_file_that_vanish_between_the_read_does_not_crash(self, _isolated_downloads):
        """`stat` on a file somebody deleted while the panel was building is an
        `OSError`, and a panel that raises on it is a broken tab."""
        folder = _isolated_downloads
        doomed = folder / "doomed.txt"
        doomed.write_bytes(b"z")

        class _Gone:
            name = "doomed.txt"

            def stat(self):
                raise OSError("it is gone")

        view, _widget = _tab(folder)
        # a size that cannot be read is a sentence, not a traceback
        view._received_done((folder, [_Gone()]))
        assert view.received_note.get_label()

    def test_a_folder_that_cannot_be_read_is_a_sentence(self, _isolated_downloads):
        """**Driven from the source, not the sink.** The first version called
        `_received_done` with a ready-made exception, which asserted the sink and
        left the `except OSError` in `_received_work` unexercised - so removing
        it entirely stayed green. The folder is made unreadable instead, because
        a directory whose permissions changed is what actually happens."""
        folder = _isolated_downloads
        # **Built first, broken after.** `_tab()` calls `files_tab()`, and this
        # file's own `_in_thread` stub runs `_received_work()` the moment the
        # tab is built - so chmod'ing before the build raised from the build
        # itself, before any assertion about the sentence. The order is: build
        # (readable), then break, then ask.
        view, _widget = _tab(folder)
        folder.chmod(0o000)
        try:
            with pytest.raises(RuntimeError) as raised:
                view._received_work()
            # **And the panel turns it into a sentence rather than a traceback**
            # - the same contract the other tabs hold.
            view._received_done(raised.value)
            assert "could not read" in view.received_note.get_label().lower(), (
                view.received_note.get_label())
        finally:
            folder.chmod(0o755)

    def test_nothing_arrived_says_so_rather_than_showing_an_empty_box(
            self, _isolated_downloads):
        view, widget = _tab(_isolated_downloads)
        view._received_done(view._received_work())
        # **Off the built page, not off the widget.** Walking `view.received`
        # directly reads a box that may not be in the tree at all - and the
        # mutation that dropped `box.append(self.received)` stayed green
        # against exactly that. What a person sees is the page.
        labels = _texts(widget)
        assert any("Nothing has arrived yet" in t for t in labels), labels


def _texts(widget, out=None):
    if out is None:
        out = []
    if hasattr(widget, "get_label"):
        out.append(widget.get_label())
    if hasattr(widget, "get_title"):
        out.append(widget.get_title())
    child = widget.get_first_child()
    while child is not None:
        _texts(child, out)
        child = child.get_next_sibling()
    return out


def test_human_size_reads_like_a_person():
    import tempfile
    with tempfile.NamedTemporaryFile(suffix=".bin") as handle:
        handle.write(b"0" * 4096)
        handle.flush()
        assert panel._human_size(pathlib.Path(handle.name)).startswith("4.0 kB")
