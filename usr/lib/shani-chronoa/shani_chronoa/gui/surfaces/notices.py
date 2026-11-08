"""The queue of things Chronoa noticed, newest first, with why and a dismiss.

Reflexes notify, an armed rule asks, a meeting app offers - until now each of
those was its own `notify-send`, and the person could not see what Chronoa had
been saying, could not dismiss a subject once for all, and the assistant
could not say why it had spoken. `notices.py` is the store; this page is the
read side, plus the dismiss button that makes the queue a queue instead of a
log.

**Read, never guessed.** Every row comes from `notices.list_open()` at build
time, and a dismissed row is removed from the page as well as from the store,
so what is shown and what is held cannot drift. An unreadable store is one
row that says so, not an empty page that looks like "nothing happened".

**Notice, not interrupt.** Nothing on this page raises, and nothing here
notifies: reading the queue must not become another interruption.
"""

from __future__ import annotations

import time
from typing import Any

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gtk

from shani_chronoa import notices
from shani_chronoa.gui.surfaces import common

TITLE = "Notices"
ICON = "mail-unread-symbolic"
SUBTITLE = ("What Chronoa noticed on its own, newest first. Dismissing one "
            "keeps it from nagging again for the same thing.")


def _when(at: float) -> str:
    return time.strftime("%Y-%m-%d %H:%M", time.localtime(at or 0))


def _subtitle(notice: dict) -> str:
    parts = [_when(notice.get("at") or 0), str(notice.get("kind") or "notice")]
    if notice.get("why"):
        parts.append(f"why: {notice['why']}")
    if notice.get("outcome"):
        parts.append(f"you said: {notice['outcome']}")
    return " | ".join(parts)


def _add(group: Gtk.Widget, row: Gtk.Widget) -> None:
    adder = getattr(group, "add", None)
    if callable(adder):
        adder(row)
    else:
        group.append(row)


def build(app: Any) -> Gtk.Widget:
    page, set_content = common.surface(TITLE, SUBTITLE)
    body = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
    body.set_margin_top(12)
    body.set_margin_start(12)
    body.set_margin_end(12)

    try:
        open_notices = notices.list_open()
    except Exception:  # noqa: BLE001 - a surface shows what it can
        open_notices = None

    if open_notices is None:
        group = common.group("Notices", "The queue could not be read.")
    elif not open_notices:
        group = common.group(
            "Notices",
            "Nothing Chronoa noticed is waiting. Reflexes, rule approvals "
            "and meeting offers appear here when they happen.")
    else:
        group = common.group(
            "Notices",
            f"{len(open_notices)} notice(s) waiting. Dismissing one stops "
            "the same thing being said again.")
        for notice in open_notices:
            notice_id = str(notice.get("id") or "")
            dismiss = Gtk.Button(label="Dismiss", valign=Gtk.Align.CENTER)
            dismiss.add_css_class("flat")

            def on_dismiss(_button, _id=notice_id, _row=None):
                notices.dismiss(_id)
                row = _button.get_parent()
                if row is not None:
                    parent = row.get_parent()
                    if parent is not None and hasattr(parent, "remove"):
                        parent.remove(row)

            dismiss.connect("clicked", on_dismiss)
            row = common.row(str(notice.get("title") or "Notice"),
                             _subtitle(notice), suffix=dismiss)
            _add(group, row)

    # The scroller is inside the page (activity's shape), so a long queue
    # scrolls under the toolbar instead of growing past the window.
    body.append(common.scrolled(group))
    set_content(body)
    return page


__all__ = ["TITLE", "ICON", "build"]
