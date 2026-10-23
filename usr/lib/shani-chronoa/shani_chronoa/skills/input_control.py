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
input-method protocol) or ydotool (requires a running ydotoold daemon) -
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

# Scroll directions, named. xdotool rides the wheel buttons (4 up, 5 down, 6
# left, 7 right); the GNOME extension and the directions the model may give.
_SCROLL_DIRECTIONS = ("up", "down", "left", "right")
_SCROLL_XDO = {"up": "4", "down": "5", "left": "6", "right": "7"}


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
      `ydotool` requires a running `ydotoold` daemon. Both are opt-in and are
      only accepted when the session is actually Wayland.
    - X11 (or an unknown session with a DISPLAY): `xdotool` uses the XTEST
      extension, which is the only backend verified end-to-end on this host.

    Returns None when no backend is available, so the caller can refuse
    rather than emit a command that cannot possibly work.
    """
    session = os.environ.get("XDG_SESSION_TYPE", "").lower()
    wayland = bool(os.environ.get("WAYLAND_DISPLAY")) or os.environ.get("XDG_SESSION_TYPE", "").lower() == "wayland"
    display = os.environ.get("DISPLAY", "")

    if session == "wayland" or wayland:
        # Wayland: no generic synthetic-input permission for plain clients.
        if shutil.which("wtype"):
            return ("wtype", ["wtype"])
        if shutil.which("ydotool"):
            return ("ydotool", ["ydotool"])
        # Neither ships on the Shanios images; the desktop portal does, on GNOME and Plasma
        # alike (see shani_chronoa/portal.py) - it asks the person once in a system dialog.
        return ("portal", [])
        return None

    # X11 (or unknown session with a DISPLAY): xdotool via XTEST.
    if display and shutil.which("xdotool"):
        return ("xdotool", ["xdotool"])
    # ydotool also works on X11 if its daemon is running.
    if shutil.which("ydotool"):
        return ("ydotool", ["ydotool"])
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
        if action == "scroll":
            button = _SCROLL_XDO.get(params.get("direction"))
            if button is None:
                return None
            return prefix + ["click", "--repeat", str(max(1, int(params.get("amount", 3)))), button]
        if action == "drag":
            # One xdotool invocation chains the moves and the button, so the
            # press is held across the move - a separate mousedown then mousemove
            # would be two processes and the button would not stay down.
            return prefix + ["mousemove", str(params["sx"]), str(params["sy"]),
                             "mousedown", "1", "mousemove", str(params["ex"]), str(params["ey"]),
                             "mouseup", "1"]
    elif name == "wtype":
        # **Typing only.** wtype(1) is a virtual *keyboard*: it has no pointer
        # at all, and `-M` presses a modifier - so the old `wtype -M hello`
        # failed with "Invalid modifier name 'hello'", and `-m`/`-c` for the
        # pointer do not exist (measured 2026-10-15). `--` ends option parsing
        # so text starting with '-' is typed, not read as a flag.
        if action == "type":
            return prefix + ["--", params["text"]]
        return None
    elif name == "ydotool":
        # **All three of these were wrong, and the backend was dead with
        # them.** The module called a binary named `dotool`, which does not
        # exist - Arch's `ydotool` package (1.0.4-2) installs `ydotool` and
        # `ydotoold(8)` - so `shutil.which("dotool")` returned None on every
        # machine and this branch was unreachable code on a system that had
        # the tool installed.
        #
        # The commands were wrong too, checked against the man page rather
        # than against the name:
        #
        # | this built | ydotool(1) actually takes |
        # |------------|---------------------------|
        # | `move X Y`        | `mousemove [-a\|--absolute] X Y` |
        # | `click BUTTON`    | `click [-d ms] [-r\|--repeat N] [button...]` |
        # | `type -- TEXT`    | `type [-D ms] [-d ms] [-f file] "text"` |
        #
        # - **`--absolute` is not optional for us.** These params are screen
        #   coordinates, and without it `mousemove` moves by a *relative*
        #   delta - so a click at (400,300) would land 400,300 from
        #   wherever the pointer already was.
        # - **`click` does repeat.** The comment below this table said it did
        #   not, and built a repeat form only for xdotool; ydotool's `click`
        #   has `-r/--repeat N` in the same release.
        # - **`type` has no `--`.** The text is a positional argument, and
        #   passing `--` would type the literal string "--".
        #
        # Still unverified by execution: ydotool needs a `ydotoold` daemon
        # over `/dev/uinput`, and there is neither on this dev box.
        if action == "move":
            return prefix + ["mousemove", "--absolute",
                             str(params["x"]), str(params["y"])]
        if action == "click":
            cmd = prefix + ["click"]
            if params.get("count", 1) > 1:
                cmd += ["--repeat", str(params["count"])]
            # `0xC0` is mouse-down followed by mouse-up - a complete click.
            # Without the bit mask ydotool sends only the press.
            cmd.append(f"0xC0:{params['button']}")
            return cmd
        if action == "type":
            return prefix + ["type", params["text"]]
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

    if action == "type":
        refusal = chronoa_has_focus()
        if refusal:
            return refusal
    gnome = _gnome_input()
    if gnome is not None:
        return _run_gnome(gnome, action, params, description)

    backend = _detect_backend()
    if backend is None:
        return (
            "Input control is enabled but no input-injection backend is "
            "available on this host (need xdotool on X11, or wtype/ydotool on "
            "Wayland). Install one and ensure your session exposes it."
        )

    if backend[0] == "portal":
        return _run_portal(action, params, description)

    cmd = _build_command(backend, action, params)
    if cmd is None:
        return f"Input control: backend '{backend[0]}' cannot perform '{description}'."

    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=5)
    except FileNotFoundError:
        return "Input control: backend binary disappeared before execution."
    except Exception as e:  # noqa: BLE001 - surface any failure as a message
        return f"Input control failed: {e}"

    if result.returncode != 0:
        return f"Input control failed: {result.stderr.strip() or result.stdout.strip()}"
    return f"{description.capitalize()} done."


#: How long the pasted text stays on the clipboard before yours is put back:
#: long enough for the app to read it on Ctrl+V.
PASTE_RESTORE_SECONDS = 1.0


def paste_text(text: str, press_paste) -> bool:
    """Put `text` on the clipboard, press paste, then put the clipboard back.

    **The last resort, after keys and IBus**, as Handy does for every
    dictation: typing reaches only what the layout produces, and with no IBus
    a Devanagari word has no other way in. The person's clipboard is read
    first and restored after `PASTE_RESTORE_SECONDS`; a clipboard that cannot
    be read (an image, nothing) is cleared rather than left holding our text.
    """
    import shutil
    import subprocess
    import time
    wayland = bool(os.environ.get("WAYLAND_DISPLAY"))
    if wayland and shutil.which("wl-copy") and shutil.which("wl-paste"):
        read, write, clear = ["wl-paste", "--no-newline"], ["wl-copy"], ["wl-copy", "--clear"]
    elif shutil.which("xclip"):
        read = ["xclip", "-selection", "clipboard", "-o"]
        write = ["xclip", "-selection", "clipboard"]
        clear = None
    else:
        return False
    try:
        before = subprocess.run(read, capture_output=True, timeout=3)
        saved = before.stdout if before.returncode == 0 else None
        subprocess.run(write, input=text.encode("utf-8"), timeout=3, check=True)
        time.sleep(0.15)
        press_paste()
        time.sleep(PASTE_RESTORE_SECONDS)
        if saved is not None:
            subprocess.run(write, input=saved, timeout=3)
        elif clear:
            subprocess.run(clear, timeout=3)
        return True
    except Exception:  # noqa: BLE001 - a paste that could not happen is said by the caller
        return False


CHRONOA_APP_ID = "dev.shani.chronoa"


def chronoa_has_focus() -> str:
    """A refusal when Chronoa's own window holds focus; "" otherwise.

    **Its own message box is never the target.** Measured 2026-10-15: asked to
    write a note in Text Editor, the model typed while the editor had not yet
    appeared, and both sentences landed in Chronoa's composer - and a Return
    there would have sent them as a message. Read from the window list; when
    no list can be had, nothing is refused on this account.
    """
    try:
        from shani_chronoa.windows import detect
        backend, _why = detect()
        if backend is None:
            return ""
        focused = next((w for w in backend.list() if w.focused), None)
    except Exception:  # noqa: BLE001 - an unreadable list refuses nothing
        return ""
    if focused is not None and (focused.app_id or "").lower() == CHRONOA_APP_ID:
        return ("Nothing was typed: Chronoa's own window has focus, so it would have gone "
                "into its message box. Focus the target window first (open_application "
                "waits for it, or use focus_window).")
    return ""


#: App-id fragments of the terminals a paste must reach with Ctrl+Shift+V, not
#: Ctrl+V. **This is the terminal-aware paste from AIYantra's "Extra" desktop
#: engine.** A terminal reads Ctrl+V as a literal control character (or nothing),
#: so a clipboard paste that works everywhere else silently fails there - a
#: Devanagari or emoji word, which only the paste path can enter, never arrives.
#: Matched as a lowercase substring of the focused window's app id, so
#: `org.gnome.Terminal`, `org.gnome.Console`, `com.gnome.Ptyxis` and the rest hit.
_TERMINAL_APP_IDS = frozenset({
    "terminal", "console", "konsole", "xterm", "alacritty", "kitty", "foot",
    "wezterm", "ptyxis", "tilix", "terminator", "rxvt", "st-", "contour",
    "blackbox", "guake", "yakuake", "cool-retro-term", "qterminal", "sakura",
})


def _focused_is_terminal() -> bool:
    """Whether the window that would receive a paste is a terminal emulator.

    Read from the window list, like `chronoa_has_focus`; an unreadable list is
    not a terminal (Ctrl+V, the safe default for every other app).
    """
    try:
        from shani_chronoa.windows import detect
        backend, _why = detect()
        if backend is None:
            return False
        focused = next((w for w in backend.list() if w.focused), None)
    except Exception:  # noqa: BLE001 - an unreadable list is "not a terminal"
        return False
    app_id = (getattr(focused, "app_id", "") or "").lower()
    return any(fragment in app_id for fragment in _TERMINAL_APP_IDS)


def _paste_combo() -> str:
    """The key combo that pastes into the focused window - terminals need Shift."""
    return "ctrl+shift+v" if _focused_is_terminal() else "ctrl+v"


def _gnome_input():
    """Chronoa's GNOME Shell extension, when it can send input (see windows.gnome)."""
    try:
        from shani_chronoa.windows import gnome
        return gnome.input_backend()
    except Exception:  # noqa: BLE001 - no extension is "use another backend"
        return None


