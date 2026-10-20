"""Your own files, searchable — the surface for `search_my_files`.

**Why a panel and not just a tool.** `file_index` and the `search_my_files`
skill have existed since they landed, reachable only by a model deciding to call
them. That is the right shape for a question asked in conversation and the wrong
one for a box: "what do my notes say about the wifi box" is a thing a person
types into a search field, not a sentence they ask out loud, and the difference
is that a person types the *next* one in half a second. A panel is also the only
place the answer **"nothing is indexed yet"** can be visible without asking.

**Three states that are not the same, and a search box that cannot tell them
apart is a confident wrong answer.** Nothing indexed (fixable with the button on
this page), consent off (fixable in Settings), and nothing matched (a fact about
the words). `file_index.search()` returns an empty list for all three, so this
panel reads `file_index.status()` for the first two and says which one it is,
confined to the directory the entry names.

**The panel says which search it ran.** `search()` hands back `(hits, how)` and
`how` is either "keyword and embedding" or an explanation of why it was
keyword-only. A search box that shows results without that line reads as though
it searched by meaning when it did not — which is the specific claim
`local_embed` not being installed makes false, and the reason the skill's own
sentence exists.

**The search runs when the person asks, not when they pause.** Every other panel
uses `common.search_entry()`, which filters a list already in hand after a 150 ms
pause. This one cannot: `search()` embeds the query and talks to a local HTTP
server, so a debounce fires a round trip per word typed. The entry is therefore
`activate`-driven (Enter) with a visible Search button — both routes, because a
person who has just typed three words should not have to guess which key.

**Everything that touches the index runs off the GTK thread.** A reindex walks a
directory and embeds it in batches of 32; a search embeds the query. On the main
loop the window freezes for the duration, which is the same defect a surface with
no button avoids.
"""

from __future__ import annotations

import logging
import pathlib
import threading
from typing import Any, List, Optional

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, GLib, Gtk

from shani_chronoa import file_index
from shani_chronoa.gui.surfaces import common

logger = logging.getLogger(__name__)

TITLE = "My files"
#: **`folder-search-symbolic` is not a glyph this theme ships** - measured
#: with `Gtk.IconTheme.has_icon`, and the check is
#: `tests/test_ui_layout_contract.py::test_every_surface_icon_exists_on_this_machine`.
#: A name the theme lacks renders as an empty box the size of an icon, which
#: reads as a rendering bug rather than as a missing glyph.
ICON = "edit-find-replace-symbolic"
SECTION = "Remembering"
SUBTITLE = ("Search your own documents and notes, by keyword and - where the "
            "embedding model is installed - by meaning.")

CONSENT_KEY = "file-index-enabled"

NOTHING_INDEXED = "Nothing is indexed yet"
NOTHING_INDEXED_DESC = ("Index a directory and your own notes become searchable "
                        "here, without being asked for out loud.")

#: How many hits the box asks for. The skill's own default, so the panel and the
#: answer the model gets do not disagree about what "the results" means.
DEFAULT_LIMIT = 8


def _app_config(app: Any) -> Any:
    """The app's config, or `None` — never one built here.

    `ChronoaConfig()` constructs a `Gio.Settings`, and on first use that creates
    `~/.config/glib-2.0`. A read-only page that creates a settings directory is
    not read-only, which `gui/surfaces/voice.py` already had to learn.
    """
    return getattr(app, "config", None)


def _human_bytes(size: Any) -> str:
    try:
        number = float(size)
    except (TypeError, ValueError):
        return "unknown size"
    if number < 1024:
        return f"{int(number)} B"
    for unit in ("kB", "MB", "GB"):
        number /= 1024.0
        if number < 1024:
            return f"{number:.0f} {unit}" if number < 10 else f"{number:.1f} {unit}"
    return f"{number:.1f} TB"


def _off_thread(button: Optional[Gtk.Widget], status: Gtk.Label,
                work, done) -> None:
    """Run `work()` on a worker, hand `done(result)` back on the GTK thread.

    `work` may take minutes and `done` touches widgets, so the two halves are
    kept apart rather than trusting a caller to remember which thread it is on.
    """
    if button is not None:
        button.set_sensitive(False)
    status.set_label("Working...")

    def runner() -> None:
        try:
            result = work()
        except Exception as exc:  # noqa: BLE001 - shown in the panel, not swallowed
            logger.warning("the file index failed", exc_info=True)
            result = exc
        GLib.idle_add(finish, result)

    def finish(result) -> bool:
        if button is not None:
            button.set_sensitive(True)
        try:
            done(result)
        except Exception:  # noqa: BLE001 - a broken callback is not a broken panel
            logger.warning("could not show the result", exc_info=True)
        return False

    threading.Thread(target=runner, daemon=True).start()


