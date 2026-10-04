"""One process runs the trigger engine: the window, or the background daemon - never both.

Both drive the same rules file. Two engines polling it would fire every rule
twice and race on its fingerprints, so the first process to take this lock
runs the engine for as long as it lives and the other leaves the rules alone,
trying again on its next poll (so the daemon takes over the moment the window
closes, and vice versa). flock() is released by the kernel when the holder
exits or crashes, so a dead process can never keep the rules stopped.
"""

from __future__ import annotations

import fcntl
import os
from pathlib import Path

_held = {"fd": None}


def lock_path() -> Path:
    base = os.environ.get("XDG_RUNTIME_DIR") or f"/tmp/shani-chronoa-{os.getuid()}"
    return Path(base) / "shani-chronoa" / "triggers.lock"


def holds() -> bool:
    """True if this process runs the trigger engine (taking the lock if it is free)."""
    if _held["fd"] is not None:
        return True
    path = lock_path()
    try:
        path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        fd = os.open(path, os.O_RDWR | os.O_CREAT, 0o600)
    except OSError:
        return True  # nowhere to coordinate: behave as before rather than run no rules at all
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        os.close(fd)
        return False
    os.ftruncate(fd, 0)
    os.write(fd, f"{os.getpid()}\n".encode())
    _held["fd"] = fd
    return True


def release() -> None:
    fd = _held["fd"]
    if fd is not None:
        _held["fd"] = None
        try:
            fcntl.flock(fd, fcntl.LOCK_UN)
        finally:
            os.close(fd)
