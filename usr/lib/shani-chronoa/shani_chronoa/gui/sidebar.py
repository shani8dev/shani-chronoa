"""The sidebar: what this machine can be asked, and what it currently thinks.

Ten surfaces, each a page, and one list that gets a person to any of them. The
list is the whole reason this file exists - a panel that exists and cannot be
reached is the dead-code class this repo keeps paying for, so the sidebar is
built from the same registry the surfaces register in and there is no second
list to forget to update.

Two decisions worth stating:

- **Chat is the first row**, before any surface. The window opens on a
  conversation, and a sidebar whose first entry is "Senses" says the wrong thing
  about what this program is.
- **Rows are built, never registered twice.** `available_surfaces()` returns a
  dict keyed by id; a duplicate id cannot appear, so a surface added twice is a
  no-op rather than two identical rows.

`Adw.NavigationSplitView` does the collapsing itself, and with it the one thing
worth getting right on a small screen: below the libadwaita breakpoint the
sidebar becomes an overlay the content slides aside for, so the chat is never
squeezed into a column of unreadable text.
"""

from __future__ import annotations

import logging
from typing import Optional

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")

from gi.repository import Adw, Gtk  # type: ignore

from shani_chronoa.gui.surfaces import common

logger = logging.getLogger(__name__)

#: The row that is not a surface: back to the conversation.
CHAT_TITLE = "Conversation"
CHAT_ICON = "chat-bubble-symbolic"


class SidebarPage(Adw.NavigationPage):
    """The panel list: search, the sections, and the way back to the chat.

    **This is a real subclass, and that matters.** It was previously a factory
    that built a plain `Adw.NavigationPage` and stashed attributes on it - so
    `self.select()` did not exist on the object actually returned. Every panel
    open therefore raised `AttributeError: 'NavigationPage' object has no
    attribute 'select'` at `window.py`, and because the page was pushed *before*
    the raise, the panel still appeared and the exception was swallowed by
    whatever ran next: the sidebar silently stopped saying where you were. Found
    by clicking a row for real, not by any test - nothing asserted the marking.

    Sections rather than one flat list, because seventeen rows with no grouping is
    a list nobody scrolls. The order comes from `surfaces.SECTION_ORDER` rather
    than from each panel's position in the registry, so adding a panel cannot
    reshuffle everything below it.
    """

    def __init__(self, app=None, on_chat=None, on_surface=None) -> None:
        """Built by construction, not by a factory.

        `Adw.NavigationPage.__init__` has to run before any of this touches a
        widget, and a classmethod that returned a *different* object is exactly
        how `select()` ended up defined on a class whose instances were never
        instances of it.
        """
        super().__init__(title="Panels")
        toolbar = Adw.ToolbarView()
        header = Adw.HeaderBar()
        toolbar.add_top_bar(header)
        header.set_title_widget(Gtk.Label(label="Chronoa"))

        self._app = app
        stack = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        search = common.search_entry("Search panels")
        search.set_margin_top(6)
        search.set_margin_bottom(6)
        search.set_margin_start(12)
        search.set_margin_end(12)
        stack.append(search)
        scroller = Gtk.ScrolledWindow(vexpand=True)
        scroller.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        stack.append(scroller)
        body = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=18)
        body.set_margin_top(6)
        body.set_margin_bottom(18)
        body.set_margin_start(12)
        body.set_margin_end(12)
        scroller.set_child(body)
        toolbar.set_content(stack)
        self.set_child(toolbar)

        def activate(_row, key: Optional[str]) -> None:
            if key is None:
                if on_chat is not None:
                    on_chat()
            elif on_surface is not None:
                on_surface(key)

        self._surface_rows = {}
        self._rows_by_title = {}
        groups = {}
        for section in _section_order():
            entries = []
            for name, (title, icon, _build) in _surfaces().items():
                if _section_of(name) != section:
                    continue
                entries.append((name, title, icon))
            if not entries:
                continue
            group = common.group(section)
            body.append(group)
            groups[section] = group
            for name, title, icon in entries:
                row = Adw.ActionRow(title=title)
                row.add_prefix(Gtk.Image.new_from_icon_name(icon))
                row.set_activatable(True)
                row.update_property([Gtk.AccessibleProperty.LABEL], [title])
                row.connect("activated", lambda _r, key=name: activate(_r, key))
                group.add(row)
                self._surface_rows[name] = (row, title.lower(), icon, section)
            self._rows_by_title[section] = entries

        self._chat_row = None
        self._body = body
        self.search = search
        self.groups = groups

        def _filter(text: str) -> None:
            needle = (text or "").strip().lower()
            for key, (row, title, icon, section) in self._surface_rows.items():
                row.set_visible(not needle or needle in title or needle in icon
                                or needle in key or needle in section.lower())
            # A section heading with nothing under it is a heading that lies
            # about what is on the screen, so it goes with its rows.
            for section, entries in self._rows_by_title.items():
                visible = [name for name, _t, _i in entries
                           if self._surface_rows[name][0].get_visible()]
                if section in groups:
                    groups[section].set_visible(bool(visible))

        self._filter = _filter
        search.connect("search-changed", lambda e: _filter(e.get_text()))

    def select(self, key: Optional[str]) -> None:
        """Mark the open panel, so the sidebar says where you are.

        With rows spread over several `Adw.PreferencesGroup`s there is no
        single-selection list to select from, so "where am I" is the `selected`
        CSS class on the row itself - which is also what makes it visible when
        the panel is tall and the list is scrolled.
        """
        for name, (row, _title, _icon, _section) in self._surface_rows.items():
            if name == key:
                row.add_css_class("selected")
            else:
                row.remove_css_class("selected")