#: xdotool's numbers back to the names the extension takes.
_BUTTON_NAMES = {"1": "left", "2": "middle", "3": "right"}


def _run_gnome(gnome, action: str, params: dict, description: str) -> str:
    from shani_chronoa.windows import WindowError
    try:
        if action == "move":
            gnome.pointer_move(params["x"], params["y"])
        elif action == "click":
            button = str(params["button"])
            gnome.pointer_click(_BUTTON_NAMES.get(button, button), int(params.get("count", 1)))
        elif action == "scroll":
            gnome.pointer_scroll(str(params["direction"]), int(params.get("amount", 3)))
        elif action == "drag":
            gnome.pointer_drag(int(params["sx"]), int(params["sy"]),
                               int(params["ex"]), int(params["ey"]))
        elif action == "type":
            text = params["text"]
            # Keys reach only what the layout can produce. Anything beyond
            # Latin-1 (Devanagari, CJK, emoji) is committed through Chronoa's
            # IBus engine instead, as dictation tools do (ibus_input.py).
            if any(ord(c) > 0xff for c in text):
                from shani_chronoa import ibus_input
                if ibus_input.type_via_ibus(text):
                    return f"{description.capitalize()} done (through Chronoa's IBus input)."
                combo = _paste_combo()
                if paste_text(text, lambda: gnome.key_press(combo, 1)):
                    where = " into the terminal with Ctrl+Shift+V" if combo == "ctrl+shift+v" else ""
                    return (f"{description.capitalize()} done (pasted{where} - Chronoa's IBus input "
                            f"is not running; your clipboard was put back).")
                return ("Input control: that text has characters no key can type, "
                        "Chronoa's IBus input is not running and the clipboard could not "
                        "be used, so it was not typed.")
            gnome.type_text(text)
        else:
            return f"Input control: the GNOME extension cannot perform '{description}'."
    except WindowError as exc:
        return f"Input control through Chronoa's GNOME Shell extension failed: {exc}"
    return f"{description.capitalize()} done (through Chronoa's GNOME Shell extension)."


