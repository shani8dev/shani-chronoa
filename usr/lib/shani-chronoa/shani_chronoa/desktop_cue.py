"""Say on screen what Chronoa is doing - every tool call, not only input.

Chronoa acts on the person's desktop, and a person cannot tell its actions
from their own unless it says so where they are looking - which is often not
Chronoa's window. So each tool call is shown by Chronoa's GNOME Shell
extension as a pill at the top of the screen, in the same words as the chat's
tool card (`gui.blocks.call_phrase`): "Chronoa: Changed pricing.py".

Fire-and-forget: one asynchronous D-Bus call, never waited on, and nothing at
all when the extension is not there (another desktop, or it is off).
"""
from __future__ import annotations

import logging

logger = logging.getLogger(__name__)

BUS_NAME = "org.shani.Chronoa.Windows"
OBJECT_PATH = "/org/shani/Chronoa/Windows"
INTERFACE = "org.shani.Chronoa.Windows"


def show(text: str, hold: bool = False) -> None:
    try:
        from gi.repository import Gio, GLib
        bus = Gio.bus_get_sync(Gio.BusType.SESSION, None)
        bus.call(BUS_NAME, OBJECT_PATH, INTERFACE, "Cue", GLib.Variant("(sb)", (text, hold)),
                 None, Gio.DBusCallFlags.NO_AUTO_START, 2000, None, None, None)
    except Exception:  # noqa: BLE001 - a cue is never worth a failure
        logger.debug("could not show the desktop cue", exc_info=True)


def for_tool(name: str, arguments: dict) -> str:
    """The cue for one tool call: the tool card's own words, after "Chronoa: "."""
    try:
        from shani_chronoa.gui.blocks import call_phrase, tool_words
        phrase = call_phrase(name, arguments or {})
        if not phrase or "=" in phrase:
            phrase = tool_words(name)
    except Exception:  # noqa: BLE001
        phrase = name.replace("_", " ")
    return f"Chronoa: {phrase[:1].upper()}{phrase[1:]}"


def plan_text() -> str:
    """Where Chronoa is in its own checklist, for the screen; "" with no plan.

    "Chronoa · step 2 of 4 · Run the tests" - so a several-step job can be
    followed without its window open. Read from the checklist itself
    (`todo_list`, this project's items in Build mode), never from the model.
    """
    try:
        from shani_chronoa.skills import todo_list
        items, _problem = todo_list._load()  # noqa: SLF001 - the one reader
        items = todo_list.project_items(items or [], todo_list.current_project())
    except Exception:  # noqa: BLE001
        return ""
    if not items:
        return ""
    done = sum(1 for i in items if i.get("status") == "completed")
    current = next((i for i in items if i.get("status") == "in_progress"), None) \
        or next((i for i in items if i.get("status") == "pending"), None)
    if current is None:
        return f"Chronoa · all {len(items)} steps done"
    step = items.index(current) + 1
    words = " ".join(str(current.get("content") or "").split())[:60]
    return f"Chronoa · step {step} of {len(items)} ({done} done) · {words}"
