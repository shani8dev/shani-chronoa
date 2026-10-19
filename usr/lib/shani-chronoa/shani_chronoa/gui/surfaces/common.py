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

import logging
import re
import textwrap
from typing import Any, Callable, Optional

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
gi.require_version("Pango", "1.0")

from gi.repository import Adw, GLib, Gtk, Pango  # type: ignore

logger = logging.getLogger(__name__)

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
    """Let `label` wrap *and* stop it deciding how wide the page is.

    Also pre-wraps the existing text with real newlines, because of a mismatch
    this machine measured in GTK4 on 2026-10-07: with `wrap=True` and
    `max_width_chars` the *height-for-width* measure uses the allocated width,
    while the paint pass breaks lines at `max_width_chars`. At window width the
    subtitle therefore painted three lines into about two lines of allocation,
    overlapping the banner beneath it. Pre-broken text makes the measure and
    the painted lines agree regardless of window width.
    """
    label.set_wrap(True)
    label.set_max_width_chars(chars)
    text = label.get_label()
    if text and "\\n" not in text:
        label.set_label("\\n".join(
            textwrap.fill(paragraph, width=chars)
            for paragraph in text.split("\\n\\n")))
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


#: Widest a panel's content is laid out, in pixels. Measured on the real
#: window at 1000px: rows ran the full width while the subtitle above them
#: wrapped at ~270px, so the page had two left edges' worth of line lengths
#: and status sentences 900px long. Centred and capped, every panel reads at one
#: measure; below the cap nothing changes.
PANEL_READING_PX = 820


def _clamped(child: Gtk.Widget, width: int = PANEL_READING_PX) -> Gtk.Widget:
    clamp = Adw.Clamp(maximum_size=width, tightening_threshold=width)
    clamp.set_child(child)
    return clamp


