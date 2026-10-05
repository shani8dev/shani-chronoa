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


#: A wrapping label with no width cap is a page-width bug.
#:
#: `Gtk.Label.set_wrap(True)` wraps the text *at whatever width it is given*, and
#: reports its natural width as the full unwrapped run - measured on the setup
#: wizard's page description: 947px of text in a 560px window. So the page grew to
#: 1,466px, `Adw.NavigationPage`'s own scroller (whose horizontal policy is the
#: default) put a horizontal scrollbar under it, and that grey bar appeared at
#: the bottom of **every** wizard screenshot. `set_max_width_chars` is what bounds
#: it: the same label measures 437px capped at 56 characters.
#:
#: Sixty-odd characters is roughly a comfortable measure on a desktop panel and
#: narrow enough to fit a 480px window, which is where the measurement that set
#: it was taken.
MAX_WRAP_CHARS = 56


def wrap_label(label: "Gtk.Label", chars: int = MAX_WRAP_CHARS) -> "Gtk.Label":
    """Let `label` wrap *and* stop it deciding how wide the page is."""
    label.set_wrap(True)
    label.set_max_width_chars(chars)
    return label


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
        # The back arrow is *not* built here.
        #
        # It used to be: a `Gtk.Button` with `go-previous-symbolic`, packed into
        # this panel's own `Adw.HeaderBar`. But `Adw.NavigationView` draws a back
        # button of its own into every page it pushes, so every panel had two -
        # and counting the buttons in a rendered window found **four**, because
        # the window's conversation page and the pushed panel each carry a pair.
        # Two arrows side by side, both meaning "back", is worse than one: it
        # looks like two different destinations and neither is labelled.
        #
        # `Adw.NavigationView` owns the affordance and pops its own stack, so
        # letting it is both fewer widgets and the same behaviour. `_back` is
        # still assigned below, and still late-bound: `window.py` sets it after
        # the page exists, and it is what a test or another caller can invoke to
        # go back without a click.
        toolbar.add_top_bar(header)
        if subtitle:
            # Inside the toolbar's content, not above it.
            #
            # It used to be appended to a box wrapping the whole `ToolbarView`, so
            # it rendered *above* the panel's `HeaderBar` - which read as the
            # window's own title and pushed the real title down a line. A
            # subtitle that looks like the title is worse than no subtitle: a
            # person reads it for the window's name.
            note = Gtk.Label(label=subtitle)
            note.add_css_class("dim-label")
            note.add_css_class("surface-subtitle")
            wrap_label(note)
            note.set_margin_top(4)
            note.set_margin_start(12)
            note.set_margin_end(12)
            page = Adw.NavigationPage(child=toolbar, title=title)
            # The toolbar's content slot is filled by `set_content` later, so the
            # subtitle goes in a wrapper around whatever that is - and the wrapper
            # goes into the content slot now. Setting the content twice is a
            # replacement, not an append, so the wrapper would be gone; hence the
            # indirection: `set_content` puts the caller's box *inside* the
            # wrapper rather than into the toolbar.
            shell = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
            shell.append(note)
            shell._inner = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
            shell.append(shell._inner)
            toolbar.set_content(shell)
        else:
            shell = None
            page = Adw.NavigationPage(child=toolbar, title=title)

        #: `window.py` assigns the real callback when it pushes the page. The
        #: click reads it *then*, so a panel built by a test and never pushed is
        #: simply inert rather than raising.
        page._back = back

        def _go_back() -> None:
            """Invoke the late-bound back callback, if one was ever set.

            No button is wired to this any more - see above - but it stays
            callable because `window.py` assigns `page._back` and anything
            holding a page can use it to go back without synthesising a click.
            """
            callback = getattr(page, "_back", None)
            if callable(callback):
                callback()

        page._go_back = _go_back

        def set_content(child: Gtk.Widget) -> None:
            inner = getattr(shell, "_inner", None)
            if inner is not None:
                inner.append(child)
            else:
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


#: The three states a panel's own health can be in. The same closed vocabulary
#: `diagnostics.py` uses for its rows, deliberately: a panel that says "could not
#: determine" about its own summary and "working" about a subsystem is describing
#: two different questions, and one word for both would hide which is which.
STATUS_OK = "ok"
STATUS_ATTENTION = "attention"
STATUS_UNKNOWN = "unknown"

