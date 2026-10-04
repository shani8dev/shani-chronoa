"""Shared Adwaita scaffolding for the surfaces.

Every panel in the sidebar is the same shape: a title, some groups of rows, and
a way to say "there is nothing here yet" that is not an empty rectangle. Writing
that once is the difference between a sidebar of eight widgets that look like
they belong together and eight that do not.

**Adw, but never at the cost of opening.** `Adw.init()` has to run before any
Adw widget exists and libadwaita may be absent from a headless builder, so the
import here is guarded and every helper has a plain-GTK answer. A surface that
degrades is a panel that looks plain; a surface that raises takes the window
with it, and the window is the product.

The rows are `Adw.PreferencesGroup` / `Adw.ActionRow` / `Adw.SwitchRow` because
those carry the accessibility, focus order and keyboard behaviour that a
hand-built `Gtk.Box` of labels does not - a screen reader announces an
`ActionRow`'s title, subtitle and target as one control, and announces a box of
labels as three unrelated strings.
"""

from __future__ import annotations

from typing import Callable, List, Optional

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")

from gi.repository import Adw, Gtk  # type: ignore

#: Adwaita needs initialising before a single Adw widget is constructed, and it
#: is idempotent, so it happens at import time rather than in every surface's
#: constructor. The settings window has always done this for its own widgets;
#: doing it here means a surface built first (which is what happens in a test)
#: cannot be the one that breaks.
_ADW_INITIALISED = False


def adw_ready() -> bool:
    """True when libadwaita is importable *and* initialised.

    Surfaces that want the modern rows call this; the ones that do not still
    build, in plain GTK.
    """
    global _ADW_INITIALISED
    if not _ADW_INITIALISED:
        try:
            Adw.init()
        except Exception:
            return False
        _ADW_INITIALISED = True
    return True


def surface(title: str, subtitle: str = "",
            back: Optional[Callable[[], None]] = None
            ) -> "tuple[Gtk.Widget, Callable[[Gtk.Widget], None]]":
    """A titled, scrollable page and the function that puts a child in it.

    Returns `(page, set_content)`. The two-step exists because the first thing a
    surface builds is usually its toolbar: with libadwaita that is a
    `ToolbarView` whose content slot is filled later, and without it a
    `ScrolledWindow` that a caller can wrap in anything.
    """
    if adw_ready():
        toolbar = Adw.ToolbarView()
        header = Adw.HeaderBar()
        if title:
            header.set_title_widget(Gtk.Label(label=title))
        if back is not None:
            # A panel is pushed onto the content view, so it needs a way back that
            # does not involve the sidebar - which on a narrow window is a drawer
            # that is collapsed. The button is the panel's own `Adw.HeaderBar`,
            # which libadwaita renders as a back arrow with the title beside it.
            up = Gtk.Button()
            up.set_icon_name("go-previous-symbolic")
            up.add_css_class("flat")
            up.set_tooltip_text("Back to the conversation")
            up.update_property([Gtk.AccessibleProperty.LABEL], ["Back to the conversation"])
            up.connect("clicked", lambda _b: back())
            header.pack_start(up)
        toolbar.add_top_bar(header)
        if subtitle:
            # Built before the page, not re-parented into it: giving an
            # `Adw.NavigationPage` its child twice (toolbar, then a box holding
            # the toolbar) trips `gtk_box_append`'s "child already has a parent"
            # assertion and leaves the page blank with a warning on stderr.
            note = Gtk.Label(label=subtitle)
            note.add_css_class("dim-label")
            note.set_wrap(True)
            note.set_margin_top(4)
            note.set_margin_start(12)
            note.set_margin_end(12)
            stack = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
            stack.append(note)
            stack.append(toolbar)
            page = Adw.NavigationPage(child=stack, title=title)
        else:
            page = Adw.NavigationPage(child=toolbar, title=title)

        def set_content(child: Gtk.Widget) -> None:
            toolbar.set_content(child)

        return page, set_content

    box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
    label = Gtk.Label(label=title)
    label.add_css_class("title-3")
    label.set_margin_top(12)
    label.set_margin_bottom(6)
    label.set_margin_start(12)
    label.set_margin_end(12)
    box.append(label)
    scroller = Gtk.ScrolledWindow(vexpand=True)

    def set_content(child: Gtk.Widget) -> None:
        scroller.set_child(child)
        box.append(scroller)

    return box, set_content


def group(title: str = "", description: str = "") -> Gtk.Widget:
    """A titled group of rows, or a plain box when Adw is not there."""
    if adw_ready():
        widget = Adw.PreferencesGroup(title=title)
        if description:
            widget.set_description(description)
        return widget
    box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
    box.set_margin_top(12)
    box.set_margin_start(12)
    box.set_margin_end(12)
    if title:
        heading = Gtk.Label(label=title)
        heading.add_css_class("heading")
        box.append(heading)
    if description:
        note = Gtk.Label(label=description)
        note.add_css_class("dim-label")
        note.set_wrap(True)
        box.append(note)
    return box


def row(title: str, subtitle: str = "", suffix: Optional[Gtk.Widget] = None,
        activatable: bool = False) -> Gtk.Widget:
    """One row: a title, an optional explanation, an optional control.

    A model-supplied title goes through `set_markup` after escaping; the
    subtitle goes through `set_text`, which takes no markup at all. That
    asymmetry is deliberate - it is the cheapest way to be certain a description
    from a sense module cannot become a tag.
    """
    if adw_ready():
        widget = Adw.ActionRow(title=title)
        if subtitle:
            widget.set_subtitle(subtitle)
        if suffix is not None:
            widget.add_suffix(suffix)
        if activatable:
            widget.set_activatable(True)
        return widget
    box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=12)
    box.set_margin_top(6)
    box.set_margin_bottom(6)
    text = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
    name = Gtk.Label(label=title)
    name.set_xalign(0.0)
    text.append(name)
    if subtitle:
        detail = Gtk.Label(label=subtitle)
        detail.set_xalign(0.0)
        detail.set_wrap(True)
        detail.add_css_class("dim-label")
        text.append(detail)
    box.append(text)
    if suffix is not None:
        box.append(suffix)
    return box


