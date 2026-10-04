"""The source text of a Chronoa module, or of every module in it when it is a package.

Tests that check source text (a call site, a forbidden pattern) use this, so a
module becoming a package - app, settings_window, gui, triggers were split into
packages in the 2026-10-02 structure review - does not break or blind them.
"""

from pathlib import Path

PKG = Path(__file__).resolve().parents[1] / "usr" / "lib" / "shani-chronoa" / "shani_chronoa"


def package_source(dotted: str) -> str:
    """'settings_window' or 'senses.memory' -> its text; a package -> all its .py files joined."""
    base = PKG.joinpath(*dotted.split("."))
    if base.is_dir():
        return "".join(f.read_text(encoding="utf-8") for f in sorted(base.rglob("*.py")))
    return base.with_suffix(".py").read_text(encoding="utf-8")