def surface(title: str, subtitle: str = "",
            back: Optional[Callable[[], None]] = None,
            max_width: int = PANEL_READING_PX,
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
            # Left, on the same edge as the content under it. A capped label
            # keeps GTK's default xalign of 0.5, so every panel opened with a
            # narrow centred paragraph floating over left-aligned rows.
            note.set_xalign(0.0)
            note.set_halign(Gtk.Align.START)
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
            toolbar.set_content(_clamped(shell, max_width))
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
                toolbar.set_content(_clamped(child, max_width))

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
#: Switched off **by a choice**, not by a fault: a consent key left at its
#: default, a background unit nobody enabled. Before this existed the only
#: words were the three above, so every panel whose gate was shut said "Needs
#: attention" in red - and the Devices panel printed "This is a setting on this
#: machine, not a fault." directly under that red word. Five of the sidebar's
#: rows were red on a healthy machine with default settings; red that is always
#: on stops meaning anything, which costs the rows where it is true. Grey, not
#: amber: amber already means "could not tell", and this panel could.
STATUS_OFF = "off"
#: And an empty store that was read without error is `STATUS_OK`, not
#: `STATUS_UNKNOWN`: "no tool calls yet", "no rules armed" and "nothing to
#: compare" are answers. Amber is for a panel that could not find out, and
#: five panels wore it for "nothing here yet" (rendered, 2026-10-08).

#: Words shown for each state, and the CSS class that colours the dot beside
#: them. The class names match the diagnostics row classes on purpose - the same
#: green, the same red, the same amber - so the whole app has one visual
#: language for health rather than one per panel.
STATUS_WORDS = {
    STATUS_OK: "Ready",
    STATUS_ATTENTION: "Needs attention",
    STATUS_UNKNOWN: "Could not determine",
    STATUS_OFF: "Turned off",
}

STATUS_CLASSES = {
    STATUS_OK: "status-ok",
    STATUS_ATTENTION: "status-attention",
    STATUS_UNKNOWN: "status-unknown",
    STATUS_OFF: "status-off",
}

#: Markers a test finds in the built tree, so an assertion reads what was built
#: rather than a value the caller stashed on itself. Kept in one place because
#: they are the contract between this helper and every test that checks a panel
#: says something about its own state.
STATUS_ROW_CSS = "status-row"
STATUS_WORD_CSS = "status-word"

#: The dot's diameter, and the one place it is decided.
#:
#: It has to be a `Gtk.DrawingArea` and not a `Gtk.Label`. An empty label
#: measures to its *font's* line height - 18px against a 10px `min-height` on
#: this machine - and GTK4's CSS has no `width`/`height` to correct that with,
#: so the 5px `border-radius` on an 18px-tall box paints a rounded rectangle and
#: not a dot. Measured on a rendered panel, the dot beside "Needs attention" was
#: a square; measured on the sidebar rows, it was 10 x 18. Both were the same
#: one-line mistake.
#:
#: Half of `.status-dot`'s `border-radius`, so the radius closes the circle.
STATUS_DOT_PX = 10


def status_dot() -> "Gtk.DrawingArea":
    """The coloured dot `.status-dot` paints. One implementation, two callers.

    `status_row()` built its own on the libadwaita path and another on the plain
    one, and `sidebar.py` built a third; all three were `Gtk.Label`s, so all
    three rendered as squares. It is kept here rather than in `style.py` because
    the size is a widget property - GTK4's CSS cannot express it.
    """
    dot = Gtk.DrawingArea()
    dot.set_content_width(STATUS_DOT_PX)
    dot.set_content_height(STATUS_DOT_PX)
    dot.add_css_class("status-dot")
    dot.set_valign(Gtk.Align.CENTER)
    return dot


class StatusRecorder:
    """One surface's own health, made once and shown in two places.

    The row inside the panel and the dot on its sidebar row are the same
    statement about the same data, so they are made once here rather than
    computed twice. `row()` builds the visible row and remembers the word;
    `status()` answers for the sidebar, via `window._panel_status`.

    **Why this exists rather than twenty hand-written `status()` closures.** A
    closure per surface would have to re-derive the answer from whatever the
    surface had read - which is a second count, free to fall out of date, and
    `senses.py` already had to guard against exactly that by hand:

        # Computed from the same `entries` the rows were built from, so the dot
        # cannot disagree with the panel - there is no second count to fall out
        # of date.

    That is the right instinct and the wrong mechanism, because it only holds
    for the one surface that wrote it. Here the value is written on the way past
    and read on the way back, so there is nothing to keep in step.

    The initial value is `STATUS_UNKNOWN` rather than `STATUS_OK`: a panel that
    has not said anything has not said it is fine.
    """

    def __init__(self, initial: str = STATUS_UNKNOWN) -> None:
        if initial not in STATUS_WORDS:
            raise ValueError(
                f"unknown initial status {initial!r}; expected one of "
                f"{sorted(STATUS_WORDS)}"
            )
        self._status = initial

    def row(self, status: str, summary: str, detail: str = "") -> Gtk.Widget:
        """Build the panel's status row *and* record the word it stands for."""
        self._status = status
        return status_row(status, summary, detail)

    def set(self, status: str) -> str:
        """Record a status without building a row, for a surface that has one already."""
        if status not in STATUS_WORDS:
            raise ValueError(
                f"unknown status {status!r}; expected one of {sorted(STATUS_WORDS)}"
            )
        self._status = status
        return status

    def status(self) -> str:
        """The recorded word, for `window._panel_status` to read."""
        return self._status


def home_paths(text: Any) -> str:
    """`text` with this person's home folder written `~` (MAIN-UI-TODO 10h).

    `/home/<name>/.config/shani-chronoa/triggers.json` is a line of chrome
    around the one word that matters; `~/.config/shani-chronoa/triggers.json`
    is what a person types. Only a whole leading folder is replaced -
    `/home/<name>2/` is somebody else's. The rows that shorten a path keep the
    full one as their tooltip, so nothing is lost.
    """
    import os
    import re as _re
    text = "" if text is None else str(text)
    home = os.path.expanduser("~").rstrip("/")
    if not home or home == "/" or home not in text:
        return text
    return _re.sub(_re.escape(home) + r"(?=/|$|[\s'\"),.;:])", "~", text)


def _hover_full(widget: Gtk.Widget, *texts: str) -> None:
    """The unshortened text as `widget`'s tooltip, when `home_paths` changed it."""
    full = " - ".join(t for t in texts if t)
    if full and home_paths(full) != full and not widget.get_tooltip_text():
        widget.set_tooltip_text(full)


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
    full = summary if not detail else f"{summary} - {detail}"
    spoken = home_paths(full)

    if adw_ready():
        row = Adw.ActionRow()
        row.set_title(word)
        row.set_subtitle(spoken)
        row.add_css_class(STATUS_ROW_CSS)
        row.add_css_class(STATUS_CLASSES[status])
        dot = status_dot()
        row.add_prefix(dot)
        label = _first_label(row)
        if label is not None:
            label.add_css_class(STATUS_WORD_CSS)
        _hover_full(row, full)
        return row

    box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
    box.add_css_class(STATUS_ROW_CSS)
    box.add_css_class(STATUS_CLASSES[status])
    dot = status_dot()
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

    **The value column also ellipsises, and that is not tidiness.** Measured on
    this GTK: a `wrap=True` label with `set_max_width_chars` set still reports a
    **511px minimum** for a line carrying a 64-character unbreakable digest,
    because a token with no break opportunity cannot be wrapped - and that one
    label's minimum became the whole `Disks and filesystems` group's 523px and
    then the panel's, breaking the 480px contract. Adding
    `ellipsize=Pango.EllipsizeMode.MIDDLE` takes the same label to a **15px**
    minimum. `MIDDLE` rather than `END` because the text here is paths and mount
    points, and both ends are what identifies them.
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
            row.append(_value_label(value))
        else:
            # Not a pair: keep the line whole rather than inventing a split.
            row.append(_value_label(stripped))
        box.append(row)
    return box


def _value_label(text: str) -> Gtk.Label:
    """One reading's value: wraps, and elides what wrapping cannot shorten.

    Split out because `key_values` builds this label in two branches, and a rule
    that has to be remembered twice is a rule one of them will forget.
    """
    label = Gtk.Label(label=text, xalign=0.0, wrap=True, hexpand=True)
    label.set_ellipsize(Pango.EllipsizeMode.MIDDLE)
    label.add_css_class("key-value-text")
    return label


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
        activatable: bool = False, escape: bool = True) -> Gtk.Widget:
    """One row: a title, an optional explanation, an optional control.

    **The title is escaped here, and this docstring already claimed it was.**
    Measured 2026-10-11: `Adw.ActionRow`'s title is Pango markup, so
    `set_title("weather & news")` fails to parse and logs *"Entity did not end
    with a semicolon"* - an ampersand is an ordinary character in a name somebody
    typed. Three surfaces had each hand-rolled `GLib.markup_escape_text` before
    calling this, which is the shape of a helper that does not do what its own
    contract says: one function, three workarounds, and every *new* caller left
    to find out for themselves. They no longer need to.

    The subtitle goes through `set_subtitle`, which is **also** Pango markup -
    measured, not assumed: `set_subtitle("a & b")` logs the same parse error. So
    both are escaped here, and the plain-GTK branch below escapes neither,
    because `Gtk.Label` takes no markup at all.

    **`escape=False` for a caller that switches markup off on the row.**
    `model.py` does exactly that, and escaping for it printed the entity - the
    same defect as escaping twice, in the other direction. The default stays
    `True`: escaping is the safe answer for every caller that leaves the row as
    libadwaita builds it, and the opt-out is named at the one site that needs it.
    """
    full_title, full_subtitle = title, subtitle
    title, subtitle = home_paths(title), home_paths(subtitle)
    if adw_ready():
        # `escape=False` is for a caller that switches markup off on the row
        # afterwards. Escaping is correct for markup that will be *parsed*;
        # escaping text that will be shown verbatim prints the entity to the
        # person, which is what happened to `model.py` - measured, a model
        # named `<b>x</b>` rendered as `&lt;b&gt;x&lt;/b&gt;`.
        widget = Adw.ActionRow(
            title=(GLib.markup_escape_text(title) if escape else title) if title else "",
            subtitle=(GLib.markup_escape_text(subtitle) if escape else subtitle) if subtitle else "")
        if suffix is not None:
            widget.add_suffix(suffix)
        if activatable:
            widget.set_activatable(True)
        _hover_full(widget, full_title, full_subtitle)
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
    """A row whose suffix is a switch, wired to `on_changed`.

    **`active` is applied, before the handler is connected.** It was accepted
    and never used, so every switch built here - 21 of them across the panels -
    showed off whatever the setting said (found 2026-10-15: the Sandbox page's
    low-priority switch read off while the setting was on). Set before
    connecting, so drawing the row does not write the setting back.
    """
    toggle = Gtk.Switch(valign=Gtk.Align.CENTER, active=bool(active))
    if on_changed is not None:
        toggle.connect("notify::active", lambda _s, _p: on_changed(toggle.get_active()))
    widget = row(title, subtitle, suffix=toggle)
    if adw_ready() and isinstance(widget, Adw.ActionRow):
        widget.set_activatable_widget(toggle)
    widget.update_property([Gtk.AccessibleProperty.LABEL], [title])
    return widget