#: Words shown for each state, and the CSS class that colours the dot beside
#: them. The class names match the diagnostics row classes on purpose - the same
#: green, the same red, the same amber - so the whole app has one visual
#: language for health rather than one per panel.
STATUS_WORDS = {
    STATUS_OK: "Ready",
    STATUS_ATTENTION: "Needs attention",
    STATUS_UNKNOWN: "Could not determine",
}

STATUS_CLASSES = {
    STATUS_OK: "status-ok",
    STATUS_ATTENTION: "status-attention",
    STATUS_UNKNOWN: "status-unknown",
}

#: Markers a test finds in the built tree, so an assertion reads what was built
#: rather than a value the caller stashed on itself. Kept in one place because
#: they are the contract between this helper and every test that checks a panel
#: says something about its own state.
STATUS_ROW_CSS = "status-row"
STATUS_WORD_CSS = "status-word"


def status_row(status: str, summary: str, detail: str = "") -> Gtk.Widget:
    """One row that says whether this panel is healthy, before its contents.

    **The problem this exists to solve.** Every panel rendered every row the
    same shade of grey whether it was granted, absent or broken, so the app read
    as uniformly fine whether it was working or not. A person had to read prose
    in every row to find out, and the prose is honest but long - the only health
    signal the app had was a sentence, in body-text grey, which is not a signal at
    all when everything else is also grey.

    So: a coloured dot, a fixed word, and the sentence that explains it. The word
    is short on purpose and comes first, so a panel can be scanned down its left
    edge instead of read. `summary` carries the count ("2 of 16 senses
    degraded"); `detail` carries why, and is optional because a panel with
    nothing to add should not invent a sentence to fill the space.

    The status is a *closed* vocabulary, checked here rather than trusted: a
    caller passing a fourth word would silently produce a dot with no colour,
    which is the one outcome this row exists to prevent.
    """
    if status not in STATUS_WORDS:
        raise ValueError(
            f"unknown status {status!r}; the vocabulary is "
            f"{sorted(STATUS_WORDS)} and a fourth word would render an "
            "uncoloured dot, which is the failure this row exists to prevent"
        )
    word = STATUS_WORDS[status]
    spoken = summary if not detail else f"{summary} - {detail}"

    if adw_ready():
        row = Adw.ActionRow()
        row.set_title(word)
        row.set_subtitle(spoken)
        row.add_css_class(STATUS_ROW_CSS)
        row.add_css_class(STATUS_CLASSES[status])
        dot = Gtk.Label(label="")
        dot.add_css_class("status-dot")
        dot.set_valign(Gtk.Align.CENTER)
        row.add_prefix(dot)
        label = _first_label(row)
        if label is not None:
            label.add_css_class(STATUS_WORD_CSS)
        return row

    box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
    box.add_css_class(STATUS_ROW_CSS)
    box.add_css_class(STATUS_CLASSES[status])
    dot = Gtk.Label(label="")
    dot.add_css_class("status-dot")
    box.append(dot)
    text = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
    head = Gtk.Label(label=word, xalign=0.0)
    head.add_css_class(STATUS_WORD_CSS)
    text.append(head)
    note = Gtk.Label(label=spoken, xalign=0.0, wrap=True)
    wrap_label(note)
    note.add_css_class("dim-label")
    text.append(note)
    box.append(text)
    return box


def _first_label(widget: Gtk.Widget) -> "Optional[Gtk.Label]":
    """The first `Gtk.Label` in a widget's subtree, or None."""
    if isinstance(widget, Gtk.Label):
        return widget
    child = widget.get_first_child()
    while child is not None:
        found = _first_label(child)
        if found is not None:
            return found
        child = child.get_next_sibling()
    return None


