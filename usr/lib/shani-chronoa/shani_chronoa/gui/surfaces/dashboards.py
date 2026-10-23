"""The dashboards Chronoa has built - a pane to see and open them.

**Why a panel and not just the skill.** `dashboard` writes a self-contained HTML
page, and until this the only way back to one was to remember where it was saved.
harness-studio keeps dashboards together in a pane; this is that - it lists the
folder `dashboard.default_dir()` fills (the one the skill uses when no path is
given) and opens each in Chronoa's own browser, so a page of charts is one click
away rather than a path to recall.

A dashboard the person saved somewhere of their own is theirs to manage and does
not show here; this lists the shared folder, which is the set the skill grows.
"""

from __future__ import annotations

import time
from pathlib import Path
from typing import Any

from gi.repository import Gdk, Gtk

from shani_chronoa import dashboard
from shani_chronoa.gui.surfaces import common

TITLE = "Dashboards"
ICON = "view-grid-symbolic"
SECTION = "Acting"
SUBTITLE = ("Pages of charts Chronoa built. Ask it for a dashboard and it "
            "appears here; open one to see it in Chronoa's browser.")


def _pretty(stem: str) -> str:
    return stem.replace("-", " ").replace("_", " ").strip().title() or stem


def _age(path: Path) -> str:
    try:
        secs = time.time() - path.stat().st_mtime
    except OSError:
        return ""
    if secs < 90:
        return "just now"
    for unit, n in (("day", 86400), ("hour", 3600), ("minute", 60)):
        if secs >= n:
            count = int(secs // n)
            return f"{count} {unit}{'s' if count != 1 else ''} ago"
    return "just now"


def _open(button: Gtk.Button, path: Path) -> None:
    """In Chronoa's own browser, else the desktop's - never drop the click."""
    from shani_chronoa.gui import blocks
    try:
        if blocks.show_in_own_browser(button, path.as_uri()):
            return
        Gtk.show_uri(button.get_root(), path.as_uri(), Gdk.CURRENT_TIME)
    except Exception:  # noqa: BLE001 - a pane must not crash on a bad path
        pass


def _dashboards() -> "list[Path]":
    folder = dashboard.default_dir()
    try:
        files = [p for p in folder.iterdir() if p.suffix.lower() in (".html", ".htm")]
    except OSError:
        return []
    return sorted(files, key=lambda p: p.stat().st_mtime, reverse=True)


def _fill(content: Gtk.Box, recorder: common.StatusRecorder) -> None:
    """(Re)draw the list into `content` in place - `set_content` appends, so a
    refresh clears and refills the one container rather than adding a second."""
    child = content.get_first_child()
    while child is not None:
        content.remove(child)
        child = content.get_first_child()

    found = _dashboards()
    count = len(found)
    summary = (f"{count} dashboard{'s' if count != 1 else ''}"
               if count else "No dashboards yet")
    # Both states are healthy: "none yet" is an answer, not a fault (amber is for
    # a panel that could not find out, which this always can).
    content.append(recorder.row(common.STATUS_OK, summary))
    if not found:
        content.append(common.empty_state(
            ICON, "No dashboards yet",
            "Ask Chronoa to build a dashboard - “a sales dashboard from this "
            "data” - and it will show up here.", inline=True))
        return
    group = common.group("")
    for path in found:
        try:
            size = path.stat().st_size
        except OSError:
            size = 0
        subtitle = " · ".join(x for x in (_age(path), f"{size / 1000:.0f} KB" if size else "") if x)
        open_button = Gtk.Button(label="Open")
        open_button.set_valign(Gtk.Align.CENTER)
        open_button.add_css_class("flat")
        open_button.connect("clicked", _open, path)
        adder = getattr(group, "add", None) or group.append
        adder(common.row(_pretty(path.stem), subtitle, suffix=open_button))
    content.append(group)


def build(app: Any) -> Gtk.Widget:
    page, set_content = common.surface(TITLE, SUBTITLE)
    recorder = common.StatusRecorder()
    page.status = recorder.status
    content = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)
    set_content(content)  # once: it appends, so later refreshes fill `content`
    _fill(content, recorder)
    # Re-read on every showing: the skill writes new dashboards from a chat turn
    # while this page sits built and cached in the window.
    if hasattr(page, "connect") and common.adw_ready():
        page.connect("showing", lambda *_a: _fill(content, recorder))
    return page


__all__ = ["TITLE", "ICON", "SECTION", "SUBTITLE", "build"]