def connect_button(app: Any, route: str = "", label: str = "Connect a device") -> Gtk.Button:
    """A button that opens Connect a device, on `route` if given.

    For the empty states that used to end at "nothing is paired": the page that
    can tell a device is missing offers the way to add one.
    """
    button = Gtk.Button(label=label, halign=Gtk.Align.CENTER)
    button.add_css_class("pill")
    button.add_css_class("suggested-action")

    def clicked(*_a) -> None:
        activate = getattr(app, "activate_action", None)
        if callable(activate):
            activate("connect-device", GLib.Variant("s", route))
    button.connect("clicked", clicked)
    return button


def empty_state(icon: str, title: str, description: str = "",
                child: Optional[Gtk.Widget] = None,
                *, inline: bool = False) -> Gtk.Widget:
    """"There is nothing here", said as a page rather than an empty rectangle.

    An empty list with no explanation reads as a bug - this repo has shipped
    surfaces that were exactly that - so the empty case always says what is
    absent and, where it is knowable, why.

    **`inline=True` builds the squeezable box instead of an `Adw.StatusPage`,
    and the caller has to say which it is.** Measured 2026-10-13:

        Adw.StatusPage declares a MINIMUM height of 301px
        (and a minimum width of 152px), whatever height it is offered

    As a whole page that is right, and `goals` - which owns its page - renders
    the StatusPage exactly as intended: full icon, title, description, button.
    **Mounted inline** - inside a scrolled content box, between a banner and
    the rest of the panel - it is given far less than 301px and GTK clips it.
    Two panels did exactly that and both rendered **a 55px horizontal slice
    through the middle of the icon, with neither the title nor the
    description**:

    | panel | what was on screen instead of the sentence |
    |---|---|
    | `triggers` | a grey crescent, and **no "No trigger rules armed"** - on a panel whose entire content was that sentence |
    | `memory` | a clipped prohibition icon, and no title or description |

    The icon is drawn *centred* in the 301px it insists on, so the slice
    through the middle is a band across the icon rather than its top or
    bottom - which is why it reads as a stray shape rather than as a
    truncated one.

    The box below has no such floor: it sizes to its children, so the same
    icon, title and description survive in a band a tenth of that height.
    """
    if adw_ready() and not inline:
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
    # **The heading is wrapped for the same reason the description is.** It was
    # not, and that was invisible while this branch was only reached when Adw was
    # unavailable - `Adw.StatusPage` wraps its own title, so a caller moving to
    # the box got a title that ran off the edge instead. `test_long_labels_wrap_
    # or_ellipsise[memory]` caught it the moment a panel did.
    wrap_label(heading)
    box.append(heading)
    if description:
        note = Gtk.Label(label=description)
        wrap_label(note)
        note.set_justify(Gtk.Justification.CENTER)
        note.add_css_class("dim-label")
        box.append(note)
    if child is not None:
        box.append(child)
    # **Its own scroller, for the same reason.** `test_every_surface_can_scroll`
    # requires every surface to have one, and `memory` was passing only because
    # the `Adw.StatusPage` it no longer uses *contains* one internally. An
    # inline empty state is now a real thing in two panels, so it carries its
    # own rather than depending on a widget class for it.
    return scrolled(box)


