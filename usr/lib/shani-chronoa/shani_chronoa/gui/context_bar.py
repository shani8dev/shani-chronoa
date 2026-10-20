"""How full the model's window is, where the person can see it.

**"It forgot what I said" has to be distinguishable from "the window is
full"**, and at the width the app opens at the rail - the only place that
distinction was drawn - is deleted. The rail's own meter says where the
forgetting starts: **75% of what can still be sent**, which is the same
threshold the rail's level bar uses for its own colours.

So the bar is above the composer, at every width, and it appears **only
when it has something to say** - a window at 15% is a window working
normally, and a permanent bar for it would be chrome on every window for
a reading nobody needs. Below the threshold the chrome of a quiet window
is exactly what it was measured to be.

**The bar shows the *usable* share, not the raw one.** A window that is
80% full of input while 1,024 tokens are held back for the reply is 91%
used, and the number that answers "can another question still fit" is the
second one. The raw percentage is in the tooltip, so nothing is lost.
"""

from __future__ import annotations

import logging
import gi

gi.require_version("Gtk", "4.0")
from gi.repository import Gtk  # noqa: E402

logger = logging.getLogger(__name__)

#: **Where "it forgot what I said" starts** - the rail's own threshold for
#: its level bar's colours, kept so the two read the same way.
#:
#: **A threshold, not a measurement**: the question is when a person
#: should be told, and the answer the rail already gave is 75% of what
#: can still be sent.
WARN_AT = 0.75


class ContextBar(Gtk.Box):
    """A slim level bar saying how full the model's window is.

    **Hidden entirely when the window is not filling**, so a quiet
    conversation costs nothing - the same rule the timer strip and the task
    card follow, and the reason the window's minimum height does not move.
    """

    def __init__(self) -> None:
        super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        self.add_css_class("context-bar")
        self.set_visible(False)
        self._bar = Gtk.LevelBar(min_value=0.0, max_value=1.0)
        # Offsets named for the theme's own colours - the rail's bar uses
        # the same three, so the two read the same way.
        self._bar.add_offset_value("low", 0.75)
        self._bar.add_offset_value("high", 0.9)
        self._bar.add_offset_value("full", 1.0)
        self._bar.set_valign(Gtk.Align.CENTER)
        self.append(self._bar)

    def set_report(self, report) -> None:
        """Show the report, or hide the bar when there is nothing to say.

        **Never raises.** The bar is refreshed from the window's tick, and a
        tick that dies takes the rest of the window with it.
        """
        try:
            self._apply(report)
        except Exception as exc:  # noqa: BLE001 - a glance must not take the window down
            logger.warning("the context bar failed to update: %s", exc)
            self.set_visible(False)

    def _apply(self, report) -> None:
        if report is None:
            self.set_visible(False)
            return
        usable = getattr(report, "usable_percent", None)
        if usable is None:
            self.set_visible(False)
            return
        fraction = max(0.0, min(1.0, float(usable) / 100.0))
        if fraction < WARN_AT:
            # **Below the line the window is working normally**, and a bar
            # for it would be chrome on every window.
            self.set_visible(False)
            return
        self._bar.set_value(fraction)
        # **A GTK4 level bar has no text at all.** `set_text` was GTK3, and
        # GTK4's `Gtk.LevelBar` has neither it nor a label child - so the
        # number goes in the tooltip and in the accessible name, and the bar
        # itself is the reading. That is the rail's own bar: a share of
        # something is read faster as a length than as a number.
        self._bar.set_tooltip_text(self._tooltip(report))
        self._bar.update_property(
            [Gtk.AccessibleProperty.LABEL],
            [f"the model's window is {usable:.0f}% used - "
             "hover for what is filling it"])
        self.set_visible(True)

    @staticmethod
    def _tooltip(report) -> str:
        """The whole reading, for the hover: the headline, the raw share and
        what is filling it."""
        parts = [report.headline()]
        raw = getattr(report, "percent", None)
        usable = getattr(report, "usable_percent", None)
        reserve = getattr(report, "reply_reserve", 0)
        if raw is not None and usable is not None and raw != usable:
            parts.append(f"{usable:.0f}% of what can still be sent "
                         f"({raw:.0f}% of the raw window, with "
                         f"{reserve:,} tokens held back for the reply)")
        rows = getattr(report, "segment_rows", None)
        if callable(rows):
            try:
                for label, tokens, _share in rows():
                    parts.append(f"{label}: {tokens:,} tokens")
            except Exception:  # noqa: BLE001 - a tooltip is not worth a crash
                logger.debug("context breakdown unreadable", exc_info=True)
        if getattr(report, "elided", False):
            parts.append("part of this conversation was already dropped to "
                         "make the request fit")
        return "\n".join(str(part) for part in parts)


