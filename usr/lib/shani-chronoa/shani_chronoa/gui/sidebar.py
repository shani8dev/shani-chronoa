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
#:
#: "Conversation" (singular) against the "Conversations" panel (plural), because
#: this row is the live chat - the thing on screen now - and that panel is the
#: list of saved ones. With both in the sidebar, and a "Conversation" section
#: heading above the panel as well, the word appeared three times and meant two
#: different things. The section is gone and the panel has moved to
#: "Remembering", so the singular here now names exactly one row.
CHAT_TITLE = "Conversation"
#: `chat-bubble-symbolic` does not exist on the installed theme (measured:
#: `has_icon` False), so the conversation row rendered blank. `chat-symbolic` is
#: checked to exist rather than assumed - **on Yaru**, which is the theme of the
#: machine that checked it. Adwaita, the theme ShaniOS ships, has no
#: `chat-symbolic`, so on the target desktop the row was blank again.
#: `chat-message-new-symbolic` is in both; `test_sidebar_icons_exist_in_adwaita`
#: checks Adwaita's own files so the installed theme cannot hide this a third time.
CHAT_ICON = "chat-message-new-symbolic"

#: One icon size for the whole sidebar - rows, the chat row and the gear.
#:
#: **Every one of the 22 icons exists in the installed theme** (checked with
#: `Gtk.IconTheme.has_icon`, not assumed), and all of them measured 16px tall,
#: because nothing here ever asked for a size and 16px is what libadwaita
#: defaults to. That is legible for `chat-symbolic` and `audio-volume-high-symbolic`
#: and effectively invisible for `phone-symbolic` and `view-app-grid-symbolic`,
#: which at 16px render as an empty rectangle and a faint dot grid.
#:
#: So the complaint that "some of them have no icon" is not a missing name - it
#: is a missing size. The icons that survived being too small are the ones drawn
#: with the most strokes.
#:
#: 20px is a deliberate bump rather than a larger one: the rows are ~40px tall,
#: and past ~22px the icons start crowding the title rather than helping.
SIDEBAR_ICON_PX = 20



def _settings_target(name: str) -> "Optional[str]":
    """The panel's settings target, or None. Read through `surfaces`, not a copy.

    A second table here would be a second thing to keep in step with the first -
    and this file's own docstring is about exactly that failure, for the list of
    panels themselves.
    """
    from shani_chronoa.gui import surfaces

    return surfaces.settings_target(name)


