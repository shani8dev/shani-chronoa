"""One page for a job that was split across several panels.

The sidebar grew to 37 panels because each question got its own tile: "what is
answering" and "which models exist" were two; "routines", "rules", "goals" and
"background mode" were four; "diff", "editor" and "artifacts" were three;
"diagnostics", "desktop", "machine" and "what's running" were four. A person
answering one question had to open several things and remember which was which.

A hub is a page with a tab for each member. **It embeds the member's own
`build()` page**, so there is one implementation of each answer - the hub does
not re-read the store or redraw the rows. A panel that changes still changes in
one place, and the hub and the original sidebar row cannot disagree.

**The originals stay in `SURFACE_IDS`.** Removing them would break every page
id, palette row, notification and test that names one, and the point of a hub is
a shorter path, not a broken one. A hub is another way in, listed in the
sidebar; the old panel keeps resolving. Nothing here deletes a capability.

**`build()` returns a page with `status()`** from the first member that has one,
so the hub's sidebar dot says what the member would say rather than inventing
a health of its own.
"""

from __future__ import annotations

import importlib
import logging
from typing import Any, List, Tuple

import gi  # noqa: E402

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gtk  # noqa: E402

from shani_chronoa.ui_gtk.gui.surfaces import common  # noqa: E402

logger = logging.getLogger("shani_chronoa.gui.surfaces.hub")


def _member(name: str, app: Any):
    module = importlib.import_module(f"shani_chronoa.ui_gtk.gui.surfaces.{name}")
    return module, module.build(app)


def build_hub(
    app: Any,
    title: str,
    subtitle: str,
    icon: str,
    members: Tuple[str, ...],
    section: str,
    first: str = "",
) -> Gtk.Widget:
    """A titled, scrollable page whose tabs are the member surfaces.

    `first` names the tab shown on open; an empty string opens the first member.
    A member that fails to build costs that tab, not the hub - a panel that
    shells out to a missing binary must not take the whole page down.
    """
    page, set_content = common.surface(title, subtitle)
    stack = Adw.ViewStack(vexpand=True)
    names: List[str] = []
    for name in members:
        try:
            _module, widget = _member(name, app)
        except Exception as exc:  # noqa: BLE001 - one broken member is not the hub
            logger.warning("hub member %r could not be built: %s", name, exc)
            label = Gtk.Label(label=f"{name} could not be opened here")
            label.set_wrap(True)
            stack.add_titled(
                common.scrolled(label), name, name.replace("_", " ").title()
            )
            names.append(name)
            continue
        stack.add_titled(widget, name, name.replace("_", " ").title())
        names.append(name)

    switcher = Adw.ViewSwitcher(stack=stack, policy=Adw.ViewSwitcherPolicy.WIDE)
    switcher.set_halign(Gtk.Align.CENTER)
    wrapper = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
    wrapper.set_margin_top(10)
    wrapper.append(switcher)
    wrapper.append(stack)
    set_content(wrapper)
    if first and first in names:
        stack.set_visible_child_name(first)

    # The hub's health is the first member's: the same recorder, so the dot on
    # this sidebar row and that member's own row say the same thing.
    source = getattr(
        stack.get_child_by_name(first or (names[0] if names else "")), "status", None
    )
    if callable(source):
        page.status = source
    else:
        recorder = common.StatusRecorder()
        recorder.set(common.STATUS_OK)
        page.status = recorder.status
    return page
