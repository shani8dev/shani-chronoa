"""The GTK client of Chronoa — the only process side that imports GTK.

The engine (`engine.py`, `agent_service.py`, `assistant.py`, `tools.py`,
`mcp.py`, the senses, the triggers and the stores) never imports this
package. shani-shell's QML surfaces are a second client of the same engine,
speaking to the Agent D-Bus interface instead. Keeping the two clients on
opposite sides of this boundary is what makes "starts without GTK" a fact
rather than an intention: blocking `gi.repository.Gtk`/`Adw` leaves the engine
runnable (see `tests/test_headless_engine.py`).

`gui/`, `settings_window/` and `app/` remain importable at their old paths so
the GTK app and its tests keep working unchanged. New GTK entry points should
come through here, so there is one place that owns layer-shell preloading and
the GTK start sequence.
"""

from __future__ import annotations

from typing import Any


def main() -> None:
    """Preload layer-shell, then start the GTK application.

    Importing `shani_chronoa.app` before the preload would bind libgtk4-layer-shell
    too late for the desktop companion; the launcher used to do both in this
    order, and the order is preserved here rather than in a caller.
    """
    from shani_chronoa.layer_shell_preload import preload_layer_shell

    preload_layer_shell()
    from shani_chronoa.app import main as gtk_main

    gtk_main()


def __getattr__(name: str) -> Any:
    """Lazy re-exports, so importing this boundary never imports GTK."""
    if name == "ChronoaApplication":
        from shani_chronoa.app import ChronoaApplication

        return ChronoaApplication
    if name in ("gui", "settings_window"):
        from shani_chronoa import gui, settings_window

        return gui if name == "gui" else settings_window
    raise AttributeError(name)


__all__ = ["ChronoaApplication", "gui", "main", "settings_window"]
