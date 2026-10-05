"""A tray icon, if the desktop has one to give.

From `sayri`'s indicator, which gets two things right:

- **One click is one action.** Its menu's `show` handler toggles listening and
  then dismisses the popup, so clicking the tray icon does what a person
  clicking a tray icon means instead of opening a menu they have to dismiss. A
  tray icon that opens a menu on every click is a menu with extra steps.
- **A tray that can be absent.** GTK4 removed `Gtk.StatusIcon`, so the tray needs
  `libappindicator`, which Chronoa does not depend on. So this reports the
  absence rather than pretending: `available()` asks, `build()` returns None, and
  the Desktop panel already has a row that will say "no tray support here".

**Nothing here starts or stops anything on its own.** The icon's label follows
the assistant's state, because an icon that says "Ready" while nothing can answer
is the confident-wrong-answer shape. Clicking it calls the same
`_toggle_listening` the button in the window calls, so there is one way to do this
rather than two that can disagree.
"""

from __future__ import annotations

import logging
from typing import Any, Optional

import gi

gi.require_version("Gtk", "4.0")
from gi.repository import Gio  # noqa: E402

logger = logging.getLogger(__name__)

#: What the desktop calls us. `app.shani-chronoa` rather than the bare
#: application id: an indicator wants its own well-known name, and reusing the
#: D-Bus name can collide with the running application's.
INDICATOR_ID = "app.shani-chronoa.Chronoa"

#: A free icon if the theme has one, so the tray is never a blank square. Both
#: are in the standard Adwaita set, which is what GNOME, KDE and Xfce ship.
FALLBACK_ICONS = {
    "idle": "audio-input-microphone-symbolic",
    "listening": "audio-input-microphone-symbolic",
    "queued": "content-loading-symbolic",
    "thinking": "content-loading-symbolic",
    "speaking": "audio-volume-high-symbolic",
    "error": "dialog-error-symbolic",
}


def available() -> tuple:
    """Is there a tray here at all? Returns `(usable, why)`.

    Tries Ayatana's first because that is what Arch ships as
    `libappindicator`, then the original `AppIndicator` binding. Both are looked
    up at call time, never at import, so a desktop that gains the library later is
    picked up without a restart and a missing one costs nothing at startup.
    """
    try:
        gi.require_version("AyatanaAppIndicator3", "0.1")
        __import__("gi.repository.AyatanaAppIndicator3")
        return True, "Ayatana AppIndicator"
    except (ImportError, ValueError) as first:
        try:
            gi.require_version("AppIndicator3", "0.1")
            __import__("gi.repository.AppIndicator3")
            return True, "AppIndicator"
        except (ImportError, ValueError):
            return False, (f"no AppIndicator binding here ({first}); the "
                           "libappindicator package provides one")


class Tray:
    """A status icon whose click is the action, not a menu."""

    def __init__(self, app: Any, indicator_class) -> None:
        self._app = app
        self._icon = indicator_class.new(INDICATOR_ID)
        self._icon.set_title("Chronoa")
        self._icon.set_label("")
        self._icon.set_menu(_menu(app))
        # The sayri part: activate on click, and do not leave a menu open. A
        # popup opened on every activation is a menu with extra steps, because
        # GTK hands us the activation *after* deciding the menu was wanted.
        self._icon.connect("activate", lambda *_a: _toggle(app))
        self._icon.connect("popup-menu", lambda *_a: True)   # swallow the menu

    def set_state(self, state_name: str, label: str = "") -> None:
        """Follow the assistant's state, so the icon never claims more than it is."""
        from shani_chronoa.gui import AssistantState

        icon = FALLBACK_ICONS.get(state_name, FALLBACK_ICONS["idle"])
        try:
            # The status-icon API differs between bindings; whichever one is
            # present is asked to show the icon, and a failure is logged rather
            # than raised, because a missing icon must not stop the assistant.
            self._icon.set_icon(icon)
            self._icon.set_status(AssistantState(state_name).label)
        except Exception as exc:        # noqa: BLE001 - an icon is not the assistant
            logger.debug("tray icon could not be updated: %s", exc)
        self._icon.set_label(label)

    def destroy(self) -> None:
        try:
            self._icon.set_visible(False)
        except Exception as exc:        # noqa: BLE001
            logger.debug("tray icon could not be hidden: %s", exc)


def _toggle(app: Any) -> None:
    """The window's own toggle, so there is one way to start listening."""
    activate = getattr(app, "activate_action", None)
    if activate is None:
        return
    try:
        activate("toggle-listening", None)
    except Exception as exc:            # noqa: BLE001 - a tray click must not raise
        logger.warning("tray click failed: %s", exc)


def _menu(app: Any) -> Gio.Menu:
    """A small menu for the secondary actions. The primary click never uses it."""
    menu = Gio.Menu()
    ask = Gio.MenuItem.new("Ask one question", None)
    ask.set_action_and_target_value("app.quick-ask", None)
    menu.append_item(ask)
    settings = Gio.MenuItem.new("Settings", None)
    settings.set_action_and_target_value("app.settings", None)
    menu.append_item(settings)
    quit_item = Gio.MenuItem.new("Quit", None)
    quit_item.set_action_and_target_value("app.quit", None)
    menu.append_item(quit_item)
    return menu


def build(app: Any) -> Optional[Tray]:
    """The tray, or None - and the reason on the log, not just in the return."""
    usable, why = available()
    if not usable:
        logger.info("no tray icon here: %s", why)
        return None
    try:
        if "Ayatana" in why:
            gi.require_version("AyatanaAppIndicator3", "0.1")
            from gi.repository import AyatanaAppIndicator3 as Indicator
        else:
            gi.require_version("AppIndicator3", "0.1")
            from gi.repository import AppIndicator3 as Indicator
        tray = Tray(app, Indicator)
        logger.info("tray icon created through %s", why)
        return tray
    except Exception as exc:            # noqa: BLE001 - a tray is optional
        logger.info("tray icon could not be created: %s", exc)
        return None