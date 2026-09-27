"""Skill: capture the screen to a file and report the saved path.

Capture is delegated to `screengrab.py`'s `capture_screen()`, which already
does bounded, timeout-protected X11/Wayland capture and returns real PNG
bytes. This module does not reimplement a single pixel of capture - it only
decides *where* the bytes land and how to tell the user about it, which is
the actuation this skill exists for.

The file is written under the user's own data directory (the same place
`argfile.py` and `ToolTracker` use), not the system temp dir, so a
screenshot survives a sandboxed skill run that remounts `/tmp`. The path is
reported in full because "a screenshot was taken" is useless without being
able to find it.

Refuses with a clear reason when there is no display at all - a headless
session is a normal state, not an error, and the one thing that must never
happen is a traceback where a sentence would do.
"""

import os
import shutil
import time

from shani_chronoa.skills import Skill
from shani_chronoa.screengrab import ScreenCaptureError, capture_screen

_OUTPUT_DIR = os.path.expanduser("~/.local/share/shani-chronoa/screenshots")
_PREFIX = "chronoa-screenshot-"


def _ensure_output_dir() -> str:
    try:
        os.makedirs(_OUTPUT_DIR, exist_ok=True)
        return _OUTPUT_DIR
    except OSError:
        # A read-only HOME must not make the skill fail; fall back to the
        # process temp dir, which mkdtemp makes private by default.
        import tempfile
        return tempfile.mkdtemp(prefix="chronoa-screenshot-")


def _run(_arguments: dict) -> str:
    try:
        capture = capture_screen()
    except ScreenCaptureError as e:
        return f"Could not take a screenshot: {e}"
    except Exception as e:  # noqa: BLE001 - never a traceback to the caller
        return f"Could not take a screenshot: {e}"

    directory = _ensure_output_dir()
    stamp = time.strftime("%Y%m%d-%H%M%S")
    filename = f"{stamp}-{capture.width}x{capture.height}.png"
    path = os.path.join(directory, filename)

    try:
        with open(path, "wb") as handle:
            handle.write(capture.data)
    except OSError as e:
        return f"Could not save the screenshot: {e}"

    size_kb = len(capture.data) // 1024
    scaled = (
        f", downscaled from {capture.native_width}x{capture.native_height}"
        if capture.downscaled()
        else ""
    )
    return (
        f"Screenshot saved to {path} ({size_kb} KiB, "
        f"{capture.width}x{capture.height}{scaled} via {capture.backend})."
    )


_SCHEMA = {
    "type": "function",
    "function": {
        "name": "screenshot",
        "description": (
            "Capture the screen to a PNG file and return the path it was "
            "saved to. Refuses when there is no display."
        ),
        "parameters": {"type": "object", "properties": {}},
    },
}

SKILLS = [Skill(name="screenshot", schema=_SCHEMA, run=_run)]