class _FilesView(Gtk.Box):
    """The directory box, the search box, and one of four answers."""

    def __init__(self, app: Any) -> None:
        super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        self._app = app
        self.status_recorder = common.StatusRecorder()
        self._status_slot = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        self._answer_slot = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        self._answer_slot.set_vexpand(True)

        self.append(self._directory_row())
        self.append(self._status_slot)
        self.append(self._answer_slot)
        self._root = str(pathlib.Path.home())
        self._show_index_state()
        self._refresh_status()

    # -- the controls --------------------------------------------------------

    def _directory_row(self) -> Gtk.Widget:
        """Which directory, and the button that reads it.

        `path=` on the skill is a string, so the entry is the same contract: a
        person types (or pastes) a path and everything above it applies to that
        one directory. It starts on `$HOME` because that is the skill's own
        default, and it is replaced by the index's own root as soon as one
        exists — so a person who indexed `~/notes` is not left staring at a
        directory they never asked about.
        """
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        for side in ("start", "end", "top"):
            getattr(box, f"set_margin_{side}")(12 if side in ("start", "end") else 10)

        entry_row = Adw.EntryRow() if common.adw_ready() else _plain_entry_row()
        self.path_entry = entry_row
        entry_row.set_title("Directory")
        entry_row.set_text(str(pathlib.Path.home()))
        _label(entry_row, "Which directory to index and search")
        box.append(entry_row)
        self._path_entry = entry_row

        self.reindex = Gtk.Button(label="Index this directory")
        self.reindex.add_css_class("suggested-action")
        self.reindex.set_tooltip_text(
            "Read the directory and build or refresh the index")
        _label(self.reindex, "Index this directory")
        self.reindex.connect("clicked", self._on_reindex)
        box.append(self.reindex)

        self.search_entry = Gtk.SearchEntry(
            placeholder_text="What are you looking for?")
        self.search_entry.set_tooltip_text(
            "Search by meaning as well as by keyword. Press Enter to search.")
        _label(self.search_entry, "Search your files")
        self.search_entry.connect("activate", self._on_search)
        self.search_entry.connect("search-changed", self._on_search_changed)
        box.append(self.search_entry)

        self.search_button = Gtk.Button(label="Search")
        self.search_button.set_tooltip_text("Search this directory")
        _label(self.search_button, "Search")
        self.search_button.connect("clicked", self._on_search)
        box.append(self.search_button)
        return box

    # -- the status line, and the consent it can refuse on --------------------

    def _consent(self) -> bool:
        config = _app_config(self._app)
        if config is None:
            return False
        try:
            return bool(config.get_bool(CONSENT_KEY, False))
        except Exception:  # noqa: BLE001 - a setting that cannot be read is not consent
            return False

    def _refresh_status(self) -> None:
        """The one line that says what is indexed and how it will be searched."""
        common.clear(self._status_slot)
        if not self._consent():
            # Named, with the switch that grants it, because a panel that says
            # "no results" under a shut consent key is indistinguishable from a
            # directory that does not contain the words.
            # Named as Settings names it, and turned on from here.
            self.status_recorder.set(common.STATUS_OFF)
            self._status_slot.append(common.consent_banner(
                self._app, CONSENT_KEY,
                "Nothing has been read and nothing will be searched.",
                on_change=self._after_consent))
            return

        state = file_index.status(root=self._directory())
        if not state["indexed"]:
            self._status_slot.append(self.status_recorder.row(
                common.STATUS_OK, NOTHING_INDEXED,
                NOTHING_INDEXED_DESC))
            return

        where = state["root"] or "several directories"
        if state["embedded"]:
            how = (f"{state['indexed']} file(s) under {where}, "
                   f"{state['embedded']} of them searchable by meaning.")
        else:
            how = (f"{state['indexed']} file(s) under {where}, none with an "
                   "embedding — this searches by keyword only, so it will not "
                   "find a file that says \"the wifi box\" when you ask about "
                   "routers.")
        self._status_slot.append(self.status_recorder.row(
            common.STATUS_OK, how, ""))
        # Only when the entry does not already name it: writing on every
        # refresh would fight a person halfway through typing a path.
        if state["root"] and state["root"] != self._directory():
            self._path_entry.set_text(state["root"])

    # -- the four answers ----------------------------------------------------

    def _present(self, widget: Gtk.Widget) -> None:
        """Put one widget in the answer slot, behind a scroller if it has none.

        **The scroller, not the states.** `test_every_surface_can_scroll` is the
        check that caught `inventory` shipping this same defect - rows passed
        straight to the toolbar's content slot, so the last ones could not be
        read at any window height - and it caught it here: seven
        `common.empty_state()` sites went in unwrapped.

        Wrapping here rather than at each site is what keeps the two from
        disagreeing: `_show_hits` already scrolls its own list, so it passes
        something that carries a `ScrolledWindow` and must not be wrapped twice.
        """
        child = self._answer_slot.get_first_child()
        while child is not None:
            following = child.get_next_sibling()
            self._answer_slot.remove(child)
            child = following
        if not _has_scroller(widget):
            widget = common.scrolled(widget, 520)
        self._answer_slot.append(widget)

    def _after_consent(self) -> None:
        """The switch was turned on from the banner: redraw the page."""
        self._refresh_status()
        self._show_index_state()

    def _show_index_state(self) -> None:
        """Consent off, or nothing indexed: a sentence and a way out.

        Not an `Adw.StatusPage` for the empty case: the controls above are still
        live, and a full-page empty state on a page with a working directory
        field reads as "nothing here" rather than "nothing indexed yet".
        """
        if not self._consent():
            # **The banner is this page's one statement about the gate.**
            # It names the switch, says it is off, says what that means and
            # turns it on from here. The refusal below used to say the same
            # thing a second time - in the switch's words, in a title, and
            # in a sentence pointing back at the banner - so the reader met
            # it three times before the controls that are still live.
            #
            # **So the content area says nothing**, the way the Phone panel's
            # does not: the panel's own subtitle says what the page is for,
            # and the banner says why there is nothing here yet.
            self._present(_nothing())
            return
        self._present(common.empty_state(
            "edit-find-replace-symbolic", NOTHING_INDEXED, NOTHING_INDEXED_DESC))

    def _show_hits(self, hits: List[dict], how: str, query: str) -> None:
        group = common.group()
        for hit in hits:
            group.add(self._hit_row(hit, query))
        header = Gtk.Label(label=f"{len(hits)} file(s) match “{query}” — {how}")
        header.set_xalign(0.0)
        header.add_css_class("dim-label")
        common.wrap_label(header, 60)
        header.set_margin_start(12)
        header.set_margin_end(12)
        header.set_margin_bottom(6)
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        box.append(header)
        box.append(common.scrolled(group, 520))
        self._present(box)

    def _hit_row(self, hit: dict, query: str) -> Gtk.Widget:
        """One hit: its path, what kind of file it is, and a line of its own text.

        **Not `common.row()`,** because that takes a *string* title and the path
        has to go in as a `common.paths_in()` label so a 140-character
        `~/Documents/...` breaks at its separators and its separators are
        fixed-width. Passing the label instead raised
        `TypeError: Must be string, not Label` on the first real search — found
        by building the panel with an index that actually had rows, which is the
        only way a hit row is ever constructed.

        So this is the `conversations.py` shape: an `Adw.PreferencesRow` holding
        a box it lays out itself, with the row supplying the margins, the hover
        state and the focus ring.
        """
        path = str(hit.get("path") or "")
        bits = [str(hit.get("kind") or "file"),
                _human_bytes(hit.get("size"))]
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
        box.append(common.paths_in(path, selectable=True))
        meta = Gtk.Label(label="  ·  ".join(bits), xalign=0.0)
        meta.add_css_class("dim-label")
        meta.add_css_class("caption")
        box.append(meta)
        excerpt = Gtk.Label(label=str(hit.get("excerpt") or ""), xalign=0.0)
        excerpt.set_wrap(True)
        common.wrap_label(excerpt, 70)
        excerpt.add_css_class("dim-label")
        box.append(excerpt)
        row = (Adw.PreferencesRow() if common.adw_ready()
               else _plain_preferences_row())
        row.set_child(box)
        _label(row, f"{path}: {hit.get('excerpt') or ''}")
        return row

    def _show_no_hits(self, query: str, how: str) -> None:
        """No hits — and *which* of the two empty answers this is.

        "Nothing is indexed" has a button on this page; "nothing matched" does
        not, and sending someone to reindex because their query was too specific
        is the advice this line exists not to give.
        """
        state = file_index.status(root=self._directory())
        if not state["indexed"]:
            self._present(common.empty_state(
                "edit-find-replace-symbolic", NOTHING_INDEXED, NOTHING_INDEXED_DESC))
            return
        self._present(common.empty_state(
            "edit-find-symbolic",
            f"Nothing in the index matches “{query}”",
            f"Searched by {how}. The index holds {state['indexed']} file(s) "
            "under " + (state["root"] or "several directories") + "."))

    # -- the actions ---------------------------------------------------------

    def _directory(self) -> str:
        return str(self._path_entry.get_text() or "").strip() or str(
            pathlib.Path.home())

    def _on_search_changed(self, text: str) -> None:
        """Cleared means "no query", which is the index state rather than no hits.

        `search()` with an empty query is not an error but it is not a search
        either, and the panel would sit on a list of hits from a query the
        person has deleted - which reads as "these are what you are looking
        for" after they stopped looking.
        """
        if not text.strip():
            self._show_index_state()

    def _on_search(self, *_args) -> None:
        query = str(self.search_entry.get_text() or "").strip()
        if not query or not self._consent():
            self._show_index_state()
            return
        root, limit = self._directory(), DEFAULT_LIMIT
        self.search_button.set_label("Searching...")
        _off_thread(
            self.search_button, Gtk.Label(label="Searching..."),
            lambda: file_index.search(root, query, limit=limit),
            lambda result: self._searched(query, result))

    def _searched(self, query: str, result) -> None:
        """The worker is done. A failure is a sentence, not a traceback."""
        self.search_button.set_label("Search")
        if isinstance(result, Exception):
            self._present(common.empty_state(
                "dialog-error-symbolic", "The search could not be run",
                f"{type(result).__name__}: {result}"))
            return
        hits, how = result
        if hits:
            self._show_hits(hits, how, query)
        else:
            self._show_no_hits(query, how)

    def _on_reindex(self, _button: Gtk.Button) -> None:
        if not self._consent():
            self._show_index_state()
            return
        root = self._directory()
        self.reindex.set_label("Indexing...")
        _off_thread(
            self.reindex, Gtk.Label(label="Indexing..."),
            lambda: file_index.index_directory(root, embed=True),
            self._index_done)

    def _index_done(self, result) -> None:
        """Report what the run actually did, including the ways it came out partial."""
        self.reindex.set_label("Index this directory")
        self._refresh_status()
        if isinstance(result, Exception):
            self._present(common.empty_state(
                "dialog-error-symbolic", "The index could not be built",
                f"{type(result).__name__}: {result}"))
            return
        report = result
        if "error" in report:
            self._present(common.empty_state(
                "dialog-error-symbolic", "The directory could not be indexed",
                str(report["error"])))
            return
        if report.get("truncated"):
            # **Stops and says so.** A partial index presented as a complete one
            # is the confident-wrong-answer shape: the person searches, finds
            # nothing for a file that is really there, and has no way to know.
            self._present(common.empty_state(
                "dialog-warning-symbolic",
                f"Indexed the first {report.get('examined', 0)} file(s) and stopped",
                "That is more files than one run examines, so this index is "
                "partial. Name a smaller directory, or a subdirectory of this "
                "one, to get all of it."))
            return
        self._show_index_state()


