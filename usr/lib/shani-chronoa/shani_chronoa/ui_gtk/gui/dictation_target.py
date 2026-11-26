"""Dictate into the app that had focus, through the driver's guarded type path.

Myna's core flow is a hotkey, speech, and the resulting text in the app the
person was using. Chronoa's `dictate` already captures and transcribes a long
utterance; what it did not have was the last hop - putting those words into the
focused app. This module is that hop.

**One type path, with its guard.** `ui_driver.type_text` is the only writer:
it checks the secure-field guard (a password box is refused on every route),
the comms guard (a chat or mail window is refused) and the focus rule before a
single keystroke leaves the process. A helper that called `wtype` or XTEST
directly would be a second path around all three, and the guard would be a
label rather than a control.

**The target is remembered before dictation, not guessed after.** Starting the
microphone changes focus to Chronoa's own window, so asking afterwards which
app was focused would answer "Chronoa" every time. `remember_target()` is
called on the hotkey press, before `dictate`; `send()` uses what it stored.

**Refusals are sentences, not exceptions.** A secure field, a comms app, no
focus and a driver that cannot reach the desktop all answer with a line the
caller can toast. Nothing here retries with another route when the guarded
one refuses - the refusal is the safety property, not a transient error.
"""

from __future__ import annotations

import logging
from typing import Any, Optional, Tuple

logger = logging.getLogger("shani_chronoa.gui.dictation_target")


class Target:
    """The app (and window title) that had focus when dictation was started."""

    __slots__ = ("app", "title")

    def __init__(self, app: str, title: str = "") -> None:
        self.app = (app or "").strip()
        self.title = (title or "").strip()

    def as_tuple(self) -> Tuple[str, str]:
        return self.app, self.title

    def __repr__(self) -> str:  # a log line, never a stored secret
        return f"Target(app={self.app!r}, title={self.title!r})"


def focused_target() -> Target:
    """Ask the window backend which app has focus, before we take it.

    The backend is asked through its own reader rather than a global: on niri
    `focused-window` is the only evidence (an action's exit code lies), on X11
    the active window's class is. A desktop that cannot answer gives an empty
    target, which `send()` reports as a refusal instead of guessing.
    """
    try:
        from shani_chronoa import ui_driver

        # `ui_driver.windows()` is the desktop's own list: (id, title, box).
        # The title is what its `find_window` matches on, so carrying it -
        # rather than a guessed app id - is what makes the later type land.
        for item in ui_driver.windows():
            if not isinstance(item, (tuple, list)) or len(item) < 2:
                continue
            title = str(item[1] or "").strip()
            if not title or "chronoa" in title.lower():
                continue
            app = title.split(" — ")[0].split(" - ")[0].strip()
            return Target(app, title)
    except Exception:  # noqa: BLE001 - a desktop without a backend answers no
        logger.debug("dictation target could not be read", exc_info=True)
    return Target("", "")


def remember_target(store: Any) -> Target:
    """Store the focused app on `store` and return it.

    `store` is the window: a hotkey handler has one, and a test can supply a
    stand-in. The attribute is named so the same value is read by `send()`.
    """
    target = focused_target()
    setattr(store, "_dictation_target", target)
    return target


def remembered_target(store: Any) -> Optional[Target]:
    value = getattr(store, "_dictation_target", None)
    return value if isinstance(value, Target) else None


def send(text: str, store: Any) -> str:
    """Type `text` into the remembered app; return what happened.

    An empty string is not a keystroke - a dictation that produced no words
    must not focus an app and send nothing. The guard inside `type_text` does
    the refusing; this function only decides whether there is text and a
    target, and carries the driver's answer back as a sentence.
    """
    words = (text or "").strip()
    if not words:
        return "Dictation produced no words, so nothing was typed"
    target = remembered_target(store)
    if target is None or not target.app:
        return (
            "No focused app was remembered. Press the hotkey first, then "
            "dictate; nothing was typed"
        )
    try:
        from shani_chronoa import ui_driver

        result = ui_driver.type_text(target.app, target.title, words)
        return str(result or f"Typed dictation into {target.app}")
    except Exception as exc:  # noqa: BLE001 - a refused keystroke is a sentence
        return f"Dictation was not typed: {exc}"


def clear(store: Any) -> None:
    """Drop a remembered target so a later turn cannot reuse a stale one."""
    try:
        delattr(store, "_dictation_target")
    except AttributeError:
        pass
