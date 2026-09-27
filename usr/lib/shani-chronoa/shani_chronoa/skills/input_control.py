"""Skills: synthetic input injection (move pointer, click, type text).

These are deliberately narrow, schema-typed actuators - there is no free-form
command string anywhere in this module. Each action is a fixed function with
typed parameters, exactly like the rest of the skill set.

Consent gate (the point of this module): every actuation is refused unless
the `input-control-enabled` key is true, checked BEFORE any input device is
touched. This key is the input-control capability's OWN dedicated consent
key - deliberately separate from the vision sense's `vision-sense-enabled`,
because seeing a screen and controlling it are different risks. Combined
with vision, unrestricted input control would let any prompt injection in a
web page or a file become a path to controlling the machine; this gate is
what prevents that.

The key is read with `config.get_bool("input-control-enabled", False)`,
which is fail-closed: the key is not yet declared in the gschema (a parent
task adds it in a separate serialized step), so on any schema that does not
declare it `get_bool` returns the supplied default of False and the skill
refuses. Do NOT add the key here or edit the gschema - that is owned
elsewhere.

Backend detection is honest about the display server. Wayland does NOT
permit synthetic input from a plain client without compositor cooperation,
so on Wayland we only accept wtype (requires a compositor speaking the
input-method protocol) or dotool (requires a running dotoold daemon) -
both opt-in. On X11 we use xdotool's XTEST extension, which is the only
backend this host can actually prove end-to-end. If no backend is available
the skill refuses rather than guessing.

All actuation goes through `tools.py`'s dispatch path (the skill is a normal
registered Skill), so `ToolTracker` records every call in the existing
per-user audit log - no second logging system is introduced here.
"""

import os
import shutil
import subprocess
from typing import Optional

from shani_chronoa.config import ChronoaConfig
from shani_chronoa.skills import Skill

# The single dedicated consent key for input control. Separate from
# vision-sense-enabled: seeing and controlling are different risks.
_CONSENT_KEY = "input-control-enabled"

# xdotool button numbers for the named buttons.
_BUTTON_NUMBERS = {"left": "1", "middle": "2", "right": "3"}


def _consent(config: ChronoaConfig) -> tuple[bool, str]:
    """Return (allowed, reason). Fail-closed: an undeclared key => False."""
    if not config.get_bool(_CONSENT_KEY, False):
        return False, (
            f"input control is turned off (enable '{_CONSENT_KEY}'); "
            "this is a separate consent gate from the vision sense"
        )
    return True, ""


def _detect_backend() -> Optional[tuple[str, list[str]]]:
    """Return (name, argv_prefix) for an available input-injection backend.

    Honest about the display server:

    - Wayland: a plain client has no generic synthetic-input permission.
      `wtype` requires a compositor that speaks the input-method protocol;
      `dotool` requires a running `dotoold` daemon. Both are opt-in and are
      only accepted when the session is actually Wayland.
    - X11 (or an unknown session with a DISPLAY): `xdotool` uses the XTEST
      extension, which is the only backend verified end-to-end on this host.

    Returns None when no backend is available, so the caller can refuse
    rather than emit a command that cannot possibly work.
    """
    session = os.environ.get("XDG_SESSION_TYPE", "").lower()
    wayland = bool(os.environ.get("WAYLAND_DISPLAY"))
    display = os.environ.get("DISPLAY", "")

    if session == "wayland" or wayland:
        # Wayland: no generic synthetic-input permission for plain clients.
        if shutil.which("wtype"):
            return ("wtype", ["wtype"])
        if shutil.which("dotool"):
            return ("dotool", ["dotool"])
        return None

    # X11 (or unknown session with a DISPLAY): xdotool via XTEST.
    if display and shutil.which("xdotool"):
        return ("xdotool", ["xdotool"])
    # dotool also works on X11 if its daemon is running.
    if shutil.which("dotool"):
        return ("dotool", ["dotool"])
    return None


def _build_command(backend: tuple[str, list[str]], action: str, params: dict) -> Optional[list[str]]:
    """Build the argv list for `action` under `backend`.

    Returns None if the backend cannot express the action. The argv is always
    a list (never a shell string), so the LLM-issued text argument is passed
    as a single argv element with no shell interpolation - no injection
    surface.
    """
    name, prefix = backend
    if name == "xdotool":
        if action == "move":
            return prefix + ["mousemove", str(params["x"]), str(params["y"])]
        if action == "click":
            cmd = prefix + ["click"]
            if params.get("count", 1) > 1:
                cmd += ["--repeat", str(params["count"])]
            cmd.append(params["button"])
            return cmd
        if action == "type":
            # `--` ends option parsing so a leading `-` in the text is literal.
            return prefix + ["type", "--clearmodifiers", "--", params["text"]]
    elif name == "wtype":
        # Syntax per wtype(1); unverified on this host (no Wayland compositor).
        if action == "move":
            return prefix + ["-m", str(params["x"]), str(params["y"])]
        if action == "click":
            return prefix + ["-c", params["button"]]
        if action == "type":
            return prefix + ["-M", params["text"]]
    elif name == "dotool":
        # Syntax per dotool(1); unverified on this host (no dotoold daemon).
        if action == "move":
            return prefix + ["move", str(params["x"]), str(params["y"])]
        if action == "click":
            return prefix + ["click", params["button"]]
        if action == "type":
            return prefix + ["type", "--", params["text"]]
    return None