def _open_settings(app, target: str) -> None:
    """Open a settings page from a sidebar row, and report a refusal as nothing.

    Routed through `common.open_page`, which is the same function every panel's
    own "Open Privacy settings" button uses - so a gear and a panel button are
    one route, not two that can drift. That helper also refuses rather than
    raises when the id is unknown, which matters here: `pages.show` returning
    False for a retired id opens nothing, and a gear that visibly did nothing is
    worse than no gear, so the failure is logged where a developer sees it.
    """
    common.open_page(app, target)


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

    def __init__(self, app=None, on_chat=None, on_surface=None,
                 on_close=None) -> None:
        """Built by construction, not by a factory.

        `Adw.NavigationPage.__init__` has to run before any of this touches a
        widget, and a classmethod that returned a *different* object is exactly
        how `select()` ended up defined on a class whose instances were never
        instances of it.

        `on_close` is separate from `on_chat` on purpose: closing a drawer must
        not also pop the content stack, because the drawer can be sitting over a
        panel that was already open.
        """
        super().__init__(title="Panels")
        # **No `Adw.HeaderBar` here, on purpose.**
        #
        # This page is the sidebar of an `Adw.NavigationSplitView`, and the split
        # view already gives the window one header bar with the window controls
        # and a title. The sidebar built a second one titled "Chronoa", so the
        # window showed two title bars stacked - "Chronoa" above "Shani Chronoa"
        # - and, because both belonged to the split view, **two back arrows**:
        # counted in a rendered window, both were `AdwBackButton`, one per header
        # bar, and neither was labelled. Two arrows that mean "close the sidebar"
        # read as two different destinations.
        #
        # The split view paints the title, the window controls and the back
        # affordance for the whole window, which is what it is for. Dropping this
        # header also gives back ~48px of height on every panel - and `Machine`
        # was overflowing its viewport.
        #
        # The `Adw.ToolbarView` is kept, because it is what `self` needs in order
        # to hold a scrolled body, and its absence of a *permanent* top bar is the
        # point - see `set_drawer_mode` for the one case where one appears.
        toolbar = Adw.ToolbarView()
        toolbar.add_top_bar(self._build_drawer_bar(on_close))

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
        body = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)
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
        self._dots = {}
        #: panel id -> the settings gear on its row. Absent for the panels
        #: Settings does not govern - see `surfaces.SETTINGS_TARGETS`.
        self._gears = {}
        self._rows_by_title = {}
        groups = {}

        # The conversation row, first and above every section.
        #
        # `CHAT_TITLE`/`CHAT_ICON` were module constants and this attribute was
        # `None`, so the state the window actually opens in had no row at all:
        # opening the chat marked nothing, because there was nothing to mark. The
        # module docstring's "chat is the first row" was describing an intention
        # the constructor did not carry out.
        #
        # It is appended *before* the section loop on purpose, and this comment
        # is here because it was not: the block sat below the loop, so the row
        # rendered last, under a section list it is nothing to do with, while
        # the code above it said "first and above every section". The comment
        # was describing the code as written somewhere else. Both the comment
        # and the docstring were right about the intent and wrong about the
        # tree; this is the line that carries the intent.
        chat_row = Adw.ActionRow(title=CHAT_TITLE)
        chat_row.add_css_class("sidebar-row")
        chat_icon = Gtk.Image.new_from_icon_name(CHAT_ICON)
        chat_icon.set_pixel_size(SIDEBAR_ICON_PX)
        chat_row.add_prefix(chat_icon)
        chat_row.set_activatable(True)
        chat_row.update_property([Gtk.AccessibleProperty.LABEL], [CHAT_TITLE])
        chat_row.connect("activated", lambda _r: activate(_r, None))
        body.append(common.group())  # no heading: it is above the sections
        chat_group = body.get_last_child()
        chat_group.add(chat_row)

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
                # `sidebar-row` so the `selected` rule above is scoped to this
                # list. `selected` is a libadwaita class name that a
                # `PreferencesRow` may also carry, and a rule with no subject
                # would style every selected row on screen rather than the open
                # panel.
                row.add_css_class("sidebar-row")
                panel_icon = Gtk.Image.new_from_icon_name(icon)
                panel_icon.set_pixel_size(SIDEBAR_ICON_PX)
                row.add_prefix(panel_icon)
                # The health dot, beside the icon rather than after the title.
                #
                # The sidebar is the only thing on screen that shows every panel
                # at once, and it showed them all identically - so Conversation,
                # Calendar and Diagnostics looked equally healthy, and the only
                # way to find out which needed attention was to open each one in
                # turn. Six pixels per row turns a list into a dashboard.
                #
                # It is *hidden* by default and only shown when a panel has
                # something to report: a dot on every row would be a dot that
                # means nothing, which is the state this is fixing.
                # `common.status_dot()` rather than a widget of our own: the
                # sidebar row and the panel's own status row are the same mark,
                # and three separate `Gtk.Label`s is how three separate squares
                # happened. `.sidebar-dot` adds only the sidebar's own shape.
                dot = common.status_dot()
                dot.add_css_class("sidebar-dot")
                dot.set_visible(False)
                # A **prefix**, beside the icon - which is what the comment above
                # this block has always said, and what `add_suffix` did not do.
                #
                # As a suffix the dot was the last thing before the gear, so it
                # sat at a different x on every row that has one: measured on the
                # rendered sidebar, the dot on `Machine` was hard against the
                # right edge while the dot on `Senses` was 40px further left. The
                # whole reason for a dot per row is that the sidebar is the one
                # thing on screen showing every panel at once, so its job is to be
                # scannable - and a column of markers in two columns is not one.
                # As a prefix it lands after the icon on every row, whatever else
                # that row carries.
                row.add_prefix(dot)
                self._dots[name] = dot
                # **A gear on the rows whose settings actually govern them.**
                #
                # The panels are read-only reports and the settings window holds
                # the switches, which is the right split - and it made every
                # "turn this on in Settings" a sentence naming a window with no
                # way to reach it. The gear is one click from the row that says
                # the thing is off, so the route is on the same screen as the
                # complaint.
                #
                # Only on the panels `surfaces.settings_target` names. A gear that
                # opens a section of unrelated switches is a dead end wearing a
                # button, and a dot of honesty here costs the reader nothing: the
                # rows without one are the ones Settings does not govern.
                target = _settings_target(name)
                if target is not None:
                    # A child `Gtk.Image` rather than `icon_name=`, because
                    # GTK4's button icon size is the `icon-size` CSS property and
                    # that takes a `Gtk.IconSize` enum, not pixels - so there is
                    # no way to make the gear exactly 20px through the icon_name
                    # route, and `Gtk.Button.set_icon_size()` is GTK3 and does not
                    # exist (measured: AttributeError). An image child takes
                    # pixels, which is what matching the rows actually needs.
                    # **`emblem-system-symbolic`, not `preferences-system-symbolic`,
                    # and the reason is that both names exist in both themes.**
                    # `preferences-system-symbolic` is a *gear* in Yaru and a
                    # *wrench and screwdriver* in Adwaita - the sidebar's "Open
                    # Settings on X" button drew the wrench on GNOME, from the
                    # theme ShaniOS ships. Found by rendering the panel and
                    # looking, then rasterising each theme's own file side by
                    # side: `has_icon` is True for both names in both themes, so
                    # neither the installed-theme check nor
                    # `test_sidebar_icons_exist_in_adwaita.py` can see this -
                    # it is not a missing icon, it is the wrong picture.
                    # `emblem-system-symbolic` is a gear in Adwaita *and* Yaru.
                    #
                    # Not unit-tested, deliberately: comparing the two themes'
                    # pixels cannot decide "same meaning" - measured, same-meaning
                    # pairs differ on 32-52% of alpha pixels and different-meaning
                    # pairs on 51-55%, so the ranges overlap and any threshold
                    # flags every icon. A render is the instrument here.
                    gear_icon = Gtk.Image.new_from_icon_name(
                        "emblem-system-symbolic")
                    gear_icon.set_pixel_size(SIDEBAR_ICON_PX)
                    gear = Gtk.Button(child=gear_icon)
                    gear.add_css_class("flat")
                    gear.add_css_class("sidebar-gear")
                    gear.set_valign(Gtk.Align.CENTER)
                    section_word = target.split(":")[-1].replace("-", " ")
                    gear.set_tooltip_text(
                        f"Open Settings on {section_word}")
                    gear.update_property(
                        [Gtk.AccessibleProperty.LABEL],
                        [f"Open Settings on {section_word} for {title}"])
                    gear.connect(
                        "clicked",
                        lambda _b, t=target: _open_settings(self._app, t))
                    row.add_suffix(gear)
                    self._gears[name] = gear
                row.set_activatable(True)
                row.update_property([Gtk.AccessibleProperty.LABEL], [title])
                row.connect("activated", lambda _r, key=name: activate(_r, key))
                group.add(row)
                self._surface_rows[name] = (row, title.lower(), icon, section)
            self._rows_by_title[section] = entries

        self._chat_row = chat_row
        self._chat_group = chat_group
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

    def set_status(self, key: Optional[str], status: Optional[str]) -> None:
        """Mark a panel's row with a health dot, or clear it.

        `status` is one of `common.STATUS_OK` / `_ATTENTION` / `_UNKNOWN`, or
        None to hide the dot again. **A healthy panel still gets its dot**, but
        a panel with nothing to say gets none - a dot on all twenty rows would be
        the flat list it replaced, drawn smaller.

        The dot is drawn rather than asked for as a glyph, for the reason
        `machine.py` records for its own banner: an icon name is a request the
        theme may not fill, and a missing one is an empty box that looks like a
        rendering bug rather than a missing glyph.
        """
        dot = self._dots.get(key) if key is not None else None
        if dot is None:
            return
        if status is None:
            dot.set_visible(False)
            return
        if status not in common.STATUS_CLASSES:
            raise ValueError(
                f"unknown status {status!r}; expected one of "
                f"{sorted(common.STATUS_CLASSES)} or None"
            )
        for existing in common.STATUS_CLASSES.values():
            dot.remove_css_class(existing)
        dot.add_css_class(common.STATUS_CLASSES[status])
        dot.set_visible(True)
        dot.set_tooltip_text(
            f"{self._surface_rows[key][1].capitalize()}: "
            f"{common.STATUS_WORDS[status]}"
        )

    def _build_drawer_bar(self, on_close) -> Gtk.Widget:
        """The one header bar this page has, and it only exists as a drawer.

        **A drawer over the content needs its own way out, because the button
        that opened it is underneath it.** Measured on libadwaita 1.5 in an
        1100x700 window: with the sidebar showing as an overlay it is allocated
        the whole 1100px and the window's own toggle reports `get_mapped() ==
        False`. That is the "expand the sidebar and there is no button to retract
        it" report - F9 worked, and so did the "Conversation" row, but both are
        things a person has to already know about, and neither is on screen.

        So this bar is shown **only** while the sidebar is an overlay, which is
        the only state where it is needed and the only state where the window's
        toggle is unreachable. In the column layout it stays hidden, because that
        is exactly the duplicate title bar and duplicate back arrow this file's
        constructor comment explains at length.

        The title is "Panels" rather than "Chronoa" so a drawer does not read as
        a second, differently-named window.
        """
        bar = Adw.HeaderBar()
        self._drawer_close = Gtk.Button(
            icon_name="window-close-symbolic")
        self._drawer_close.add_css_class("flat")
        self._drawer_close.set_tooltip_text("Close the panel list (F9)")
        self._drawer_close.update_property(
            [Gtk.AccessibleProperty.LABEL], ["Close the panel list"])
        self._drawer_close.connect(
            "clicked", lambda _b: on_close() if on_close else None)
        bar.pack_start(self._drawer_close)
        title = Adw.WindowTitle(title="Panels")
        bar.set_title_widget(title)
        bar.set_visible(False)
        self._drawer_bar = bar
        return bar

    def set_drawer_mode(self, on: bool) -> None:
        """Show the close bar exactly while the sidebar is an overlay.

        Driven by `window._sync_sidebar_toggle`, which reads the split view, so
        this cannot claim to be a drawer when the sidebar is a column - the
        failure the constructor comment is all about. Setting `visible` on a
        `Gtk.Widget` also stops it being mapped, so `on` is really "am I an
        overlay covering the content", which is the question.
        """
        bar = getattr(self, "_drawer_bar", None)
        if bar is not None:
            bar.set_visible(bool(on))

    def select(self, key: Optional[str]) -> None:
        """Mark the open panel, so the sidebar says where you are.

        With rows spread over several `Adw.PreferencesGroup`s there is no
        single-selection list to select from, so "where am I" is the `selected`
        CSS class on the row itself - which is also what makes it visible when
        the panel is tall and the list is scrolled.

        `None` means the conversation is open, and the conversation has a row of
        its own for exactly that reason: `select(None)` used to unmark every
        panel and stop, which on the state the window opens in read as "nothing
        is selected".
        """
        for name, (row, _title, _icon, _section) in self._surface_rows.items():
            if name == key:
                row.add_css_class("selected")
            else:
                row.remove_css_class("selected")
        if key is None and self._chat_row is not None:
            self._chat_row.add_css_class("selected")
        elif self._chat_row is not None:
            self._chat_row.remove_css_class("selected")


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

        **The returned breakpoint must still be given to the window**
        (`window.add_breakpoint(...)`). Constructing one binds nothing: libadwaita
        only evaluates breakpoints registered with an `AdwWindow`. This function
        returned its breakpoint and `window.py` discarded it, so the sidebar
        never collapsed at any width - measured at 600px, where the conversation
        was squeezed to a 150px column between two sidebars rather than the
        sidebar becoming a drawer. That is this repo's own dead-control class
        wearing a responsive-design costume.
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