def _section_of(name: str) -> str:
    from shani_chronoa.gui import surfaces

    try:
        return surfaces.sections().get(name, "") or "Everything else"
    except Exception:
        logger.error("Panel sections could not be read", exc_info=True)
        return "Everything else"


def _section_order():
    from shani_chronoa.gui import surfaces

    try:
        return tuple(surfaces.SECTION_ORDER)
    except Exception:
        return ()


def _surfaces():
    from shani_chronoa.gui import surfaces

    try:
        return surfaces.available_surfaces()
    except Exception:
        # A sidebar with no entries is still a usable window: the chat does not
        # depend on any panel. Losing the traceback here would make a broken
        # panel look like a deliberately empty one.
        logger.error("The panel registry could not be read", exc_info=True)
        return {}


class SidebarBreakpoint:
    """The libadwaita breakpoint that collapses the sidebar on a small window.

    `Adw.NavigationSplitView` needs a breakpoint to know when "sidebar" means
    "a permanent column" and when it means "a drawer over the content". Without
    one the split view keeps both at every width, which on a 500px window is a
    200px sidebar of ten words next to 300px of chat.
    """

    @staticmethod
    def apply(split: "Adw.NavigationSplitView", max_width: int = 720) -> "Adw.Breakpoint":
        """`Adw.Breakpoint.new()` takes a *condition*, not a width.

        Measured on libadwaita 1.5: `Adw.Breakpoint.new(720)` raises (`Expected
        Adw.BreakpointCondition, but got int`) and `BreakpointCondition.new` does
        not exist - the constructor is `parse()`, and the binding is
        `add_setter`. `max-width: 720px` is resolved by GTK's own breakpoint
        machinery, so it follows the same scale the rest of the stylesheet does.
        """
        # `BreakpointCondition.parse()` is the constructor on libadwaita 1.5 -
        # there is no `.new()` - and `Breakpoint.add_setter` is how a condition
        # is bound to a property. Both spellings here were found by calling the
        # installed library, not by reading: the first two attempts raised.
        condition = Adw.BreakpointCondition.parse(f"(max-width: {max_width}px)")
        breakpoint = Adw.Breakpoint.new(condition)
        # `add_setter(object, property, value)` - four arguments: the object, the
        # property name, and the value to set when the condition applies.
        breakpoint.add_setter(split, "collapsed", True)
        return breakpoint