def _run_portal(action: str, params: dict, description: str) -> str:
    """Input through xdg-desktop-portal's RemoteDesktop (Wayland, GNOME and Plasma)."""
    from shani_chronoa import portal

    if action == "move":
        return ("Moving the pointer to an absolute position is not possible through the desktop portal "
                "without also sharing the screen with Chronoa, so it was not done. Typing and clicking work.")
    names = {"1": "left", "2": "middle", "3": "right"}
    try:
        with portal.RemoteInput(portal.KEYBOARD if action == "type" else portal.POINTER) as ri:
            if action == "type":
                ri.type_text(params["text"])
            elif action == "click":
                button = names.get(str(params["button"]))
                if button is None:
                    return "The portal can click the left, middle or right button only."
                ri.click(button, int(params.get("count", 1)))
            else:
                return f"Input control: the portal cannot perform '{description}'."
    except (portal.PortalError, ValueError) as exc:
        return f"Input control through the desktop portal failed: {exc}."
    return f"{description.capitalize()} done (through the desktop portal)."


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
    # Only `wtype -c` lacks a repeat flag, so the count is passed to the
    # backends that have one and dropped for the one that does not. (The old
    # comment here said ydotool could not repeat either; `ydotool click -r N`
    # can, per its own man page - the module was calling a binary that did
    # not exist, so nothing had ever checked.)
    return _run_action("click", params, description=f"clicked {button}")


