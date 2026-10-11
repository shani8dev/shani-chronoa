"""Show this computer's screen on a paired phone, over the Bluetooth network.

The module that actually does it (`screen_stream.py`) was **fully built and
reached by nothing** - no skill, no unit, no launcher, no test, from the day it
landed. The scan that found it (`a query-shaped call whose answer is discarded`,
then "who calls this") is the one this repository has recorded finding five dead
modules with, and this is the sixth. It is wired rather than deleted because
there is nothing wrong with it: the ScreenCast portal round trip, the 640x360
VP8 pipeline and the one-viewer HTTP server are each a coherent piece, and the
sizing in `screen_stream.py`'s docstring came from a real measurement of the
link.

**It is a separate process, for the reason `automation_overlay` is.** The
portal's Request/Response round trip is a GLib main loop, and this skill runs in
a sandboxed child that has no main loop of its own. So this file starts
`usr/bin/shani-chronoa-screen-stream` and talks to it, exactly as
`ui_driver.start_cue` starts `shani-chronoa-overlay`.

**Consent-gated, off by default, and named as what it is.** Sharing your screen
is the widest thing an assistant here can do - wider than a file, because it is
everything - so it gets its own switch rather than riding on `phone-control`.
The refusal is the same sentence as `vm` and `container`: what is off, and where
to turn it on.
"""

from __future__ import annotations

import logging
import os
import shutil
import subprocess
from typing import Optional

from shani_chronoa.skills import Skill

logger = logging.getLogger(__name__)

_CONSENT_KEY = "screen-share-enabled"

_ACTIONS = ("status", "start", "stop")

#: What the page says while nothing is streamed. `screen_stream.PAGE` is the
#: viewer; this is the answer a person gets from *asking*.
_LAUNCHER = "usr/bin/shani-chronoa-screen-stream"

#: The process is tracked by PID, never by name. The workspace AGENTS.md records
#: what `pkill -f` does to a session bus; the same rule applies to any cleanup.
_PROC: "dict[str, Optional[subprocess.Popen]]" = {}


def _consent(config) -> "tuple[bool, str]":
    """(allowed, why-not) - the house signature every gated tool returns."""
    if not config.get_bool(_CONSENT_KEY, False):
        return False, (f"Sharing this screen is turned off. Nothing was sent. "
                       f"Enable '{_CONSENT_KEY}' in Settings to allow it.")
    return True, ""


def launcher_path() -> str:
    """The `usr/bin` launcher, however this checkout is laid out.

    Resolved from this file rather than `shutil.which`, because a checkout under
    test has no installed launcher and the answer "not installed" would be about
    the harness rather than the machine.
    """
    here = os.path.dirname(os.path.abspath(__file__))
    # Four levels, not the three `ui_driver.py` uses: that file sits in the
    # package directory and this one sits in `skills/` below it. The first
    # version of this used three and reported "the launcher is not installed"
    # about a machine where it plainly was - a probe that is always False is the
    # same defect as a test that always skips.
    for candidate in (os.path.join(here, "..", "..", "..", "..", "bin",
                                   "shani-chronoa-screen-stream"),):
        path = os.path.abspath(candidate)
        if os.path.exists(path):
            return path
    return shutil.which("shani-chronoa-screen-stream") or ""


def _check_text() -> "tuple[str, str]":
    """`(text, why-not)` for "could this machine do it at all?".

    Answered by the launcher's `--check`, which imports GStreamer and reads the
    kernel's address table - so it names the missing **package** rather than the
    portal's refusal, which tells nobody what to install.
    """
    launcher = launcher_path()
    if not launcher:
        return "not installed", "the screen-stream launcher is not installed"
    try:
        done = subprocess.run([launcher, "--check"], capture_output=True, text=True,
                              timeout=90, check=False)
    except (OSError, subprocess.SubprocessError) as exc:
        return "not answering", f"the check would not run ({exc})"
    values = dict(line.split("=", 1) for line in done.stdout.splitlines()
                  if "=" in line)
    gstreamer = values.get("gstreamer", "")
    host = values.get("bluetooth_address", "none")
    if not gstreamer.startswith("True"):
        return "cannot encode", gstreamer.split("/", 1)[-1]
    if host in ("", "none"):
        return ("no Bluetooth network",
                "the phone is not sharing its network over Bluetooth; turn on "
                "its tethering and connect to it first")
    return "ready", f"http://{host}:{values.get('port', 8090)} would serve it"


def _run(arguments: dict):
    config = arguments.get("_config")
    if config is None:
        from shani_chronoa.config import ChronoaConfig
        config = ChronoaConfig()
    allowed, refusal = _consent(config)
    if not allowed:
        return refusal

    action = (arguments.get("action") or "status").strip().lower()
    if action not in _ACTIONS:
        return (f"action must be one of {', '.join(_ACTIONS)}; "
                f"{action!r} is not a screen-share action")

    if action == "status":
        state, detail = _check_text()
        if state == "ready":
            return f"The screen can be shared: {detail}."
        return f"The screen cannot be shared right now ({state}): {detail}."

    if action == "stop":
        proc = _PROC.get("proc")
        if proc is None or proc.poll() is not None:
            return "Nothing is being shared."
        proc.terminate()
        try:
            proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            proc.kill()
        _PROC["proc"] = None
        return "Stopped. The portal session is closed and nothing is streamed."

    # action == "start"
    state, detail = _check_text()
    if state != "ready":
        return (f"Not started: {state} ({detail}). Fix that first and ask again - "
                f"the portal will not be opened.")
    proc = _PROC.get("proc")
    if proc is not None and proc.poll() is None:
        return f"Already sharing on {detail}."
    launcher = launcher_path()
    try:
        proc = subprocess.Popen([launcher], stdout=subprocess.PIPE,
                                stderr=subprocess.PIPE, text=True)
    except OSError as exc:
        return f"The launcher would not start ({exc})."
    _PROC["proc"] = proc
    return (f"Sharing the screen on {detail}. Open that address on the phone. "
            f"The portal asked you first and will ask again next time. "
            f"'stop' ends it.")


SCHEMA = {
    "type": "function",
    "function": {
        "name": "screen_to_phone",
        "description": (
            "Show this computer's screen on a paired phone, over the Bluetooth "
            "network. 'status': could this be shared here at all, and what is "
            "missing if not. 'start': ask the desktop to share the screen and "
            "serve it to the phone - the desktop shows its own 'Share screen' "
            "dialog, which only the person can answer. 'stop': end it and close "
            "the portal session. The picture goes over Bluetooth, so it is not "
            "on Wi-Fi, but it is the whole screen - which is why this needs the "
            "'screen-share-enabled' consent key."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "action": {
                    "type": "string",
                    "enum": list(_ACTIONS),
                    "description": "status: can this be shared, and what is "
                                   "missing. start: begin sharing (the desktop "
                                   "asks the person). stop: end it. Defaults to "
                                   "status.",
                    "default": "status",
                },
            },
            "required": [],
        },
    },
}

SKILLS = [Skill(name="screen_to_phone", schema=SCHEMA, run=_run)]
