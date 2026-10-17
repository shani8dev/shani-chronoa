"""Pick a Broadway display number that a new `gtk4-broadwayd` can really use.

Eight test files started a private Broadway display by taking the first number
whose `broadway<N+1>.socket` was missing. **A missing socket is not a free
display.** `gtk4-broadwayd :N` also listens on TCP port 8080+N, and measured
2026-10-15 a leftover `gtk4-broadwayd :77 --port 8177` from an earlier run held
8177 - so the picker chose `:97` (its socket was free), the new daemon printed
"Unable to listen to port 8177: Address already in use" and exited, and 39
panel tests failed as "the harness produced nothing", which reads as broken
panels. Stale socket files from killed daemons had already used up 41-97.

So both are checked: the socket file is absent *and* the port can be bound.
"""
from __future__ import annotations

import pathlib
import socket
from typing import Optional


def _port_free(port: int) -> bool:
    with socket.socket(socket.AF_INET6, socket.SOCK_STREAM) as probe:
        probe.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            probe.bind(("::", port))
        except OSError:
            return False
    return True


def free_display(runtime: "pathlib.Path | str", start: int, stop: int) -> Optional[int]:
    """The first N in [start, stop) with no socket file and a bindable port."""
    base = pathlib.Path(runtime)
    return next((n for n in range(start, stop)
                 if not (base / f"broadway{n + 1}.socket").exists()
                 and _port_free(8080 + n)), None)