def setup_route(app: Any, widget: "Gtk.Widget | None" = None) -> str:
    """Which way the setup wizard can be opened from here, or `''` for none.

    **One place, so a button and its click cannot disagree.** The button's
    sensitivity says whether Setup can be opened; the click opens it. Both must
    ask the same question, or a panel can offer an action and then do nothing
    - the dead end this is here to prevent.

    Three routes, in the order `open_setup` tries them:

    - `"app"` - the application object has the action.
    - `"window"` - the window's own `Gtk.Application` has it, which is what a
      panel built outside the app object reaches.
    - `"toast"` - no application is reachable, but a toast can at least say
      where the wizard *is*, which is better than silence.

    `''` means there is nowhere to go: a bare stub, as the tests build. A
    button in that state must say so rather than sit there looking live.
    """
    activate = getattr(app, "activate_action", None)
    if callable(activate):
        return "app"
    window = getattr(app, "window", None)
    gtk_app = window.get_application() if window is not None else None
    if gtk_app is not None and hasattr(gtk_app, "activate_action"):
        return "window"
    if widget is not None and adw_ready():
        if _toast_overlay(widget) is not None:
            return "toast"
    return ""


def open_setup(app: Any, widget: "Gtk.Widget | None" = None, page: str = "") -> None:
    """Open the setup wizard - the only thing in the app that installs a model.

    Routed through `app.activate_action("setup", None)`, the same door the header
    button and `Ctrl+Shift+S` use, which lands in `brain._open_setup`. Going
    through the action rather than calling the method keeps a panel from needing
    the object that owns the wizard, and keeps the shortcut, the header button
    and every panel's "not installed" button on a single path.

    **This is `models.py`'s `_open_setup`, lifted here** so the three panels that
    say "not installed" reach the wizard the same way. It used to live in one of
    them, and a second copy in a second module is the arrangement this repo keeps
    paying for: three panels, three routes, and no way to tell from the code that
    they are supposed to be the same door.

    The two fallbacks are the ones that were there: a stub app has no action, and
    the window may be able to name the real `Gtk.Application`. When neither
    answers, the click is reported as a toast rather than swallowed - a button
    that does nothing and says nothing is the dead end this whole change is
    about. `setup_route` is what decides that, so a button can ask the same
    question before offering the action.
    """
    # `page` lands on that model's page ("ears", "eyes", ...) through the
    # `setup-page` action; the tooltip on every "Set up" button promised it,
    # and this used to ignore it and open Welcome.
    name, value = ("setup-page", GLib.Variant("s", page)) if page else ("setup", None)
    route = setup_route(app, widget)
    if route == "app":
        app.activate_action(name, value)
        return
    if route == "window":
        window = getattr(app, "window", None)
        gtk_app = window.get_application() if window is not None else None
        gtk_app.activate_action(name, value)
        return
    if route == "toast":
        overlay = _toast_overlay(widget)
        if overlay is not None:
            overlay.add_toast(
                Adw.Toast.new("Set Chronoa up from the header, or Ctrl+Shift+S"))
            return
    logger.warning("no route to the setup wizard")


