"""inotify backend: same poll contract, opt-in, and honest on failure."""

from shani_chronoa import inotify_watch as iw
from shani_chronoa.triggers.sources import PollingDirWatcher, WatcherError


def _event(wd, mask, name):
    raw = name.encode() + b"\x00"
    return iw._EVENT.pack(wd, mask, 0, len(raw)) + raw


def test_decoder_returns_full_paths_for_named_events():
    got = iw._decode_events(
        _event(7, iw.IN_MODIFY, "a.txt") + _event(7, iw.IN_DELETE, "b.txt"),
        {7: "/home/me/watch"},
    )
    assert got == ["/home/me/watch/a.txt", "/home/me/watch/b.txt"]


def test_decoder_ignores_unknown_watch_descriptors():
    assert iw._decode_events(_event(9, iw.IN_CREATE, "x"), {7: "/home/me/watch"}) == []


def test_an_overflow_is_an_error_not_a_quiet_directory():
    data = iw._EVENT.pack(7, iw.IN_Q_OVERFLOW, 0, 0)
    try:
        iw._decode_events(data, {7: "/tmp"})
    except WatcherError:
        pass
    else:
        raise AssertionError("a dropped event was reported as no change")


def test_build_watcher_falls_back_to_polling(monkeypatch):
    monkeypatch.setattr(iw, "available", lambda: False)
    assert isinstance(iw.build_watcher(), PollingDirWatcher)


def test_build_watcher_uses_inotify_where_it_works(monkeypatch):
    monkeypatch.setattr(iw, "available", lambda: True)
    monkeypatch.setattr(iw, "InotifyDirWatcher", lambda **kw: "inotify")
    assert iw.build_watcher() == "inotify"


def test_the_first_poll_only_arms_the_watch(tmp_path, monkeypatch):
    (tmp_path / "a.txt").write_text("a")

    class Libc:
        def __init__(self):
            self.added = []

        def inotify_init1(self, flags):
            assert flags & iw.IN_NONBLOCK
            return 3

        def inotify_add_watch(self, fd, path, mask):
            self.added.append(path.decode())
            return len(self.added)

    lib = Libc()
    monkeypatch.setattr(iw.select, "select", lambda *a: ([], [], []))
    watcher = iw.InotifyDirWatcher(libc=lib)
    assert watcher.poll(tmp_path) == []
    assert str(tmp_path).encode().decode() in lib.added


def test_a_failed_instance_open_is_a_watcher_error(tmp_path):
    class Libc:
        def inotify_init1(self, flags):
            return -1

    try:
        iw.InotifyDirWatcher(libc=Libc()).poll(tmp_path)
    except WatcherError:
        pass
    else:
        raise AssertionError("a refused inotify instance was reported as a quiet watch")


def test_available_never_raises(monkeypatch):
    def broken():
        raise OSError("no libc")

    monkeypatch.setattr(iw, "_libc", broken)
    assert iw.available() is False