def key_values(text: str) -> Gtk.Widget:
    """Render a multi-line reading as a two-column list rather than a paragraph.

    **Why.** Senses return tabular text - `power` answers with a charge
    percentage, a wear figure, a cycle count, a chemistry and whether mains is
    online, one per line, with the keys in a `key: value` shape. Handed to a
    `Gtk.Label` that is six wrapped lines in a narrow column, all the same
    weight, so the one number a person came for (`98.0%`) has to be found by
    reading. Aligned in two columns it is scannable down the left edge.

    **What it refuses to do.** It only re-lays-out lines that are *already*
    `key: value` or indented - it does not guess at meaning, split on the first
    colon in a sentence, or reorder anything. A line it cannot classify is
    emitted as its own full-width row, in the order it arrived. Reformatting data
    this app did not produce is how a display ends up quietly disagreeing with the
    reading it claims to show.

    The key column is fixed-width and right-aligned so the values line up; the
    value column takes the rest and wraps, because a long path is not going to
    fit on one line whatever else is done.
    """
    box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
    box.add_css_class("key-value-grid")
    for line in str(text or "").splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        key, value, aligned = _split_key_value(line, stripped)
        row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=12)
        row.add_css_class("key-value-row")
        if aligned:
            name = Gtk.Label(label=key, xalign=1.0)
            name.add_css_class("key-value-key")
            name.set_yalign(Gtk.Align.START)
            row.append(name)
            text_label = Gtk.Label(label=value, xalign=0.0, wrap=True, hexpand=True)
            text_label.add_css_class("key-value-text")
            row.append(text_label)
        else:
            # Not a pair: keep the line whole rather than inventing a split.
            text_label = Gtk.Label(label=stripped, xalign=0.0, wrap=True, hexpand=True)
            text_label.add_css_class("key-value-text")
            row.append(text_label)
        box.append(row)
    return box


def _split_key_value(line: str, stripped: str) -> "tuple[str, str, bool]":
    """Pull `key` and `value` out of one reading line, if it really is a pair.

    Three shapes are recognised, and nothing else: a `key: value` pair, an
    indented line (which is a continuation of the pair above it, so it is given
    the empty key and the value column), and anything at all - which comes back
    marked `aligned=False` so the caller emits it full width.
    """
    indented = line[:1] in (" ", "\t")
    if indented:
        return "", stripped, True
    head, sep, tail = stripped.partition(":")
    if not sep or not head.strip() or " " in head.strip():
        # No colon, an empty key, or a key with a space in it - that is a
        # sentence, not a field, and splitting it would invent a column.
        return "", stripped, False
    if not tail.strip():
        return head.strip(), "", True
    return head.strip(), tail.strip(), True


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
        wrap_label(note)
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
        wrap_label(detail)
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
        wrap_label(note)
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

    **The banner is revealed here, not by the caller.** `Adw.Banner` starts with
    `revealed == False` and draws nothing until something sets it, so a caller
    that appends one and stops puts the notice in the widget tree and nowhere
    else - while every assertion about its *text* still passes. Measured on this
    tree before this line existed: `diagnostics` (the "N of M subsystems
    working" headline) and `memory` (the privacy note) both built a banner and
    both rendered nothing; only `machine`, which had its own local `_revealed()`,
    was correct. The helper that every caller already goes through is the right
    place for this, so a new panel cannot repeat the mistake by omission.

    **A button is not built on the Adw path, because `Adw.Banner` cannot hold
    one.** Measured on the installed libadwaita 1.5: `Adw.Banner` exposes
    `set_button_label`/`get_button_label`, `add_button` does not exist, and the
    class has *no* signals at all (`GObject.signal_list_names(Adw.Banner)` is
    empty) - so there is nothing to click and nothing to connect. Calling
    `add_button` raised `AttributeError` and took the whole panel's build down
    with it. That was invisible for as long as it stayed unused, because every
    caller passes a bare string.

    So a caller that asks for a button gets the notice as a `Gtk.Box` holding
    the label and a real `Gtk.Button`, rather than an `Adw.Banner` that cannot
    hold it. The plain-GTK branch below already built exactly that, so the two
    paths now share it and the styling matches the non-Adw fallback's
    `.toolbar-view`.
    """
    if adw_ready() and not (button_label and on_button is not None):
        widget = Adw.Banner(title=text)
        widget.set_revealed(True)
        return widget
    box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
    box.add_css_class("toolbar-view")
    label = Gtk.Label(label=text)
    wrap_label(label)
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
    wrap_label(label)
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