def model_banner(app: Any, kind: str, needed_for: str = "") -> "Optional[Gtk.Widget]":
    """A "not downloaded - Download" banner for model `kind`, or None when it is there.

    `kind` is a `model_status.KINDS` key ("stt", "vision", "memory", ...). The
    button opens Setup **on that model's page**, where its size and the one
    download consent are shown - Setup stays the only place that downloads.
    `needed_for` names what *this page* loses without it, when that is more
    specific than the kind's general sentence.
    """
    from shani_chronoa import model_status
    if model_status.installed(kind) is not False:
        return None
    info = model_status.KINDS[kind]
    why = needed_for or info.enables
    return banner(f"{info.label} is not downloaded yet, so Chronoa cannot {why}.",
                  "Download", lambda: open_setup(app, page=info.setup_page),
                  "Opens Setup on its page, where the download size is shown")


def with_model_banners(app: Any, content: Gtk.Widget, kinds) -> Gtk.Widget:
    """`content` with a Download banner above it for each missing model.

    `kinds` is a list of kind keys, or of `(kind, needed_for)` pairs naming
    what *this* page cannot do without it. Nothing missing returns `content`
    unchanged, so a machine with everything sees no extra chrome.
    """
    banners = []
    for item in kinds:
        kind, needed_for = item if isinstance(item, tuple) else (item, "")
        widget = model_banner(app, kind, needed_for)
        if widget is not None:
            banners.append(widget)
    if not banners:
        return content
    box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
    for widget in banners:
        widget.set_margin_top(6)
        box.append(widget)
    content.set_vexpand(True)
    box.append(content)
    return box


def _toast_overlay(widget: Gtk.Widget) -> "Adw.ToastOverlay | None":
    """The nearest `Adw.ToastOverlay` above `widget`, or None."""
    node: "Gtk.Widget | None" = widget
    while node is not None:
        if isinstance(node, Adw.ToastOverlay):
            return node
        node = node.get_parent()
    return None


