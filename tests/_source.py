"""The source text of a Chronoa module, or of every module in it when it is a package.

Tests that check source text (a call site, a forbidden pattern) use this, so a
module becoming a package - app, settings_window, gui, triggers were split into
packages in the 2026-10-02 structure review - does not break or blind them.
"""

from pathlib import Path

PKG = (
    Path(__file__).resolve().parents[1]
    / "usr"
    / "lib"
    / "shani-chronoa"
    / "shani_chronoa"
)

# Phase 5 moved the GTK tree under ui_gtk/. Old import paths still work through
# shims, but a shim's text is not the code a source check needs to read: point
# source readers at the real modules instead of following the alias.
_MOVED_PACKAGES = ("gui", "app", "settings_window", "windows")
_MOVED_MODULES = (
    "automation_overlay",
    "away",
    "desktop_cue",
    "mascot",
    "mascot_notch",
    "setup_wizard",
    "ui_driver",
)


def source_path(relative: str) -> Path:
    """'gui/window.py' -> the real file, not the compatibility shim."""
    parts = relative.split("/")
    stem = parts[0][:-3] if parts[0].endswith(".py") else parts[0]
    if parts[0] in _MOVED_PACKAGES or (len(parts) == 1 and stem in _MOVED_MODULES):
        parts.insert(0, "ui_gtk")
    return PKG.joinpath(*parts)


def package_source(dotted: str) -> str:
    """'settings_window' or 'senses.memory' -> its text; a package -> all its .py files joined."""
    parts = dotted.split(".")
    if parts[0] in _MOVED_PACKAGES or (len(parts) == 1 and parts[0] in _MOVED_MODULES):
        parts.insert(0, "ui_gtk")
    base = PKG.joinpath(*parts)
    if base.is_dir():
        return "".join(
            f.read_text(encoding="utf-8") for f in sorted(base.rglob("*.py"))
        )
    return base.with_suffix(".py").read_text(encoding="utf-8")
