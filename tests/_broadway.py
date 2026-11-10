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


#: A tiny client that answers whether a Broadway display is really there.
#:
#: **Two things, in this order.** `Gtk.init_check()` is what *initialises*
#: GDK - and it answers True whether or not a display came up, so it is not
#: the question. `Gdk.Display.get_default()` is the question - and it answers
#: None until something has initialised GDK. Asking it alone, with no
#: `Gtk.init_check()` first, says "no display" about a display that is
#: there.
_CLIENT = (
    "import gi\n"
    "gi.require_version('Gtk', '4.0')\n"
    "from gi.repository import Gtk, Gdk\n"
    "Gtk.init_check()\n"
    "print('DISPLAY_OK' if Gdk.Display.get_default() is not None else 'NO_DISPLAY')\n"
)


def display_works(number: int, runtime: "pathlib.Path | str") -> bool:
    """Whether a client can actually get the display at `number`.

    `Gdk.Display.get_default()` is the question, because `Gtk.init_check()`
    answers True with no display at all - the trap this repository records
    for `test_sense_idle`'s guard.
    """
    import os
    import subprocess
    import sys

    env = dict(os.environ)
    env.update(GDK_BACKEND="broadway", BROADWAY_DISPLAY=f":{number}",
               XDG_RUNTIME_DIR=str(runtime))
    env.pop("DISPLAY", None)
    env.pop("WAYLAND_DISPLAY", None)
    try:
        out = subprocess.run([sys.executable, "-c", _CLIENT], capture_output=True,
                             text=True, timeout=30, env=env)
    except (OSError, subprocess.SubprocessError):
        return False
    return "DISPLAY_OK" in out.stdout


def start_display(runtime: "pathlib.Path | str", start: int = 40,
                   stop: int = 2600, attempts: int = 5):
    """Start a private `gtk4-broadwayd` and return `(number, process)`.

    **The display is verified before it is handed back.** A daemon that
    exits - its port taken, its runtime directory unwritable, its socket
    stale - leaves a socket file or nothing at all, and a fixture that
    yields anyway hands the tests a display that does not exist. That
    reads as broken panels ("the harness produced nothing"), which blames
    the product for the environment. Measured on a CI runner
    (2026-11-10): 39 panel tests failed exactly that way.

    So the client is asked whether it can get the display, and a number
    that does not work is given back and another is tried.

    Raises RuntimeError when no display could be started - which is a
    failure of the environment, said as one.
    """
    import os
    import shutil
    import subprocess
    import time

    daemon = shutil.which("gtk4-broadwayd")
    if daemon is None:
        raise RuntimeError("gtk4-broadwayd is not installed")
    base = pathlib.Path(runtime)
    for _attempt in range(attempts):
        number = free_display(base, start, stop)
        if number is None:
            break
        proc = subprocess.Popen([daemon, f":{number}"],
                                stdout=subprocess.DEVNULL,
                                stderr=subprocess.DEVNULL)
        deadline = time.time() + 10
        while time.time() < deadline and not (
                base / f"broadway{number + 1}.socket").exists():
            time.sleep(0.1)
        if display_works(number, base):
            return number, proc
        proc.terminate()
        try:
            proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            proc.kill()
        # The socket file this daemon left behind would make the number look
        # taken for the next attempt, so it is removed with the daemon.
        try:
            (base / f"broadway{number + 1}.socket").unlink()
        except OSError:
            pass
    raise RuntimeError(
        "no private Broadway display could be started after "
        f"{attempts} attempt(s) - the environment cannot give these tests a "
        "display, which is not the same as the panels being broken")