def open_page(app: Any, target: str) -> None:
    """Navigate to a page of any window, through the one registry.

    **Every "turn it on in Settings" becomes a button, and this is what the
    button calls.** Those sentences were the most-cited dead end in this app: five
    panels said where the switch was without providing a way to reach it, so a
    person who had just been told what was wrong had to go and find it. Routing
    them through `pages.show` rather than each panel importing the settings window
    keeps one implementation - the same one `app.show_page`,
    `--show-page=settings:privacy` and a notification already use - so a panel
    cannot reach a page the rest of the app cannot, and a retired page id is
    resolved the same way for all of them.

    Returns nothing and raises nothing. A panel that cannot navigate - built
    against a stub app, or after the window has gone - must still build, and a
    button that raises inside a signal handler takes the panel down with it. So
    the failure is logged and the button is a no-op, which is the same reading
    the empty states give.
    """
    from shani_chronoa import pages

    try:
        reached = pages.show(target, application=app, config=getattr(app, "config", None))
    except Exception:  # noqa: BLE001 - a dead button must not take the panel down
        logger.warning("could not open %r from a panel", target, exc_info=True)
        return
    if not reached:
        logger.warning("no page at %r; the button had nowhere to go", target)


def open_settings_button(label: str, target: str) -> Gtk.Widget:
    """A button that opens `target` in another window, for a banner to hold.

    Takes the target rather than the app so it can be built by a module-level
    table - the states in `devices.py` and `calendar.py` are constants, and a
    closure over `app` would mean rebuilding them per panel.
    """
    button = Gtk.Button(label=label)
    button.add_css_class("suggested-action")
    button.set_tooltip_text(f"Open {target.split(':')[-1].replace('-', ' ')} in Settings")
    button.update_property(
        [Gtk.AccessibleProperty.LABEL],
        [f"{label}: opens the settings page it names"])
    return button


def wire_page_button(button: Gtk.Widget, app: Any, target: str) -> Gtk.Widget:
    """Connect an `open_settings_button` to the app it was built beside."""
    button.connect("clicked", lambda _b: open_page(app, target))
    return button


def consent_name(key: str) -> str:
    """The switch's name as Settings shows it ("Let Chronoa record a whole
    meeting"), or the key itself when no name is registered."""
    try:
        from shani_chronoa.capabilities import GATE_NAMES
    except Exception:  # noqa: BLE001 - a missing table must not break a panel
        return key
    return GATE_NAMES.get(key, key)


def consent_banner(app: Any, key: str, why: str,
                   on_change: Optional[Callable[[], None]] = None) -> Gtk.Widget:
    """"<switch> is off. <why>" with a **Turn on** button that turns it on here.

    **The panels printed the config key and stopped** - "Turn on
    'meeting-enabled' in Settings to allow it" - which names a setting by a word
    Settings never shows and then sends the person to find it. The switch's own
    label comes from `capabilities.GATE_NAMES`, the table the Settings rows are
    built from, and pressing the button is the same single, labelled act as
    flipping that switch: it writes the same key, through the same config.
    """
    name = consent_name(key)

    def turn_on() -> None:
        config = getattr(app, "config", None)
        if config is None:
            logger.warning("no config to turn on %s with", key)
            return
        try:
            config.set(key, "true")
        except Exception:  # noqa: BLE001 - a refused write must not take the panel down
            logger.warning("could not turn on %s", key, exc_info=True)
            return
        if on_change is not None:
            on_change()

    return banner(f"“{name}” is off. {why}", "Turn on", turn_on,
                  f"Turns on “{name}”, the same switch as in Settings")


def reload_surface(app: Any, name: str) -> None:
    """Rebuild panel `name` from scratch (after its switch was turned on)."""
    window = getattr(app, "window", None)
    reload = getattr(window, "reload_surface", None)
    if callable(reload):
        reload(name)


def gate_banner(app: Any, key: "Optional[str]", surface: str,
                label: str, target: str) -> Gtk.Widget:
    """The banner for a panel whose gate is shut.

    A consent switch (`key`) gets `consent_banner`: its Settings name and
    **Turn on**, then the panel rebuilds itself with the switch open. Anything
    else - a missing package, a setup step - keeps its route to the page that
    fixes it. Replaces "This is a setting on this machine, not a fault" over an
    "Open Privacy settings" button, which named no switch and sent the person
    away to find it.
    """
    if key:
        return consent_banner(app, key, "Nothing on this page was read while it is off.",
                              on_change=lambda: reload_surface(app, surface))
    return banner("This is a setting on this machine, not a fault.", label,
                  lambda: open_page(app, target))