class _FallbackEntryRow(Gtk.Box):
    """The `Adw.EntryRow` shape, without libadwaita.

    A page that falls back and then raises `AttributeError` on `set_title` is a
    page that is broken exactly where nobody can test it — so the two methods
    the panel calls are here as methods rather than as attributes patched onto
    a stock widget at construction time.
    """

    def __init__(self) -> None:
        super().__init__(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        self._name = Gtk.Label(label="")
        self._name.set_xalign(0.0)
        self.entry = Gtk.Entry()
        self.entry.set_hexpand(True)
        self.append(self._name)
        self.append(self.entry)

    def set_title(self, title: str) -> None:
        self._name.set_label(title)

    def get_text(self) -> str:
        return self.entry.get_text()

    def set_text(self, text: str) -> None:
        self.entry.set_text(text)


class _FallbackPreferencesRow(Gtk.Box):
    """The `Adw.PreferencesRow` shape, without libadwaita."""

    def __init__(self) -> None:
        super().__init__(orientation=Gtk.Orientation.HORIZONTAL, spacing=12)
        self.set_margin_top(6)
        self.set_margin_bottom(6)

    def set_child(self, widget: Gtk.Widget) -> None:
        self.append(widget)


def _plain_entry_row() -> Gtk.Widget:
    """An `Adw.EntryRow`, or the fallback where libadwaita is absent."""
    return _FallbackEntryRow() if not common.adw_ready() else Adw.EntryRow()


def _plain_preferences_row() -> Gtk.Widget:
    """An `Adw.PreferencesRow`, or the fallback where libadwaita is absent."""
    return _FallbackPreferencesRow()


def _has_scroller(widget: Gtk.Widget) -> bool:
    """Whether `widget` already contains a `Gtk.ScrolledWindow`.

    Read rather than remembered, because a caller that wraps its own list must
    not be wrapped a second time - a scroller inside a scroller gives the
    content two independent scroll positions and neither is the one the person
    expects.
    """
    stack = [widget]
    while stack:
        node = stack.pop()
        if isinstance(node, Gtk.ScrolledWindow):
            return True
        child = node.get_first_child()
        while child is not None:
            stack.append(child)
            child = child.get_next_sibling()
    return False


def _nothing() -> Gtk.Widget:
    """What to do about it, for a page whose banner is the statement.

    **Not an empty state with words in it.** The banner above already says
    what is wrong and offers the button; a title here would say it again in
    a third vocabulary, and a sentence pointing at the banner would say it a
    fourth time.

    **This says what the person can do**, which the banner does not: the
    controls above are still live, and this is what they are for.
    """
    box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
    box.set_margin_start(24)
    box.set_margin_end(24)
    note = Gtk.Label(
        label="Pick a directory above and index it to search it.",
        xalign=0.0)
    note.add_css_class("dim-label")
    common.wrap_label(note, 48)
    box.append(note)
    return box


def _consent_refusal(app: Any) -> Gtk.Widget:
    """The panel's answer when the consent key is off.

    It offers the Settings page rather than only naming the key: a switch nobody
    can find is the dead-control class this repo records, and the row above
    already names the key in full for the person who wants to find it another
    way.
    """
    box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
    box.set_valign(Gtk.Align.CENTER)
    box.set_vexpand(True)
    box.set_margin_start(24)
    box.set_margin_end(24)
    title = Gtk.Label(label="Indexing your files is turned off")
    title.add_css_class("title-2")
    # Wrapped: the layout contract allows a long label only if it wraps or
    # ellipsises, and this one was an unbroken sentence in a page that is
    # deliberately narrower than it.
    common.wrap_label(title)
    box.append(title)
    # **No second button.** The banner above names the switch and turns it on;
    # this page used to say the same thing three times - status row, this
    # sentence, and an "Open Settings" that sent the person away to flip the
    # switch the banner can flip here.
    detail = Gtk.Label(
        label=(f"Nothing has been read. Turn on “{common.consent_name(CONSENT_KEY)}” "
               "above and Chronoa can index a directory you choose."))
    detail.set_xalign(0.0)
    common.wrap_label(detail, 48)
    box.append(detail)
    return box


def _label(widget: Gtk.Widget, label: str) -> None:
    """Say what a control is, for a screen reader.

    A `Gtk.SearchEntry`'s placeholder is not a name: the accessible label is what
    is read, and a person using AT hears "edit" with nothing after it.
    """
    try:
        widget.update_property([Gtk.AccessibleProperty.LABEL], [label])
    except Exception:  # noqa: BLE001 - a widget with no a11y is not a broken panel
        logger.debug("no accessible label on %r", type(widget).__name__)


def build(app: Any) -> Gtk.Widget:
    """The "My files" page: an `Adw.NavigationPage`."""
    view = _FilesView(app)
    page, set_content = common.surface(TITLE, SUBTITLE)
    set_content(common.with_model_banners(app, view, [
        ("memory", "search your files by meaning - only by exact words")]))
    for name in ("path_entry", "search_entry", "search_button", "reindex",
                 "_show_index_state", "_show_hits", "_show_no_hits",
                 "_refresh_status", "_consent", "_directory"):
        setattr(page, name, getattr(view, name))
    page.status = view.status_recorder.status
    return page


__all__ = ["TITLE", "ICON", "SECTION", "SUBTITLE", "build", "CONSENT_KEY",
           "NOTHING_INDEXED", "DEFAULT_LIMIT"]