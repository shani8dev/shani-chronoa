"""Skill: arrange a window - minimize, maximize, fullscreen, move, resize, workspace.

The rest of the window API (`shani_chronoa.windows`) beyond focusing and
closing. None of these destroys anything - the window and its contents are
where they were, only placed differently - so, like `focus_window`, it is not
behind a consent key; `close_window` is.

Each desktop does what it really can, and says so for the rest: KWin and the
GNOME extension do everything; the accessibility bus (GNOME without the
extension) minimizes and toggles maximize on GTK4 apps and nothing else; X11
needs a recent xdotool or wmctrl for maximize and fullscreen.
"""

from __future__ import annotations

from shani_chronoa.skills import Skill
from shani_chronoa.skills.list_windows import act, session_problem

_ACTIONS = ("minimize", "maximize", "unmaximize", "fullscreen", "unfullscreen",
            "move", "resize", "set_workspace")

SCHEMA = {
    "type": "function",
    "function": {
        "name": "arrange_window",
        "description": (
            "Minimize, maximize, unmaximize, make fullscreen, leave fullscreen, "
            "move, resize, or send a window to another workspace, by window id "
            "from list_windows or by part of its title. move takes x and y, "
            "resize takes width and height (pixels), set_workspace takes a "
            "0-based workspace. Nothing is closed; close_window does that."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "action": {"type": "string", "enum": list(_ACTIONS), "description": "What to do."},
                "window_id": {"type": "string", "description": "The window id from list_windows."},
                "title_contains": {"type": "string", "description": "Match a window whose title contains this."},
                "x": {"type": "integer"}, "y": {"type": "integer"},
                "width": {"type": "integer"}, "height": {"type": "integer"},
                "workspace": {"type": "integer", "description": "0-based workspace number."},
            },
            "required": ["action"],
        },
    },
}


def _ints(arguments: dict, *names: str):
    out = []
    for name in names:
        value = arguments.get(name)
        if isinstance(value, bool) or not isinstance(value, (int, float)) or value != int(value):
            raise ValueError(f"{name} must be a whole number")
        out.append(int(value))
    return out


def _run(arguments: dict) -> str:
    arguments = arguments if isinstance(arguments, dict) else {}
    action = str(arguments.get("action") or "").strip().lower()
    if action not in _ACTIONS:
        return f"action must be one of {', '.join(_ACTIONS)}, not {action!r}."
    try:
        if action == "move":
            x, y = _ints(arguments, "x", "y")
            call, done = (lambda b, w: b.move(w.id, x, y)), f"Moved {{label}} to {x},{y}"
        elif action == "resize":
            width, height = _ints(arguments, "width", "height")
            if width < 1 or height < 1:
                return "width and height must be positive."
            call, done = (lambda b, w: b.resize(w.id, width, height)), f"Resized {{label}} to {width}x{height}"
        elif action == "set_workspace":
            (index,) = _ints(arguments, "workspace")
            call, done = (lambda b, w: b.set_workspace(w.id, index)), f"Moved {{label}} to workspace {index}"
        elif action == "minimize":
            call, done = (lambda b, w: b.minimize(w.id)), "Minimized {label}"
        elif action in ("maximize", "unmaximize"):
            on = action == "maximize"
            call, done = (lambda b, w: b.maximize(w.id, on)), f"{action.capitalize()}d {{label}}"
        else:
            on = action == "fullscreen"
            call, done = ((lambda b, w: b.fullscreen(w.id, on)),
                          "Made {label} fullscreen" if on else "Took {label} out of fullscreen")
    except ValueError as exc:
        return f"Could not {action.replace('_', ' ')}: {exc}."

    def run(backend, window):
        call(backend, window)
        from shani_chronoa.windows.atspi import AtspiBackend
        if action in ("maximize", "unmaximize") and isinstance(backend, AtspiBackend):
            # GTK offers only a toggle, and the bus reports no maximized state.
            return "Toggled maximize on {label} through {backend} (GTK offers a toggle, so check how it looks)."
        return done + " through {backend}."

    if not session_problem():
        # X11 with xdotool: the same API, through its X11 backend.
        from shani_chronoa import windows
        from shani_chronoa.windows.x11 import X11Backend
        backend = X11Backend()
        try:
            target = windows.find(backend.list(), window_id=str(arguments.get("window_id") or "").strip(),
                                  title_contains=str(arguments.get("title_contains") or ""))
            said = run(backend, target)
        except windows.WindowError as exc:
            return f"Could not {action.replace('_', ' ')}: {exc}"
        return said.format(label=f"window {target.id}" + (f" ({target.title[:60]})" if target.title else ""),
                           backend=backend.name)
    return act(action.replace("_", " ") + " a window", arguments, run)


SKILLS = [Skill(name="arrange_window", schema=SCHEMA, run=_run)]
