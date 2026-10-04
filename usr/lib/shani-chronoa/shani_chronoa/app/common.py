"""Helpers and constants the parts of this package share."""

import logging
import os

import gi
gi.require_version('Gtk', '4.0')
gi.require_version('Gdk', '4.0')
gi.require_version('Gio', '2.0')
gi.require_version('Adw', '1')





def _set_log_level(debug: bool) -> None:
    """Apply the debug-mode setting to the root logger and its handlers.

    `logging.basicConfig` pins both the root logger and its handler to the
    same level, so flipping only the logger level would still be filtered by
    the handler - set both. `SHANI_DEBUG` stays an always-on override.
    """
    level = logging.DEBUG if (debug or os.environ.get("SHANI_DEBUG")) else logging.INFO
    logging.getLogger().setLevel(level)
    for handler in logging.getLogger().handlers:
        handler.setLevel(level)
