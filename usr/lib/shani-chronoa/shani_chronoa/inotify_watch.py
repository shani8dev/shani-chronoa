"""inotify backend for `PollingDirWatcher`.

The polling watcher's own docstring plans for a subclass: `poll(root) -> list[str]`
is the whole contract, and an inotify watcher should return the same paths.
This is that backend, using libc through `ctypes` - no new dependency, since
inotify is a Linux kernel interface and every ShaniOS machine has libc.

It is opt-in at construction via `build_watcher()`: a machine without the
symbols (or with a kernel that refuses an instance) keeps the polling watcher,
so nothing that worked before changes. A watch that fails is still
`WatcherError`, never an empty list - a dead watch and a quiet directory are
opposite facts.
"""

from __future__ import annotations

import ctypes
import os
import select
import struct
from pathlib import Path
from typing import Optional

from shani_chronoa.triggers.sources import (
    MAX_WATCH_ENTRIES,
    PollingDirWatcher,
    WatcherError,
)

IN_MODIFY = 0x00000002
IN_CREATE = 0x00000100
IN_DELETE = 0x00000200
IN_MOVED_FROM = 0x00000040
IN_MOVED_TO = 0x00000080
IN_CLOSE_WRITE = 0x00000008
IN_Q_OVERFLOW = 0x00004000
IN_ONLYDIR = 0x01000000
IN_NONBLOCK = 0x00000800
WATCH_MASK = (
    IN_MODIFY
    | IN_CREATE
    | IN_DELETE
    | IN_MOVED_FROM
    | IN_MOVED_TO
    | IN_CLOSE_WRITE
    | IN_ONLYDIR
)
_EVENT = struct.Struct("iIII")


def _libc():
    return ctypes.CDLL(None, use_errno=True)


def available() -> bool:
    """Whether this machine can open an inotify instance. Never raises."""
    try:
        lib = _libc()
        return hasattr(lib, "inotify_init1") and hasattr(lib, "inotify_add_watch")
    except Exception:  # noqa: BLE001
        return False


def _decode_events(data: bytes, watches: dict) -> "list[str]":
    """Paths from one raw inotify read. Pure, so a test needs no kernel.

    An overflow is a `WatcherError`, not an empty list: after an overflow the
    kernel dropped events, and reporting "nothing changed" would be the
    confident wrong answer this whole layer exists to avoid.
    """
    changed: list[str] = []
    offset = 0
    while offset + _EVENT.size <= len(data):
        wd, mask, _cookie, length = _EVENT.unpack_from(data, offset)
        offset += _EVENT.size
        name = (
            data[offset : offset + length]
            .split(b"\x00", 1)[0]
            .decode("utf-8", "replace")
        )
        offset += length
        if mask & IN_Q_OVERFLOW:
            raise WatcherError("the inotify queue overflowed; changes were lost")
        base = watches.get(wd)
        if base is None:
            continue
        changed.append(str(Path(base) / name) if name else str(base))
    return changed


class InotifyDirWatcher:
    """The same interface as `PollingDirWatcher`, backed by one inotify fd."""

    def __init__(self, libc=None) -> None:
        self._libc = libc if libc is not None else _libc()
        self._fd: Optional[int] = None
        self._watches: dict = {}  # wd -> directory

    def _open(self) -> None:
        self._fd = self._libc.inotify_init1(IN_NONBLOCK)
        if self._fd < 0:
            ctypes.set_errno(0)
            raise WatcherError("this machine refused to open an inotify instance")

    def _watch(self, directory: Path) -> None:
        wd = self._libc.inotify_add_watch(
            self._fd, os.fsencode(str(directory)), WATCH_MASK
        )
        if wd < 0:
            return  # an unreadable subdir is skipped
        self._watches[wd] = str(directory)

    def _watch_tree(self, root: Path) -> None:
        count = 0
        for dirpath, dirnames, _files in os.walk(root, followlinks=False):
            dirnames.sort()
            self._watch(Path(dirpath))
            count += 1
            if count >= MAX_WATCH_ENTRIES:
                raise WatcherError(
                    f"{root} holds more than {MAX_WATCH_ENTRIES} directories, so "
                    "an inotify watch cannot cover it"
                )
        if not self._watches:
            raise WatcherError(f"could not watch {root} at all")

    def poll(self, root: Path) -> "list[str]":
        root = Path(root)
        if self._fd is None:
            self._open()
            self._watch_tree(root)
            return []  # arm, then report changes
        changed: list[str] = []
        while select.select([self._fd], [], [], 0)[0]:
            try:
                data = os.read(self._fd, 65536)
            except OSError as exc:
                raise WatcherError(f"the inotify read failed: {exc}") from exc
            if not data:
                break
            changed.extend(_decode_events(data, self._watches))
        # A directory created during the interval gets its own watch, or later
        # changes inside it are invisible while its creation was reported.
        for path in list(changed):
            candidate = Path(path)
            if candidate.is_dir() and str(candidate) not in self._watches.values():
                self._watch(candidate)
        return sorted(set(changed))


def build_watcher(*, prefer_inotify: bool = True, libc=None) -> PollingDirWatcher:
    """The watcher to use: inotify where it works, polling everywhere else.

    Both classes answer `poll(root)`, so a caller needs no branch after this.
    """
    if prefer_inotify and (libc is not None or available()):
        try:
            return InotifyDirWatcher(libc=libc)
        except Exception:  # noqa: BLE001
            pass
    return PollingDirWatcher()