def read_banner(on_reload: Callable[[], None]) -> Gtk.Widget:
    """"Read at 08:52 - not live. Reload for the latest." with its Reload.

    Replaces three 30-word versions of "read once, when this panel was built,
    nothing here is polled, press Reload to read X, Y and Z again": the time
    says *when* and "not live" says it will not update itself, which is all
    the long sentences were for. `restamp(banner)` after a reload.
    """
    widget = banner(_read_text(), "Reload", lambda: (on_reload(), restamp(widget)),
                    "Read everything on this page again")
    return widget


def _read_text() -> str:
    import time
    return f"Read at {time.strftime('%H:%M')} - not live. Reload for the latest."


def restamp(widget: Gtk.Widget) -> None:
    """Put the current time on a `read_banner`."""
    node = widget.get_first_child() if widget is not None else None
    while node is not None:
        if isinstance(node, Gtk.Label) and node.get_label().startswith("Read at "):
            node.set_label(_read_text())
            return
        node = node.get_next_sibling()


def banner(text: str, button_label: str = "",
           on_button: Optional[Callable[[], None]] = None,
           button_tooltip: str = "") -> Gtk.Widget:
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

    **A button is not built on the Adw path, because `Adw.Banner` has no signal
    to connect one to.** Measured on libadwaita 1.5: `Adw.Banner` exposes
    `set_button_label`/`get_button_label`, `add_button` does not exist, and the
    class has *no* signals at all (`GObject.signal_list_names(Adw.Banner)` is
    empty) - so there is nothing to click and nothing to connect. Calling
    `add_button` raised `AttributeError` and took the whole panel's build down
    with it. That was invisible for as long as it stayed unused, because every
    caller passes a bare string.

    **Re-measured on libadwaita 1.9.1, because this was going to be wrong
    eventually.** `Adw.Banner` has since grown a `button-style` property and an
    internal `Gtk.Button` at depth 3 (its own dismiss control), and
    `GObject.signal_list_names(Adw.Banner)` is still empty. So the label property
    would draw a button that cannot be pressed, and there is still no
    `button-clicked` to connect - `action-name` is the only hook, and routing
    through it would mean every caller registering a named action instead of
    passing a callable. The plain box below stays, and now a banner always
    carries at most one button of *ours* carrying a label, which is what
    `tests/test_reload_is_one_banner.py` asserts - rather than an empty tree,
    which libadwaita's own dismiss control would fail.

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
    # Rendered, the sentence sat centred in the middle of the row and the
    # button was flush against the window's right edge with no margin at all,
    # so the two read as unrelated and the button looked clipped. Sentence on
    # the left, button on the right, both inset by the same 12px.
    box.add_css_class("surface-banner")
    box.set_margin_start(12)
    box.set_margin_end(12)
    box.set_margin_top(6)
    box.set_margin_bottom(6)
    label = Gtk.Label(label=text)
    wrap_label(label)
    label.set_xalign(0.0)
    label.set_hexpand(True)
    label.set_margin_start(12)
    label.set_margin_end(12)
    box.append(label)
    if button_label and on_button is not None:
        button = Gtk.Button(label=button_label)
        button.set_valign(Gtk.Align.CENTER)
        button.set_margin_top(6)
        button.set_margin_bottom(6)
        button.set_margin_end(6)
        button.connect("clicked", lambda _b: on_button())
        # **Every banner button carries a tooltip and an accessible label**, and
        # both default to the banner's own sentence when the caller gives
        # neither. Two buttons reading "Reload" on adjacent panels say what they
        # reload only in a tooltip, and a text button with no tooltip is
        # indistinguishable from a label - which is how `voice.py`'s Reload came
        # to be the only control in the app that a screen reader announced as
        # "Reload" with nothing after it. Defaulting rather than leaving it empty
        # means a caller cannot forget: the reason the banner is up is the best
        # possible explanation of what its button does.
        tip = button_tooltip or f"{button_label}: {text}"
        button.set_tooltip_text(tip)
        button.update_property([Gtk.AccessibleProperty.LABEL], [tip])
        box.append(button)
    return box


