"""Every page in Chronoa has an id, and any of them can be named from outside.

There were three ways to be somewhere in this app and only one of them worked for
anything but a person holding the mouse. The settings sections could be reached
only by typing into a search box; the setup wizard only by clicking Next through
it; the main window only by whatever happened to be on screen. So a notification
could not say "open Settings on Privacy", a keybinding could not either, and a
test could not - which is why driving this UI from a script meant guessing
selectors, and why a run could report four screenshots of four "different"
sections that were byte-identical.

This is the shape shani-cassini has had all along (`notebook.py`'s `PAGES`,
`page_ids()`, `select(pid)` and `ALIASES`, plus `--section=` and a `show-section`
action), generalised to three windows and one entry point:

    # from a terminal
    shani-chronoa --show-page=settings:privacy
    shani-chronoa --show-page=setup:review
    # from another app, over the bus
    gdbus call --session --dest dev.shani.chronoa \\
        --object-path /dev/shani/chronoa --method \\
        dev.shani.chronoa.show-page 'settings:privacy'

**An id is a promise, so retired ids resolve rather than disappear.** Rename a
section and the old id keeps opening whatever absorbed it, because a stored id
that opens nothing is worse than one that opens somewhere honest - the app
would look broken in a way nobody could diagnose from the id alone. Aliases live
here for that reason and nowhere else.

**An unknown id says so and changes nothing.** `show()` returns False and the
caller says "no such page" out loud. Guessing the nearest match would turn a
typo in a notification into a silently wrong page, which is the failure mode this
whole module exists to prevent.
"""

from __future__ import annotations

import logging
import re
from typing import Dict, Iterable, List, Optional, Tuple

logger = logging.getLogger(__name__)

#: window id -> the pages it can show, in order: (page id, human title)
_PAGES: Dict[str, List[Tuple[str, str]]] = {}

#: retired id -> the id that replaced it. Kept per window so `setup:brain` and
#: `settings:privacy` cannot collide.
_ALIASES: Dict[str, Dict[str, str]] = {}

#: window id -> the live window object, so an action can show a page on a window
#: that is not open yet.
_WINDOWS: Dict[str, object] = {}

#: window id -> the window class, for opening a window that was not built.
_FACTORIES: Dict[str, object] = {}


def slug(title: str) -> str:
    """`Tool activity` -> `tool-activity`.

    Titles are the source, so a page that is only ever identified by its heading
    cannot drift from the id that addresses it. An explicit id always wins.
    """
    return re.sub(r"[^a-z0-9]+", "-", (title or "").lower()).strip("-")


def register(window_id: str, pages: Iterable[Tuple[str, str]],
             factory=None, aliases: Optional[Dict[str, str]] = None) -> None:
    """Declare a window's pages. Idempotent: re-registering replaces the list."""
    _PAGES[window_id] = [(pid or slug(title), title) for pid, title in pages]
    _ALIASES[window_id] = dict(aliases or {})
    if factory is not None:
        _FACTORIES[window_id] = factory


def page_ids(window_id: str) -> List[str]:
    """Every live id for a window, aliases excluded."""
    return [pid for pid, _title in _PAGES.get(window_id, ())]


def page_titles(window_id: str) -> List[str]:
    return [title for _pid, title in _PAGES.get(window_id, ())]


def resolve(window_id: str, page_id: str) -> Optional[str]:
    """The live id a name refers to, or None.

    Follows one alias hop rather than looping: a cycle would hang a click, and a
    chain of renames longer than one is a sign the ids were not stable enough to
    depend on.
    """
    page_id = (page_id or "").strip().lower()
    live = {pid for pid, _t in _PAGES.get(window_id, ())}
    if page_id in live:
        return page_id
    seen = {page_id}
    target = _ALIASES.get(window_id, {}).get(page_id)
    while target and target not in live:
        if target in seen:                     # a cycle: refuse rather than hang
            logger.warning("pages: alias cycle for %s:%s", window_id, page_id)
            return None
        seen.add(target)
        target = _ALIASES.get(window_id, {}).get(target)
    return target


def windows() -> List[str]:
    return sorted(_PAGES)


def note_window(window_id: str, window) -> None:
    """Remember an open window so a page can be shown on it."""
    _WINDOWS[window_id] = window


def forget_window(window_id: str) -> None:
    _WINDOWS.pop(window_id, None)


def describe() -> str:
    """Every addressable page, for a log line or an error message."""
    lines = []
    for window_id in windows():
        live = page_ids(window_id)
        lines.append(f"{window_id}: " + ", ".join(live))
        aliases = _ALIASES.get(window_id) or {}
        for old, new in sorted(aliases.items()):
            lines.append(f"  {old} -> {new}")
    return "\n".join(lines)


def show(target: str, application=None, config=None) -> bool:
    """Show `window:page`. False if the window or the page is unknown.

    `target` is `"<window>:<id>"`; a bare id is looked up across every window, so
    `privacy` works as well as `settings:privacy` - and an ambiguous bare id
    resolves to nothing rather than to whichever window happened to register
    first.
    """
    if not target or ":" not in str(target):
        matches = [w for w in windows()
                   if resolve(w, str(target or "")) is not None]
        if len(matches) != 1:
            return False
        window_id, page_id = matches[0], resolve(matches[0], str(target))
    else:
        window_id, page_id = str(target).split(":", 1)
    if window_id not in _PAGES:
        logger.warning("pages: no window called %r", window_id)
        return False
    page_id = resolve(window_id, page_id)
    if page_id is None:
        logger.warning("pages: %s has no page %r. It has: %s",
                       window_id, page_id, ", ".join(page_ids(window_id)))
        return False

    window = _WINDOWS.get(window_id)
    if window is None:
        factory = _FACTORIES.get(window_id)
        if factory is None:
            logger.warning("pages: %s has no open window and no factory", window_id)
            return False
        window = factory(application, config) if application is not None else factory()
        note_window(window_id, window)
    show_page = getattr(window, "show_page", None) or getattr(window, "show_section", None)
    if show_page is None:
        logger.warning("pages: %s cannot show a page", window_id)
        return False
    if not show_page(page_id):
        logger.warning("pages: %s refused %r", window_id, page_id)
        return False
    presenter = getattr(window, "present", None)
    if presenter is not None:
        presenter()
    logger.info("pages: showing %s:%s", window_id, page_id)
    return True