def _run_type_text(arguments: dict) -> str:
    text = arguments.get("text")
    if not isinstance(text, str) or not text:
        return "Invalid text: expected a non-empty string."
    return _run_action("type", {"text": text}, description="text typed")


def _run_scroll(arguments: dict) -> str:
    direction = arguments.get("direction")
    if not isinstance(direction, str) or direction.strip().lower() not in _SCROLL_DIRECTIONS:
        return "Invalid direction: expected 'up', 'down', 'left' or 'right'."
    direction = direction.strip().lower()
    amount = arguments.get("amount", 3)
    if isinstance(amount, bool) or not isinstance(amount, int):
        return "Invalid amount: expected an integer number of wheel steps."
    amount = max(1, min(amount, 100))  # a bounded number of notches, never a runaway
    return _run_action("scroll", {"direction": direction, "amount": amount},
                       description=f"scrolled {direction} {amount}")


def _run_drag(arguments: dict) -> str:
    coords = {}
    for key in ("sx", "sy", "ex", "ey"):
        value = arguments.get(key)
        if isinstance(value, bool) or not isinstance(value, int):
            return f"Invalid drag coordinate: '{key}' must be an integer pixel position."
        coords[key] = value
    return _run_action("drag", coords,
                       description=f"dragged from {coords['sx']},{coords['sy']} to {coords['ex']},{coords['ey']}")


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
    Skill(
        name="scroll",
        schema={
            "type": "function",
            "function": {
                "name": "scroll",
                "description": (
                    "Scroll the window under the pointer up, down, left or right by a number of "
                    "wheel steps (move the pointer over it first). Requires the "
                    "'input-control-enabled' consent key (separate from the vision sense)."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "direction": {
                            "type": "string",
                            "enum": list(_SCROLL_DIRECTIONS),
                            "description": "Which way to scroll: up, down, left or right.",
                        },
                        "amount": {
                            "type": "integer",
                            "description": "How many wheel steps (default 3).",
                        },
                    },
                    "required": ["direction"],
                },
            },
        },
        run=_run_scroll,
    ),
    Skill(
        name="drag",
        schema={
            "type": "function",
            "function": {
                "name": "drag",
                "description": (
                    "Drag with the left button held from one pixel position to another - to select "
                    "text, move an item, or resize by its edge. Requires the 'input-control-enabled' "
                    "consent key (separate from the vision sense)."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "sx": {"type": "integer", "description": "Start horizontal pixel."},
                        "sy": {"type": "integer", "description": "Start vertical pixel."},
                        "ex": {"type": "integer", "description": "End horizontal pixel."},
                        "ey": {"type": "integer", "description": "End vertical pixel."},
                    },
                    "required": ["sx", "sy", "ex", "ey"],
                },
            },
        },
        run=_run_drag,
    ),
]


