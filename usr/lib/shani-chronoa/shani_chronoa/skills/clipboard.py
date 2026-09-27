"""Skills: read and write the system clipboard.

Two backends are probed, honestly distinguished by display server:

- **Wayland**: `wl-copy` (write) and `wl-paste` (read), the wlroots
  clipboard protocol. A plain client has no other way to reach the
  clipboard on Wayland, so these are the only accepted tools here.
- **X11**: `xclip` and `xsel`. On this host `DISPLAY` is set, so this is
  the exercisable path.

The probe is by `shutil.which`, not by guessing from `XDG_SESSION_TYPE`
alone: a session can report one type while still having the other toolset
installed, and reporting "Wayland clipboard" when `wl-copy` is absent would
be a false positive. When no backend is available the skill refuses with a
message naming what was looked for, never a traceback.

Both directions are schema-typed actions - there is no free-form command
string anywhere in this module, matching the rest of the skill set.
"""

import shutil
import subprocess
from typing import Optional, Tuple

from shani_chronoa.skills import Skill


def _session_is_wayland() -> bool:
    import os
    return bool(os.environ.get("WAYLAND_DISPLAY", "").strip()) or (
        os.environ.get("XDG_SESSION_TYPE", "").lower() == "wayland"
    )


def _detect_backend() -> Optional[Tuple[str, str, str]]:
    """Return (session, write_tool, read_tool) for the clipboard, or None.

    The session label is part of the return so the result string can say
    which display server was actually used, rather than claiming "the
    clipboard" without qualification.
    """
    if _session_is_wayland():
        write = shutil.which("wl-copy")
        read = shutil.which("wl-paste")
        if write and read:
            return ("wayland", write, read)
        return None
    # X11, or an unknown session that still has a DISPLAY to talk to.
    for write, read in (
        (shutil.which("xclip"), shutil.which("xclip")),
        (shutil.which("xsel"), shutil.which("xsel")),
    ):
        if write and read:
            return ("x11", write, read)
    return None


def _run_get_clipboard(_arguments: dict) -> str:
    backend = _detect_backend()
    if backend is None:
        return (
            "Could not read the clipboard: no clipboard tool is installed "
            "for this session (looked for wl-paste on Wayland, xclip/xsel "
            "on X11)."
        )
    session, _write, read_tool = backend
    try:
        result = subprocess.run(
            [read_tool, "-selection", "clipboard", "-o"],
            capture_output=True, text=True, timeout=5,
        )
    except FileNotFoundError:
        return f"Could not read the clipboard: {read_tool} disappeared."
    except Exception as e:  # noqa: BLE001 - surface any failure as a message
        return f"Could not read the clipboard: {e}"
    if result.returncode != 0:
        return "The clipboard is empty."
    return result.stdout


def _run_set_clipboard(arguments: dict) -> str:
    text = arguments.get("text")
    if not isinstance(text, str):
        return "Invalid text: expected a non-empty string."
    backend = _detect_backend()
    if backend is None:
        return (
            "Could not write the clipboard: no clipboard tool is installed "
            "for this session (looked for wl-copy on Wayland, xclip/xsel "
            "on X11)."
        )
    session, write_tool, _read = backend
    try:
        # stdout/stderr go to DEVNULL, never to a pipe. Both xclip and xsel fork a
        # background daemon that stays alive to own the selection, and that daemon
        # inherits whatever the parent gave it. With capture_output=True the
        # daemon holds the write end of the pipe open, so reading it never sees
        # EOF and this call dies on its own timeout - reporting "Could not write
        # the clipboard" immediately after the clipboard was in fact written.
        # Verified live: the write reported failure and the following read-back
        # returned the value. DEVNULL has no reader to block, so the parent sees
        # the real exit status as soon as the direct child is gone.
        result = subprocess.run(
            [write_tool, "-selection", "clipboard"],
            input=text.encode("utf-8"),
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=5,
        )
    except FileNotFoundError:
        return f"Could not write the clipboard: {write_tool} disappeared."
    except subprocess.TimeoutExpired:
        return (
            f"Could not write the clipboard: {write_tool} did not exit within 5s. "
            "The selection may still have been written - check it before retrying."
        )
    except Exception as e:  # noqa: BLE001 - surface any failure as a message
        return f"Could not write the clipboard: {e}"
    if result.returncode != 0:
        return (
            f"Could not write the clipboard: {write_tool} exited with "
            f"{result.returncode}."
        )
    return f"Copied to the clipboard ({session})."


SCHEMAS = {
    "get_clipboard": {
        "type": "function",
        "function": {
            "name": "get_clipboard",
            "description": "Read the current contents of the system clipboard.",
            "parameters": {"type": "object", "properties": {}},
        },
    },
    "set_clipboard": {
        "type": "function",
        "function": {
            "name": "set_clipboard",
            "description": "Write text to the system clipboard.",
            "parameters": {
                "type": "object",
                "properties": {
                    "text": {"type": "string", "description": "The text to copy."},
                },
                "required": ["text"],
            },
        },
    },
}


def _verify_clipboard_written(arguments: dict):
    """Read the clipboard back and compare it with what we were asked to write.

    `wl-copy`/`xclip` exiting 0 only means the request was accepted by the
    display server; it does not mean the selection now holds that text. This
    is the round trip the skill's own result string cannot perform, and it is
    the only evidence that the effect happened.

    Returns `(ok, evidence)`.
    """
    expected = str(arguments.get("text", ""))
    backend = _detect_backend()
    if backend is None:
        return False, "no clipboard backend available to verify with"
    _session, _write_tool, read_tool = backend
    try:
        completed = subprocess.run(
            [read_tool, "-selection", "clipboard", "-o"],
            capture_output=True, text=True, timeout=5, check=False,
        )
    except (subprocess.SubprocessError, OSError) as exc:
        return False, f"could not read the clipboard back: {exc}"
    if completed.returncode != 0:
        return False, (completed.stderr or "read-back failed").strip()
    actual = completed.stdout
    if actual == expected:
        return True, "clipboard read-back matches"
    return False, f"clipboard holds {actual[:40]!r}, expected {expected[:40]!r}"


# Declared for `verification.verify`; the LLM never supplies this.
POST_CONDITION = _verify_clipboard_written

SKILLS = [
    Skill(name="get_clipboard", schema=SCHEMAS["get_clipboard"], run=_run_get_clipboard),
    Skill(name="set_clipboard", schema=SCHEMAS["set_clipboard"], run=_run_set_clipboard),
]