def switch_row(title: str, subtitle: str = "", active: bool = False,
               on_changed: Optional[Callable[[bool], None]] = None) -> Gtk.Widget:
    """A row whose suffix is a switch, wired to `on_changed`."""
    toggle = Gtk.Switch(valign=Gtk.Align.CENTER)
    if on_changed is not None:
        toggle.connect("notify::active", lambda _s, _p: on_changed(toggle.get_active()))
    widget = row(title, subtitle, suffix=toggle)
    if adw_ready() and isinstance(widget, Adw.ActionRow):
        widget.set_activatable_widget(toggle)
    widget.update_property([Gtk.AccessibleProperty.LABEL], [title])
    return widget


def empty_state(icon: str, title: str, description: str = "",
                child: Optional[Gtk.Widget] = None) -> Gtk.Widget:
    """"There is nothing here", said as a page rather than an empty rectangle.

    An empty list with no explanation reads as a bug - this repo has shipped
    surfaces that were exactly that - so the empty case always says what is
    absent and, where it is knowable, why.
    """
    if adw_ready():
        page = Adw.StatusPage(icon_name=icon, title=title, description=description)
        if child is not None:
            page.set_child(child)
        return page
    box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
    box.set_valign(Gtk.Align.CENTER)
    box.set_vexpand(True)
    box.set_margin_top(36)
    box.set_margin_bottom(36)
    image = Gtk.Image.new_from_icon_name(icon)
    image.set_pixel_size(64)
    box.append(image)
    heading = Gtk.Label(label=title)
    heading.add_css_class("title-4")
    box.append(heading)
    if description:
        note = Gtk.Label(label=description)
        note.set_wrap(True)
        note.set_justify(Gtk.Justification.CENTER)
        note.add_css_class("dim-label")
        box.append(note)
    if child is not None:
        box.append(child)
    return box


def banner(text: str, button_label: str = "",
           on_button: Optional[Callable[[], None]] = None) -> Gtk.Widget:
    """A one-line notice above the content, with an optional action.

    Used where something is true and worth saying *before* the user acts on it -
    background mode is off because they never turned it on, a panel is showing
    degraded readings because a probe failed. A status line below the thing it
    describes is a footnote nobody reads.
    """
    if adw_ready():
        widget = Adw.Banner(title=text)
        if button_label and on_button is not None:
            widget.add_button(button_label, on_button)
        return widget
    box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
    box.add_css_class("toolbar-view")
    label = Gtk.Label(label=text)
    label.set_wrap(True)
    label.set_hexpand(True)
    label.set_margin_start(12)
    label.set_margin_end(12)
    box.append(label)
    if button_label and on_button is not None:
        button = Gtk.Button(label=button_label)
        button.connect("clicked", lambda _b: on_button())
        box.append(button)
    return box


def monospace(text: str, selectable: bool = True) -> Gtk.Widget:
    """A fixed-width block - a log line, a path, a command.

    `Gtk.Label` has no `monospace` property on GTK 4.14 (`find_property("monospace")`
    is None), so this is the app's own `.reply-code` class, which is what a code
    block in a reply already uses.
    """
    label = Gtk.Label()
    label.set_markup(f"<tt>{_escape(text)}</tt>")
    label.add_css_class("reply-code")
    label.set_selectable(selectable)
    label.set_xalign(0.0)
    label.set_wrap(True)
    return label


def _escape(text: str) -> str:
    from shani_chronoa import markdown_lite
    return markdown_lite.escape(str(text))


def search_entry(placeholder: str = "Search",
                 on_changed: Optional[Callable[[str], None]] = None) -> Gtk.Widget:
    """A search field wired to `on_changed`.

    The debounce matters and is not obvious: `Gtk.SearchEntry` fires
    `search-changed` 150 ms after the last keystroke, and `set_text()` emits
    neither signal. A caller that filters in the handler therefore sees the
    *previous* query on a programmatic set, which is how a test comes to pass for
    the wrong reason.
    """
    entry = Gtk.SearchEntry()
    entry.set_hexpand(True)
    if placeholder:
        entry.set_placeholder_text(placeholder)
    entry.update_property([Gtk.AccessibleProperty.LABEL], [placeholder or "Search"])
    if on_changed is not None:
        entry.connect("search-changed", lambda e: on_changed(e.get_text()))
    return entry


def scrolled(child: Gtk.Widget, max_content_height: int = 0) -> Gtk.Widget:
    window = Gtk.ScrolledWindow(vexpand=True)
    window.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
    if max_content_height:
        window.set_max_content_height(max_content_height)
    window.set_child(child)
    return window


def page_body(margin: int = 0) -> Gtk.Box:
    """The vertical box a surface puts its groups into."""
    box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=18)
    if margin:
        box.set_margin_top(margin)
        box.set_margin_bottom(margin)
    return box


def rows_of(container: Gtk.Widget) -> List[Gtk.Widget]:
    """Every row-like child of a group, in order.

    `Adw.PreferencesGroup` is a `Gtk.ListBox`, so its rows are its children; a
    plain box built by `group()` holds whatever was appended. One helper for
    both, because a surface's tests should not have to know which one it got.
    """
    out: List[Gtk.Widget] = []
    child = container.get_first_child()
    while child is not None:
        out.append(child)
        child = child.get_next_sibling()
    return out