def _run_action(action: str, params: dict, *, description: str) -> str:
    """Shared gate + dispatch for the input-control skills.

    Consent is checked first, before any input device is touched. Then the
    backend is detected and the typed command is built and run. A missing
    backend or a failed command produces a clear user-facing message, never
    a traceback.
    """
    config = ChronoaConfig()
    allowed, reason = _consent(config)
    if not allowed:
        return f"Input control is not permitted: {reason}."

    backend = _detect_backend()
    if backend is None:
        return (
            "Input control is enabled but no input-injection backend is "
            "available on this host (need xdotool on X11, or wtype/dotool on "
            "Wayland). Install one and ensure your session exposes it."
        )

    cmd = _build_command(backend, action, params)
    if cmd is None:
        return f"Input control: backend '{backend[0]}' cannot perform '{description}'."

    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=5)
    except FileNotFoundError:
        return f"Input control: backend binary disappeared before execution."
    except Exception as e:  # noqa: BLE001 - surface any failure as a message
        return f"Input control failed: {e}"

    if result.returncode != 0:
        return f"Input control failed: {result.stderr.strip() or result.stdout.strip()}"
    return f"{description.capitalize()} done."


def _run_move_pointer(arguments: dict) -> str:
    x = arguments.get("x")
    y = arguments.get("y")
    if isinstance(x, bool) or not isinstance(x, int):
        return "Invalid pointer coordinates: 'x' must be an integer."
    if isinstance(y, bool) or not isinstance(y, int):
        return "Invalid pointer coordinates: 'y' must be an integer."
    return _run_action("move", {"x": x, "y": y}, description="pointer moved")


def _run_click_pointer(arguments: dict) -> str:
    button = arguments.get("button")
    if not isinstance(button, str):
        return "Invalid button: expected 'left', 'middle', 'right', or an integer 1-5."
    button = button.strip().lower()
    if button in _BUTTON_NUMBERS:
        button = _BUTTON_NUMBERS[button]
    elif button.isdigit() and 1 <= int(button) <= 5:
        pass  # already a numeric string like "1"
    else:
        return "Invalid button: expected 'left', 'middle', 'right', or an integer 1-5."
    count = arguments.get("count", 1)
    if isinstance(count, bool) or not isinstance(count, int):
        return "Invalid count: expected an integer."
    count = max(1, count)
    params = {"button": button}
    if count > 1:
        params["count"] = count
    # xdotool click --repeat N B ; wtype/dotool click does not repeat, so we
    # build the repeat form only for xdotool.
    return _run_action("click", params, description=f"clicked {button}")


def _run_type_text(arguments: dict) -> str:
    text = arguments.get("text")
    if not isinstance(text, str) or not text:
        return "Invalid text: expected a non-empty string."
    return _run_action("type", {"text": text}, description="text typed")


SKILLS = [
    Skill(
        name="move_pointer",
        schema={
            "type": "function",
            "function": {
                "name": "move_pointer",
                "description": (
                    "Move the pointer to absolute screen coordinates. "
                    "Requires the 'input-control-enabled' consent key "
                    "(separate from the vision sense) to be enabled."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "x": {"type": "integer", "description": "Horizontal pixel coordinate."},
                        "y": {"type": "integer", "description": "Vertical pixel coordinate."},
                    },
                    "required": ["x", "y"],
                },
            },
        },
        run=_run_move_pointer,
    ),
    Skill(
        name="click_pointer",
        schema={
            "type": "function",
            "function": {
                "name": "click_pointer",
                "description": (
                    "Click a mouse button at the current pointer position. "
                    "Requires the 'input-control-enabled' consent key "
                    "(separate from the vision sense) to be enabled."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "button": {
                            "type": "string",
                            "enum": ["left", "middle", "right", "1", "2", "3", "4", "5"],
                            "description": "Button to click: 'left', 'middle', 'right', or an integer 1-5.",
                        },
                        "count": {
                            "type": "integer",
                            "description": "Number of clicks (default 1).",
                        },
                    },
                    "required": ["button"],
                },
            },
        },
        run=_run_click_pointer,
    ),
    Skill(
        name="type_text",
        schema={
            "type": "function",
            "function": {
                "name": "type_text",
                "description": (
                    "Type text at the currently focused window. "
                    "Requires the 'input-control-enabled' consent key "
                    "(separate from the vision sense) to be enabled."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "text": {"type": "string", "description": "The text to type."},
                    },
                    "required": ["text"],
                },
            },
        },
        run=_run_type_text,
    ),
]
