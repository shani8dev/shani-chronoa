"""Chronoa and the desktop session: the global shortcut, autostart, background mode, the settings window, debug logging."""

import logging
import os
import shutil
import subprocess
import time

import gi
gi.require_version('Gtk', '4.0')
gi.require_version('Gdk', '4.0')
gi.require_version('Gio', '2.0')
gi.require_version('Adw', '1')
from gi.repository import Gtk, Gio, GLib  # type: ignore



from shani_chronoa import files

from .common import (  # noqa: F401
    _set_log_level,
)

logger = logging.getLogger(__name__)




class DesktopIntegrationMixin:
    """Chronoa and the desktop session: the global shortcut, autostart, background mode, the settings window, debug logging. - a part of ChronoaApplication, which mixes it in."""


    #: A shortcut held at least this long is push-to-talk: its release ends the request.
    HOLD_TO_TALK_SECONDS = 0.6

    def _start_global_shortcut(self, connection=None) -> None:
        """Bind the push-to-talk shortcut through the desktop portal, once, on a thread of its own.

        GlobalShortcuts is the only way a Wayland app may react to a key while
        another window has focus (GNOME and Plasma both offer it). The first
        bind shows the desktop's own dialog, so this waits on its own D-Bus
        connection and MainContext rather than the GTK loop; a press is handed
        back to the main loop as `toggle-listening`.
        """
        if getattr(self, "_shortcut_thread", None) is not None:
            return
        if not self.config.get_bool("global-shortcut-enabled", False):
            return
        trigger = self.config.get("global-shortcut") or "CTRL+ALT+space"

        pressed_at = {"t": 0.0}

        def press():
            pressed_at["t"] = time.monotonic()
            GLib.idle_add(lambda: (self.activate_action("toggle-listening", None), False)[1])

        def release():
            # Held, not tapped: releasing ends the request at once instead of
            # waiting out the trailing-silence timer (assistd's hotkey.rs push-to-
            # talk). A short tap keeps the toggle behaviour it always had.
            if time.monotonic() - pressed_at["t"] >= self.HOLD_TO_TALK_SECONDS:
                GLib.idle_add(lambda: (self._end_listening_now(), False)[1])

        def worker():
            from shani_chronoa import portal
            try:
                # `connection` is for tests: Gio caches one session-bus connection per
                # process, so a test that relied on DBUS_SESSION_BUS_ADDRESS alone could
                # reach the real desktop portal (it did, once, in a full-suite run).
                client = portal.bind_global_shortcut("talk", "Talk to Shani Chronoa", trigger, press,
                                                     client=portal.PortalClient(connection=connection, timeout=300)
                                                     if connection is not None else None,
                                                     on_deactivated=release)
            except portal.PortalError as exc:
                logger.warning("Global shortcut not bound: %s", exc)
                return
            logger.info("Global shortcut bound through the desktop portal (%s)", trigger)
            # Not MainLoop.run(): a thread parked inside a GLib loop can deadlock
            # interpreter shutdown, so it wakes twice a second to see whether the
            # app is quitting (`_stop_global_shortcut`), and the session closes
            # with its connection when it returns.
            wake = GLib.timeout_source_new(500)
            wake.set_callback(lambda *_: True)
            wake.attach(client.context)
            while not stop.is_set():
                client.context.iteration(True)
            wake.destroy()
            client.close()

        import threading
        stop = threading.Event()
        self._shortcut_stop = stop
        self._shortcut_thread = threading.Thread(target=worker, name="global-shortcut", daemon=True)
        self._shortcut_thread.start()

    def open_browser(self, _action=None, _param=None) -> None:
        """The in-app browser window, created once and reused.

        "Attach this page" hands the page to the main window as a turn, which is
        the part that makes it worth having over a separate browser: the user
        reads a page, decides it is the answer to a question they have not typed
        yet, and attaches it.
        """
        from shani_chronoa.gui.browser import BrowserUnavailable, is_available
        if not is_available():
            if self.window:
                self.window.set_status(
                    "The browser needs WebKitGTK for GTK 4 (Arch: pacman -S webkitgtk-6.0)")
            return
        try:
            self._ensure_browser_window()
        except BrowserUnavailable as exc:
            if self.window:
                self.window.set_status(str(exc))
            return
        self._browser_window.present()

    def _ensure_browser_window(self, home_url=None):
        """The browser window, built on first use. Main thread only.

        Forgotten when the person closes it: `close-request` tears the window
        and its web process down, and handing that dead window back out - to
        the menu or to the model - presented nothing.
        """
        from shani_chronoa.gui.browser import BrowserWindow
        if getattr(self, "_browser_window", None) is None:
            kwargs = {} if home_url is None else {"home_url": home_url}
            window = BrowserWindow(application=self, on_attach=self._browser_attach, **kwargs)
            window.connect("close-request", self._on_browser_closed)
            self._browser_window = window
        return self._browser_window

    def _on_browser_closed(self, _window) -> bool:
        self._browser_window = None
        return False

    def browser_for_model(self):
        """The browser window for the `browse` skill (`browser_bridge`'s provider).

        Built blank (`about:blank`, no fetch the person did not ask for) and
        presented when first needed, so the person sees the model's browsing
        happen in a window they can watch, stop or close.
        """
        fresh = getattr(self, "_browser_window", None) is None
        window = self._ensure_browser_window(home_url="about:blank")
        if fresh:
            window.present()
        return window

    def _browser_attach(self, title: str, url: str, text: str) -> None:
        """A page the user chose: it becomes the next question, with the answer
        already in hand.

        Deliberately *not* silent. It arrives as a normal turn in the transcript,
        so the conversation records where the information came from - a search
        result that appears with no trace is indistinguishable from the assistant
        knowing something.
        """
        if not text:
            return
        question = f"Summarise what this page says:\n\n{title or url}\n{url}\n\n{text[:6000]}"
        if self.window:
            self.window.set_status(f"Attached {url}")
            self.window.submit_text(question)

    def _stop_global_shortcut(self) -> None:
        stop, thread = getattr(self, "_shortcut_stop", None), getattr(self, "_shortcut_thread", None)
        if stop is not None:
            stop.set()
        if thread is not None:
            thread.join(timeout=2)

    _AUTOSTART_DIR = str(files.config_home() / "autostart")
    _AUTOSTART_DESKTOP_FILE = os.path.join(_AUTOSTART_DIR, "shani-chronoa.desktop")
    _INSTALLED_DESKTOP_FILE = "/usr/share/applications/shani-chronoa.desktop"

    _BACKGROUND_UNIT = "shani-chronoa-daemon.service"

    def _sync_background_mode(self) -> None:
        """Enable or disable the background-mode user unit to match the setting (installed, never on by default)."""
        want = self.config.get_bool("background-mode-enabled", False)
        if shutil.which("systemctl") is None:
            return
        try:
            state = subprocess.run(["systemctl", "--user", "is-enabled", self._BACKGROUND_UNIT],
                                   capture_output=True, text=True, timeout=10).stdout.strip()
        except (OSError, subprocess.TimeoutExpired):
            return
        if state in ("not-found", ""):
            return
        if want == (state == "enabled"):
            return
        try:
            subprocess.run(["systemctl", "--user", "enable" if want else "disable", "--now", self._BACKGROUND_UNIT],
                           capture_output=True, text=True, timeout=20)
            logger.info("Background mode %s", "enabled" if want else "disabled")
        except (OSError, subprocess.TimeoutExpired) as exc:
            logger.warning("Could not change background mode: %s", exc)

    def _sync_autostart(self) -> None:
        """Create or remove the XDG autostart entry to match the auto-start setting.

        Symlinks to the installed launcher .desktop file rather than
        copying it, so it stays in sync if that file's Exec/Icon ever
        changes - unless hidden-at-login wants `--hidden`, in which case a
        generated entry is written because a symlink has nowhere to put the flag.
        """
        want = self.config.auto_start
        want_hidden = self.config.start_hidden_at_login
        exists = os.path.lexists(self._AUTOSTART_DESKTOP_FILE)
        if want:
            if want_hidden:
                self._write_hidden_autostart()
                return
            if exists and not os.path.islink(self._AUTOSTART_DESKTOP_FILE):
                # A generated --hidden entry from a previous setting; the plain
                # symlink belongs instead.
                try:
                    os.remove(self._AUTOSTART_DESKTOP_FILE)
                    exists = False
                except OSError as e:
                    logger.error(f"Could not replace hidden autostart entry: {e}")
            if exists:
                return
            if not os.path.exists(self._INSTALLED_DESKTOP_FILE):
                logger.warning(
                    f"auto-start enabled but {self._INSTALLED_DESKTOP_FILE} doesn't exist "
                    "(not installed via the package?) - skipping"
                )
                return
            try:
                os.makedirs(self._AUTOSTART_DIR, exist_ok=True)
                os.symlink(self._INSTALLED_DESKTOP_FILE, self._AUTOSTART_DESKTOP_FILE)
                logger.info("Enabled autostart on login")
            except OSError as e:
                logger.error(f"Failed to enable autostart: {e}")
        elif exists:
            try:
                os.remove(self._AUTOSTART_DESKTOP_FILE)
                logger.info("Disabled autostart on login")
            except OSError as e:
                logger.error(f"Failed to disable autostart: {e}")

    def _write_hidden_autostart(self) -> None:
        """Autostart entry for `--hidden`, because the installed file cannot carry the flag."""
        try:
            os.makedirs(self._AUTOSTART_DIR, exist_ok=True)
            with open(self._AUTOSTART_DESKTOP_FILE, "w", encoding="utf-8") as fh:
                fh.write(
                    "[Desktop Entry]\n"
                    "Type=Application\n"
                    "Name=Shani Chronoa\n"
                    "Exec=shani-chronoa --hidden\n"
                    "Icon=shani-chronoa\n"
                    "Terminal=false\n"
                    "Categories=Utility;Accessibility;Office;GTK;\n"
                    "X-GNOME-Uses-Privacy-Indicator=true\n"
                    "X-GNOME-Single-Instance=true\n"
                )
            logger.info("Enabled autostart on login, hidden window")
        except OSError as e:
            logger.error(f"Failed to write hidden autostart entry: {e}")

    def _toggle_start_hidden(self, _action: Gio.SimpleAction, _param: object) -> None:
        """Toggle start-hidden-at-login and resync the XDG autostart entry immediately."""
        new_value = not self.config.start_hidden_at_login
        self.config.set("start-hidden-at-login", "true" if new_value else "false")
        self._sync_autostart()
        if self.window:
            self.window.set_status(f"Start hidden at login: {'ON' if new_value else 'OFF'}")

    def _toggle_auto_start(self, _action: Gio.SimpleAction, _param: object) -> None:
        """Toggle autostart-on-login and sync the XDG autostart entry immediately."""
        new_value = not self.config.auto_start
        self.config.set("auto-start", "true" if new_value else "false")
        self._sync_autostart()
        if self.window:
            self.window.set_status(f"Autostart: {'ON' if new_value else 'OFF'}")

    def _toggle_debug(self, _action: Gio.SimpleAction, _param: object) -> None:
        """Toggle debug logging live and persist the choice."""
        new_value = not self.config.debug_mode
        self.config.set("debug-mode", "true" if new_value else "false")
        _set_log_level(new_value)
        if self.window:
            self.window.set_status(f"Debug logging: {'ON' if new_value else 'OFF'}")

    def _open_settings(self, _action: Gio.SimpleAction, _param: object) -> None:
        """Open (or focus) the settings window."""
        from shani_chronoa.settings_window import SettingsWindow

        if self._settings_window is None:
            self._settings_window = SettingsWindow(self)
            self._settings_window.connect("close-request", self._on_settings_closed)
        self._settings_window.present()

    def _on_settings_closed(self, _window: Gtk.Window) -> bool:
        self._settings_window = None
        return False
