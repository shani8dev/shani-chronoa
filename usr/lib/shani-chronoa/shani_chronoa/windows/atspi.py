"""Windows through the accessibility bus, for what it can really do.

Measured 2026-10-08 on GNOME 50 (Wayland), both on a private headless shell and
read-only on a live desktop:

- **GTK4** frames expose `window.close`, `window.minimize` and
  `window.toggle-maximized` as AT-SPI actions, and pressing them worked - the
  compositor's own window list showed each change, and close let the app exit.
- **GTK3, Chromium/Chrome** frames expose no window actions at all.
- **Focus does not work** on any of them: `Component.grab_focus` on a GTK4 frame
  is `atspi_error (1)`; on a GTK3 frame it returns False, and on a GTK3 button
  it returns True while the window stays behind. There is no move, resize,
  fullscreen or workspace verb either.

So this backend does exactly the three verbs, only on windows that offer them,
and says so for everything else. It never passes an arbitrary action through:
a frame also lists every action of its app (`page.close`,
`win.close-other-pages`, ...) and pressing those is not window control.
"""

from __future__ import annotations

import zlib
from typing import List, Optional, Tuple

from shani_chronoa.windows import Backend, Window, WindowError

#: The only actions this backend will ever press, by verb.
ACTIONS = {"close": "window.close", "minimize": "window.minimize",
           "maximize": "window.toggle-maximized"}
_ROLES = ("frame", "dialog", "window")
_UNSUPPORTED = (
    "the accessibility bus cannot {verb} a window - on Wayland it has no verb for "
    "it (measured on GNOME: focusing through it does nothing)")


def _atspi():
    import gi
    gi.require_version("Atspi", "2.0")
    from gi.repository import Atspi
    return Atspi


def _frames():
    """(id, app_name, frame) for every top-level frame on the bus."""
    Atspi = _atspi()
    desktop = Atspi.get_desktop(0)
    for i in range(desktop.get_child_count()):
        app = desktop.get_child_at_index(i)
        if app is None:
            continue
        try:
            pid = app.get_process_id()
        except Exception:  # noqa: BLE001 - an app that went away mid-walk
            continue
        for j in range(app.get_child_count()):
            frame = app.get_child_at_index(j)
            try:
                if frame is None or frame.get_role_name() not in _ROLES:
                    continue
                title = frame.get_name() or ""
            except Exception:  # noqa: BLE001
                continue
            # pid, position and a checksum of the title: positions shift when a
            # window closes, and the checksum turns that into "list again"
            # rather than acting on the window that slid into the slot.
            yield f"a11y:{pid}:{j}:{zlib.crc32(title.encode()) & 0xffff:x}", app, frame, pid, title


def _actions(frame) -> dict:
    try:
        if "Action" not in frame.get_interfaces():
            return {}
        iface = frame.get_action_iface()
        return {iface.get_action_name(k): k for k in range(iface.get_n_actions())}
    except Exception:  # noqa: BLE001
        return {}


def available(why_not_better: str = "") -> Tuple[Optional[Backend], str]:
    try:
        _atspi()
    except (ImportError, ValueError) as exc:
        return None, f"{why_not_better}; and the accessibility library is missing ({exc})".lstrip("; ")
    return AtspiBackend(why_not_better), ""


class AtspiBackend(Backend):
    name = "the accessibility bus"

    def __init__(self, note: str = "") -> None:
        #: Why a fuller backend is not in use - added to every refusal.
        self.note = note

    def list(self) -> List[Window]:
        Atspi = _atspi()
        out = []
        for wid, app, frame, pid, title in _frames():
            try:
                states = frame.get_state_set()
                focused = states.contains(Atspi.StateType.ACTIVE)
                minimized = states.contains(Atspi.StateType.ICONIFIED)
            except Exception:  # noqa: BLE001
                focused = minimized = False
            out.append(Window(id=wid, title=title, app_id=app.get_name() or "", pid=pid,
                              focused=focused, minimized=minimized))
        return out

    def controllable(self, wid: str) -> List[str]:
        """The verbs this window offers, for a listing to show."""
        frame = self._frame(wid)
        acts = _actions(frame)
        return [verb for verb, name in ACTIONS.items() if name in acts]

    def _frame(self, wid: str):
        for fid, _app, frame, _pid, _title in _frames():
            if fid == wid:
                return frame
        raise WindowError(f"no window on the accessibility bus has id {wid}; list the windows again")

    def _press(self, wid: str, verb: str) -> None:
        frame = self._frame(wid)
        acts = _actions(frame)
        name = ACTIONS[verb]
        if name not in acts:
            raise WindowError(self._why(
                f"this application does not offer '{verb}' on the accessibility bus "
                "(GTK4 apps do; GTK3 apps and Chromium-based browsers do not)"))
        if not frame.get_action_iface().do_action(acts[name]):
            raise WindowError(f"the application refused '{verb}'")

    def _why(self, text: str) -> str:
        return f"{text}. {self.note}" if self.note else text

    def focus(self, wid):
        raise WindowError(self._why(_UNSUPPORTED.format(verb="focus")))

    def close(self, wid):
        self._press(wid, "close")

    def minimize(self, wid):
        self._press(wid, "minimize")

    def maximize(self, wid, on=True):
        # GTK only offers a toggle, and AT-SPI reports no maximized state to
        # check it against - so the caller is told it was toggled, not set.
        self._press(wid, "maximize")

    def fullscreen(self, wid, on=True):
        raise WindowError(self._why(_UNSUPPORTED.format(verb="make fullscreen")))

    def move(self, wid, x, y):
        raise WindowError(self._why(_UNSUPPORTED.format(verb="move")))

    def resize(self, wid, width, height):
        raise WindowError(self._why(_UNSUPPORTED.format(verb="resize")))

    def set_workspace(self, wid, index):
        raise WindowError(self._why(_UNSUPPORTED.format(verb="move to another workspace")))
