"""The pending timers, where they can be seen and stopped.

**A running timer is a thing the person asked for, and at the width the
app opens at there was nowhere to see it.** The rail's Timers section held
them, and the rail is deleted below 1040px - so a kettle that was boiling
was invisible on the window the app actually opens at. The only other
place a timer appeared was the empty state's home board, which
disappears the moment a conversation starts.

So the timers live above the composer, where the task card already
lives, and **the rail no longer carries a second copy of them** - one
place, so the two cannot disagree about what is running.

**Not a link: there is no timers panel to open.** Cancel and five more
minutes are buttons on the row - they used to be "a spoken or typed
request", so stopping a kettle timer meant asking the model to call a
tool. These are the skill's own functions called directly: the person's
click, about their own timer.
"""

from __future__ import annotations

import logging
from typing import Callable, List, Optional, Tuple

import gi

gi.require_version("Gtk", "4.0")
from gi.repository import Gtk  # noqa: E402

logger = logging.getLogger(__name__)

#: One pending timer: its identifier, the label the person gave it, and the
#: seconds left on it.
Pending = Tuple[str, str, float]


def human_seconds(seconds: float) -> str:
    """`3m 12s`, `45s`, `1h 04m` - short enough to read in a row."""
    remaining = max(0, int(seconds))
    if remaining < 60:
        return f"{remaining}s"
    minutes, secs = divmod(remaining, 60)
    if minutes < 60:
        return f"{minutes}m {secs:02d}s"
    hours, minutes = divmod(minutes, 60)
    return f"{hours}h {minutes:02d}m"


def pending_timers() -> List[Pending]:
    """The running timers, as (identifier, label, seconds left).

    **A timer store that cannot be read is no timers, and says so in the
    log** - the same shape as every other glance in this app: a failure to
    read is not a reading of "none".
    """
    try:
        from shani_chronoa.skills.timer import active_timers

        return [tuple(one) for one in active_timers()]
    except Exception:  # noqa: BLE001 - a glance must not take the window down
        logger.debug("timers unreadable", exc_info=True)
        return []


class TimerStrip(Gtk.Box):
    """The pending timers, with the buttons that stop them.

    **Hidden entirely when there is nothing to show**, so the chrome of a
    quiet window is exactly what it was before this existed - the same
    rule the task card follows, and the reason the window's minimum
    height is unchanged on a machine with no timers running.
    """

    def __init__(self) -> None:
        super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        self.add_css_class("timer-strip")
        self.set_visible(False)

    # -- rows --------------------------------------------------------------

    def _clear(self) -> None:
        child = self.get_first_child()
        while child is not None:
            following = child.get_next_sibling()
            self.remove(child)
            child = following

    def _heading(self) -> None:
        label = Gtk.Label(label="Timers")
        label.add_css_class("dim-label")
        label.add_css_class("caption")
        label.set_xalign(0.0)
        self.append(label)

    def _row(self, identifier: str, label: str, left: float) -> None:
        """One timer: its name, what is left of it, and the two buttons."""
        row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        words = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        words.set_hexpand(True)
        name = Gtk.Label(label=label or "Timer")
        name.set_xalign(0.0)
        words.append(name)
        rest = Gtk.Label(label=f"{human_seconds(left)} left")
        rest.add_css_class("dim-label")
        rest.add_css_class("caption")
        rest.set_xalign(0.0)
        words.append(rest)
        row.append(words)

        from shani_chronoa.skills import timer as timer_skill

        def act(fn: Callable[[], None]) -> Callable[[], None]:
            def run() -> None:
                try:
                    fn()
                except Exception:  # noqa: BLE001 - a click must not take the window down
                    logger.debug("timer action failed", exc_info=True)
                self.refresh()

            return run

        for icon, tooltip, run in (
            ("list-add-symbolic", "Five more minutes",
             act(lambda: timer_skill.add_to_timer(300, identifier, label))),
            ("window-close-symbolic", "Cancel this timer",
             act(lambda: timer_skill.cancel_timer(identifier))),
        ):
            button = Gtk.Button(icon_name=icon)
            button.add_css_class("flat")
            button.add_css_class("circular")
            button.set_valign(Gtk.Align.CENTER)
            button.set_tooltip_text(tooltip)
            button.update_property([Gtk.AccessibleProperty.LABEL], [tooltip])
            button.connect("clicked", lambda _b, r=run: r())
            row.append(button)
        self.append(row)

    # -- refreshing --------------------------------------------------------

    def refresh(self) -> None:
        """Rebuild the rows from the timer store.

        **Never raises.** The strip is refreshed from the window's tick, and
        a tick that dies takes the rest of the window with it - so a timer
        store that cannot be read leaves the strip empty rather than taking
        the window down.
        """
        try:
            pending = pending_timers()
        except Exception as exc:  # noqa: BLE001
            logger.warning("the timer strip failed to refresh: %s", exc)
            pending = []
        self._clear()
        if not pending:
            self.set_visible(False)
            return
        self._heading()
        for identifier, label, left in pending:
            try:
                self._row(str(identifier), str(label), float(left))
            except Exception as exc:  # noqa: BLE001 - one bad timer must not hide the rest
                logger.warning("a timer row failed to build: %s", exc)
        self.set_visible(True)


def find(identifier: str) -> Optional[Pending]:
    """The pending timer with that identifier, or None."""
    for one in pending_timers():
        if str(one[0]) == str(identifier):
            return one
    return None