#: A filesystem path as it appears inside a sentence.
#:
#: Deliberately narrow. It wants a leading `/` or a leading `~`, then segments of
#: the characters a path is actually made of, and it must *stop* at the
#: punctuation that ends a sentence rather than swallowing it - `/var/log/x, and`
#: is not a path with a comma in it. A greedy matcher here would render half a
#: panel's prose in a fixed-width font, which is worse than not marking paths at
#: all, so the character class excludes every punctuation mark and the match ends
#: at the first one.
_PATH_RE = re.compile(
    r"(?<![\w/])"                 # not the tail of a word, so `a/b` is not split
    r"(?:~|\.{1,2})?/"            # `/`, `~/`, `./` or `../`
    r"[\w.@+-]+"                  # one segment
    r"(?:/[\w.@+*-]*)*"           # and the rest of them
    # `*` is allowed in the later segments only. Triggers shows its rules as
    # `/…/triggers/*.jsonl`, and stopping at the star left the glob half outside
    # the fixed-width run - which is the part that most needs to be recognisable.
    # `?` is deliberately not allowed: it is far more often punctuation.
)


def paths_markup(text: Any) -> str:
    """`text` as escaped markup, with any filesystem path in it fixed-width.

    **Prose stays prose.** The alternative - handing the whole sentence to
    `monospace()` - makes the sentence harder to read rather than the path
    easier, and these are sentences: "not installed - no model file in
    /var/cache/shani-chronoa/models". Only the path changes face, which is what
    makes the eye land on it.

    The text is escaped **before** the markup is added, and the escaping is
    applied to each piece separately - so a path containing `&` or `<` is shown
    literally rather than becoming broken markup. Measured on the same library:
    a raw description containing a bare `&` fails its markup parse and renders
    nothing at all, so this is not a cosmetic concern.

    This repo has been bitten three times by the alternative - a substring search
    matching a docstring, a regex counting prose as a selector, and a test
    comparing a widget against the table that built it - so the match is on the
    rendered string and the escaping is on the pieces, not on the whole.
    """
    # Coerced, not assumed: the values reaching this come out of probes, and a
    # `None` or an int in a string helper is normal here - `paths_markup(12345)`
    # raised `TypeError: expected string or bytes-like object` before this line.
    text = home_paths(text)
    out = []
    at = 0
    for match in _PATH_RE.finditer(text):
        if match.start() > at:
            out.append(_escape(text[at:match.start()]))
        out.append(f"<tt>{_escape(match.group(0))}</tt>")
        at = match.end()
    out.append(_escape(text[at:]))
    return "".join(out)


def paths_in(text: str, selectable: bool = False) -> Gtk.Widget:
    """A label whose filesystem paths are fixed-width and whose prose is not.

    `use_markup` is set explicitly, because `Adw.ActionRow`'s subtitle renders
    markup whether or not the caller asked - measured on libadwaita 1.5, where an
    unescaped `&` leaves the row blank. Plain `Gtk.Label` defaults to
    `use-markup=False`, so without this line a path-aware label would show its
    tags.
    """
    label = Gtk.Label()
    label.set_use_markup(True)
    label.set_markup(paths_markup(text))
    label.set_xalign(0.0)
    label.set_selectable(selectable)
    wrap_label(label)
    return label


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


def clear(container: Gtk.Widget) -> None:
    """Empty a container, in GTK4.

    **Not `container.foreach(...)`, which is GTK3.** `Gtk.Container.foreach` was
    removed in GTK4 and PyGObject does not substitute anything: measured on the
    installed 4.14, `Gtk.Box.foreach` raises `AttributeError: 'Box' object has no
    attribute 'foreach'`. Eight surfaces each had a status slot to empty on
    reload and each had copied the GTK3 line into it, so **every one of those
    eight panels raised `AttributeError` the first time it was opened** - and the
    surfaces that render a broken page instead of raising showed the shell of a
    panel with no content and nothing saying why.

    The sibling is captured *before* the removal, because removing the child
    clears the pointer the walk uses to get to the next one and the loop would
    stop after the first.
    """
    child = container.get_first_child()
    while child is not None:
        following = child.get_next_sibling()
        container.remove(child)
        child = following