#: Reads the focused editable field's text through AT-SPI, in a child process:
#: `Atspi.init()` in a process with no accessibility bus aborts it outright
#: (measured - it took a whole test run down), and the app must never.
_READ_FOCUSED = r'''
import gi
gi.require_version("Atspi", "2.0")
from gi.repository import Atspi
Atspi.init()
def walk(node, depth=0):
    if node is None or depth > 40:
        return None
    try:
        states = node.get_state_set()
        if states.contains(Atspi.StateType.FOCUSED) and states.contains(Atspi.StateType.EDITABLE):
            return node
        for i in range(min(node.get_child_count(), 200)):
            found = walk(node.get_child_at_index(i), depth + 1)
            if found is not None:
                return found
    except Exception:
        return None
    return None
desktop = Atspi.get_desktop(0)
for i in range(desktop.get_child_count()):
    hit = walk(desktop.get_child_at_index(i))
    if hit is not None:
        print("TEXT" + Atspi.Text.get_text(hit, 0, -1))
        break
'''


def _focused_text():
    """What the focused editable field holds, or None when it cannot be read."""
    import sys
    try:
        done = subprocess.run([sys.executable, "-c", _READ_FOCUSED], capture_output=True,
                              text=True, timeout=8)
    except Exception:  # noqa: BLE001 - unreadable is "cannot check", not "failed"
        return None
    out = done.stdout
    return out[out.index("TEXT") + 4:].rstrip("\n") if "TEXT" in out else None


def _post_condition(arguments: dict):
    """**What actually happened on screen**, read back - not the skill's word.

    Typing: the focused field must now contain the text (measured 2026-10-15:
    a demo model was told "Text typed done" while the text had gone into
    Chronoa's own composer, and it reported a note it never wrote). Moving the
    pointer: where it is now. A click: nothing a generic check can confirm.
    """
    if "text" in arguments:
        seen = _focused_text()
        if seen is None:
            return None
        wanted = str(arguments.get("text") or "")
        tail = seen[-80:]
        return (wanted in seen, f"the focused field now reads {tail!r}")
    if "x" in arguments and "y" in arguments:
        try:
            from shani_chronoa.windows import gnome
            backend = gnome.input_backend()
            if backend is None:
                return None
            x, y = backend._call("PointerPosition")
        except Exception:  # noqa: BLE001
            return None
        ok = abs(x - int(arguments["x"])) <= 2 and abs(y - int(arguments["y"])) <= 2
        return (ok, f"the pointer is at {x},{y}")
    return None


POST_CONDITION = _post_condition
