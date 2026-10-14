"""An editor in the window: open a file, change it, save it through the skills.

**This is the surface the tree did not have.** `code_hl.py` could colour code
and `skills/write_text_file.py` could change it, but between those two there was
no editable buffer anywhere in the package - the only `Gtk.TextView`s were a
read-only artifact preview (`blocks.py:883`) and the composer. So "edit this
file" was reachable only by asking a model to call a tool, which is the wrong
shape for a box.

**Why not GtkSourceView.** It is the obvious choice and it does not work here.
Arch's `gtksourceview5` 5.22.0-1 ships `language-specs/` containing only
`language.dtd`, `language.rng` and `language2.rng` - **zero `.lang`
definitions** - and no Arch package supplies them (`gtksourceview-langs`,
`gtksourceview5-langs`, `gtksourceview-lang` all resolve to nothing).
`GtkSource-5.typelib` *is* present, so the widget would load and highlight
nothing, which is worse than not having it: an editor with a language selector
that always says "None". That measurement is what `code_hl.py`'s module docstring
records, and it is why the 11-language regex lexer exists at all.

So highlighting here is **`code_hl.tokenize()` applied as buffer tags** - the
same scanner, the same palette, the same tokens the transcript draws. That is
also why `code_hl` needed a public token stream: until it had one, the only way
to get tokens out was to parse Pango markup back into tokens.

**Undo is GTK's, not ours.** `Gtk.TextBuffer` carries its own undo stack
(`set_enable_undo` / `undo` / `redo` / `get_can_undo`, measured on GTK 4.22.4).
That is the *document* undo and it is deliberately not the pre-image ring:
`skills/undo_last_change.py` is a 20-entry one-way ring for changes a **tool**
made, restored by consuming the entry. Using it for typing would write the whole
file to disk on every keystroke. So the buffer undoes typing, the ring undoes
agent edits, and a save made here still shows up in `undo_last_change`.

**Saving goes through `write_text_file`, not `open().write()`.** That is the
reuse argument and it is not a style preference. Routing the save through the
tool layer means the same consent key (`file-edit-enabled`), the same
sensitive-path refusal (`files.refuse_sensitive`), the same `files.parse_problem`
check that refuses to land unparseable Python/JSON/TOML, and the pre-image
recording that puts the change in the undo ring. A hand-rolled `write_text`
here would pass every one of those gates by not having any of them.

**The file list is the undo ring, not the file index.** `file_index.search()`
was the obvious source and it does not work for this: measured against a real
two-file index, `search(root, "", limit=200, embed=False)` returns **0 hits** -
an empty query matches nothing, because it is a search and not a listing. So
this page offers the two things that are actually true: the files Chronoa has
changed, read from `recent_changes()`, and an `Open file` chooser for anything
else.
"""

from __future__ import annotations

import logging
import pathlib
from typing import Any, Dict, List, Optional, Tuple

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gio, GLib, Gtk  # noqa: E402

from shani_chronoa import verification  # noqa: E402
from shani_chronoa.gui import code_hl  # noqa: E402
from shani_chronoa.gui.surfaces import common  # noqa: E402
from shani_chronoa.skills import undo_last_change  # noqa: E402
from shani_chronoa import tools  # noqa: E402

logger = logging.getLogger(__name__)

TITLE = "Editor"
#: Already used by `artifact_store`, so `test_ui_layout_contract.py`'s
#: icon-existence check has measured this name on this machine rather than
#: taking this string's word for it.
ICON = "text-editor-symbolic"

#: Extension -> `code_hl` language. Only the languages this module's own table
#: knows how to colour; anything else opens uncoloured, which `tokenize()`
#: guarantees is *unchanged* rather than half-coloured.
LANGUAGES_BY_SUFFIX = {
    ".py": "python", ".pyi": "python", ".pyw": "python",
    ".sh": "bash", ".bash": "bash", ".zsh": "bash",
    ".json": "json", ".jsonc": "json",
    ".yaml": "yaml", ".yml": "yaml",
    ".toml": "toml",
    ".xml": "xml", ".html": "xml", ".svg": "xml",
    ".sql": "sql",
    ".js": "javascript", ".jsx": "javascript", ".ts": "javascript",
    ".c": "c", ".h": "c", ".cpp": "c", ".java": "c", ".rs": "c", ".go": "c",
    ".css": "css",
    ".diff": "diff", ".patch": "diff",
}

#: Above this the file opens read-only.
#:
#: **A limit that refuses is worse than a limit that watches.** An editor that
#: refuses a 2 MB generated file leaves the person with no way to look at it at
#: all; one that opens it read-only still shows them the thing they asked for.
#: `file_index.MAX_FILE_BYTES` is 32 MB, which is a *search* limit - it bounds
#: an index that has to read every file - and reusing it here would make the
#: read-only threshold high enough that a person could type into a buffer
#: large enough to stall the UI, and then be told at Save time.
MAX_EDIT_BYTES = 2 * 1024 * 1024

#: How many recent files the list shows. `recent_changes()` is capped at 20 by
#: `undo_last_change.RING_SIZE`, so this is the whole ring by construction.
_MAX_LISTED = 20

#: Gutter geometry. Pango paints in device pixels here, so these are the numbers
#: the drawing area is measured in rather than in points.
_GUTTER_WIDTH = 52
_LINE_HEIGHT = 18
_GUTTER_FONT_PX = 11


def language_for(path: Any) -> str:
    """The `code_hl` language for ``path``, or "" when the table has no entry."""
    return LANGUAGES_BY_SUFFIX.get(pathlib.Path(str(path)).suffix.lower(), "")


def _read(path: pathlib.Path) -> Tuple[Optional[str], str]:
    """`(text, why-not)` for ``path``, decoded as UTF-8.

    Deliberately not `file_index.read_file`: that one refuses a binary, a PDF
    and anything past 32 MB, all of which are right for indexing and wrong for
    an editor that has already been handed a path. This reports what it could
    not do and lets the caller say so.
    """
    try:
        size = path.stat().st_size
    except OSError as exc:  # noqa: BLE001 - a vanished file is an answer
        return None, f"it could not be read ({exc})"
    try:
        return path.read_text(encoding="utf-8"), ""
    except UnicodeDecodeError:
        return None, "it is not UTF-8 text, and editing it would corrupt it"
    except OSError as exc:  # noqa: BLE001
        return None, f"it could not be read ({exc})"


def _tags_for(buffer: Gtk.TextBuffer) -> Dict[str, Gtk.TextTag]:
    """One tag per token kind, created once and reused.

    Looked up by name, because a `Gtk.TextTagTable` asked for a name it has
    already seen returns the **first** match - so re-creating the tags on every
    keystroke would leave an unreferenced tag in the table for each one, and
    the table would grow without bound over a long editing session.
    """
    table = buffer.get_tag_table()
    palette = code_hl.palette()
    out = {}
    for kind in code_hl._KINDS:
        tag = table.lookup(kind)
        if tag is None:
            tag = buffer.create_tag(kind)
        tag.set_property("foreground", palette.get(kind, "#000000"))
        out[kind] = tag
    return out


class _Editor:
    """One open file: a buffer, its tags, its line numbers, and its dirty state.

    Its own object rather than functions on the surface, so the tests can drive
    a real buffer without building a page around it. The widget tree is not the
    part with the logic in it.
    """

    def __init__(self, path: pathlib.Path, text: str, read_only: bool = False):
        self.path = pathlib.Path(path)
        self.read_only = read_only
        #: What is on disk, as read when this file was opened. The dirty check
        #: compares against this rather than being a flag every insert handler
        #: sets, so an edit that is undone is not dirty - which a flag cannot
        #: express, and which a person who types and backspaces should not be
        #: nagged about.
        self.on_disk = text

        self.buffer = Gtk.TextBuffer()
        self.buffer.set_enable_undo(True)
        self.buffer.set_text(text)

        self.view = Gtk.TextView(buffer=self.buffer)
        self.view.set_monospace(True)
        self.view.set_wrap_mode(Gtk.WrapMode.NONE)
        for setter, value in (("set_left_margin", 8), ("set_right_margin", 8),
                              ("set_top_margin", 4), ("set_bottom_margin", 4)):
            getattr(self.view, setter)(value)
        self.view.set_editable(not read_only)
        self.view.set_cursor_visible(not read_only)

        self._tags = _tags_for(self.buffer)
        self.apply_highlight()

    # -- content ---------------------------------------------------------
    def text(self) -> str:
        start, end = self.buffer.get_bounds()
        return self.buffer.get_text(start, end, True)

    def is_dirty(self) -> bool:
        return self.text() != self.on_disk

    def line_count(self) -> int:
        return self.buffer.get_line_count()

    # -- highlighting ----------------------------------------------------
    def apply_highlight(self) -> None:
        """Re-tag the whole buffer from `code_hl`'s token stream.

        Whole-buffer rather than incremental. This is a hand-written regex
        lexer over a document bounded by `MAX_EDIT_BYTES`, so one keystroke
        costs one scan; and an incremental version would have to invalidate
        itself whenever a quote or a block comment changed state on some
        *earlier* line, which is the whole reason string highlighting is hard.
        """
        self.buffer.remove_all_tags(self.buffer.get_start_iter(),
                                    self.buffer.get_end_iter())
        text = self.text()
        language = language_for(self.path)
        if not text or not language:
            return
        offset = 0
        for kind, chunk in code_hl.tokenize(text, language):
            start, offset = offset, offset + len(chunk)
            if kind is None or not chunk:
                continue
            self.buffer.apply_tag(
                self._tags[kind],
                self.buffer.get_iter_at_offset(start),
                self.buffer.get_iter_at_offset(offset))


class _Gutter(Gtk.DrawingArea):
    """Line numbers beside the text, painted rather than stacked.

    A `Gtk.Label` of numbers cannot stay in step with a `Gtk.TextView` unless it
    is inside the same scrolled window, and then it scrolls by whatever the
    view's own scrolling does rather than by the view's adjustment - which is
    why the two drift apart the moment either one wraps. This paints from the
    view's vertical adjustment and redraws when it moves, so they cannot
    disagree.
    """

    def __init__(self, editor: _Editor):
        super().__init__()
        self._editor = editor
        self.set_size_request(_GUTTER_WIDTH, -1)
        self._adjustment = editor.view.get_vadjustment()

    def _draw(self, _area, cr, width, height) -> bool:
        style = self.get_style_context()
        colour = style.get_color(style.get_color_state_flags())
        cr.set_source_rgba(colour.red, colour.green, colour.blue, colour.fg.alpha)
        cr.select_font_face("monospace")
        cr.set_font_size(_GUTTER_FONT_PX)

        total = self._editor.line_count()
        first = max(1, int(self._adjustment.get_value() // _LINE_HEIGHT) + 1)
        rows = max(1, height // _LINE_HEIGHT)
        for number in range(first, min(total + 1, first + rows + 1)):
            cr.move_to(8, (number - first + 1) * _LINE_HEIGHT)
            cr.show_text(str(number))
        return False


def _call_succeeded(outcome: "tools.ToolOutcome") -> bool:
    """Whether a dispatch can be shown to a person as having happened.

    **Copied from `assistant._tool_ok` rather than invented.** That function's
    docstring records the measurement that justifies this exact shape: reading
    the verdict out of the result *string* drew a refused `delete_file` and a
    call that never ran as "done". `ran` plus a verdict that is neither FAILED
    nor IMPOSSIBLE is the form that survives that test.
    """
    return bool(outcome.ran) and outcome.verdict not in (
        verification.Verdict.FAILED, verification.Verdict.IMPOSSIBLE)


class _Page:
    """The surface, kept as an object so `build()` stays a thin wrapper."""

    def __init__(self):
        self.stack = Gtk.Stack()
        self.status = common.empty_state(
            ICON, "No file open",
            "Open a file to edit it. Saving goes through the same consent gate "
            "and the same undo ring an assistant-driven edit would.")
        self.editor: Optional[_Editor] = None
        self._widgets: Dict[str, Gtk.Widget] = {}
        self._list_page: Optional[Gtk.Widget] = None

    # -- file list -------------------------------------------------------
    def _build_list(self, on_open) -> Gtk.Widget:
        """Recent changes, then an `Open file` chooser for everything else."""
        column = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)
        column.set_margin_start(18)
        column.set_margin_end(18)
        column.set_margin_top(18)
        column.set_margin_bottom(18)

        paths: List[pathlib.Path] = []
        try:
            paths = [path for path, _before, _after in undo_last_change.recent_changes()]
        except Exception as exc:  # noqa: BLE001 - a broken ring is an empty list
            logger.info("Could not read the undo ring for the file list: %s", exc)

        if paths:
            column.append(common.group(
                "Files Chronoa has changed",
                "From the undo ring. The newest is first."))
            rows = Gtk.ListBox(selection_mode=Gtk.SelectionMode.NONE)
            rows.add_css_class("boxed-list")
            for path in paths[:_MAX_LISTED]:
                button = Gtk.Button()
                label = Gtk.Label(label=str(path))
                label.set_xalign(0)
                label.set_ellipsize(3)
                button.set_child(label)
                button.connect("clicked", lambda _b, p=path: on_open(p))
                rows.append(button)
            column.append(rows)
        else:
            column.append(common.empty_state(
                ICON, "Nothing changed yet",
                "When Chronoa edits a file it lands here, ready to open. "
                "Use Open file below for anything else.", inline=True))

        choose = Gtk.Button(label="Open file...")
        choose.set_halign(Gtk.Align.START)
        choose.connect("clicked", lambda _b: self._choose(on_open))
        column.append(choose)
        return column

    def _choose(self, on_open) -> None:
        dialog = Gtk.FileChooserNative.new(
            "Open a file", None, Gtk.FileChooserAction.OPEN, "_Open", "_Cancel")
        filter_ = Gtk.FileFilter()
        filter_.set_name("Text files")
        filter_.add_mime_type("text/plain")
        dialog.set_filter(filter_)
        dialog.connect("response", lambda d, response: (
            self._chosen(d, response, on_open), True)[1])
        dialog.show()

    def _chosen(self, dialog, response, on_open) -> None:
        try:
            file = dialog.get_file() if response == Gtk.ResponseType.ACCEPT else None
        except Exception as exc:  # noqa: BLE001 - a portal that cannot answer
            logger.info("The file chooser could not be read: %s", exc)
            file = None
        finally:
            dialog.destroy()
        if file is not None:
            try:
                on_open(pathlib.Path(file.get_path()))
            except Exception as exc:  # noqa: BLE001 - a non-local file has no path
                logger.info("That file has no local path: %s", exc)

    # -- mounting --------------------------------------------------------
    def show_list(self) -> None:
        if self._list_page is not None:
            return
        self._list_page = common.scrolled(self._build_list(self.open))
        self.stack.add_named(self._list_page, "list")
        self.stack.set_visible_child_name("list")

    def open(self, path: pathlib.Path) -> None:
        """Show ``path``, or say in as many words why it cannot be shown."""
        path = pathlib.Path(path)
        text, why = _read(path)
        if text is None:
            self.stack.set_visible_child_name("status")
            self._set_status("This file cannot be opened", why)
            return
        try:
            size = path.stat().st_size
        except OSError as exc:  # noqa: BLE001
            self.stack.set_visible_child_name("status")
            self._set_status("This file cannot be opened", f"it is gone ({exc})")
            return
        self.editor = _Editor(path, text, read_only=size > MAX_EDIT_BYTES)
        self._mount()
        self.stack.set_visible_child_name("file")

    def _set_status(self, title: str, description: str) -> None:
        page = self.status
        # `common.empty_state` returns a bare widget; rebuild it rather than
        # assume an Adw.StatusPage it does not guarantee.
        self.stack.remove(page) if page.get_parent() is not None else None
        self.status = common.empty_state(ICON, title, description)
        self.stack.add_named(self.status, "status")

    def _header(self) -> Tuple[Gtk.Widget, Dict[str, Gtk.Widget]]:
        title = Gtk.Label(label="")
        title.set_ellipsize(3)
        title.set_xalign(0)
        title.set_hexpand(True)

        dirty = Gtk.Label(label="")
        dirty.add_css_class("dim-label")

        undo = Gtk.Button(label="Undo")
        redo = Gtk.Button(label="Redo")
        save = Gtk.Button(label="Save")
        save.add_css_class("suggested-action")

        spacer = Gtk.Box()
        spacer.set_hexpand(True)

        box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        box.set_margin_start(12)
        box.set_margin_end(12)
        box.set_margin_top(8)
        box.set_margin_bottom(8)
        for widget in (title, dirty, spacer, undo, redo, save):
            box.append(widget)
        return box, {"title": title, "dirty": dirty, "undo": undo,
                     "redo": redo, "save": save}

    def _mount(self) -> None:
        assert self.editor is not None
        editor = self.editor
        header, widgets = self._header()
        widgets["title"].set_text(str(editor.path))

        def refresh() -> None:
            dirty = editor.is_dirty()
            widgets["dirty"].set_text("unsaved" if dirty else "")
            widgets["save"].set_sensitive(not editor.read_only and dirty)
            widgets["undo"].set_sensitive(editor.buffer.get_can_undo())
            widgets["redo"].set_sensitive(editor.buffer.get_can_redo())

        def on_changed(_buffer) -> None:
            editor.apply_highlight()
            refresh()

        editor.buffer.connect("changed", on_changed)
        widgets["save"].connect("clicked", lambda _b: self._save(refresh))
        widgets["undo"].connect("clicked", lambda _b: editor.buffer.undo())
        widgets["redo"].connect("clicked", lambda _b: editor.buffer.redo())

        gutter = _Gutter(editor)
        gutter.set_vexpand(True)
        scroller = Gtk.ScrolledWindow()
        scroller.set_policy(Gtk.PolicyType.AUTOMATIC, Gtk.PolicyType.AUTOMATIC)
        scroller.set_vexpand(True)
        scroller.set_child(editor.view)
        scroller.get_vadjustment().connect(
            "value-changed", lambda _a: gutter.queue_draw())

        columns = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        columns.append(gutter)
        columns.append(scroller)

        page = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        page.set_vexpand(True)
        page.append(header)
        page.append(columns)
        if editor.read_only:
            page.append(common.banner(
                f"Read-only. {editor.path.stat().st_size} bytes is past the "
                f"{MAX_EDIT_BYTES}-byte limit for editing."))

        name = f"file:{editor.path}"
        self.stack.add_named(page, name)
        self._widgets = widgets
        refresh()

    def _save(self, refresh) -> None:
        """Write the buffer through `write_text_file`, and report what it said."""
        assert self.editor is not None
        editor = self.editor
        outcome = tools.execute_tool_outcome("write_text_file", {
            "path": str(editor.path),
            "content": editor.text(),
            "overwrite": True,
        })
        if _call_succeeded(outcome):
            editor.on_disk = editor.text()
            refresh()
            return
        # Shown verbatim rather than paraphrased into "could not save": the
        # refusal names its own reason - consent, a sensitive path, a file that
        # would not parse - and that sentence is the one that says what to do.
        logger.info("save refused: %s", outcome.text)
        self._set_status("The save was refused", outcome.text)
        self.stack.set_visible_child_name("status")


def build(app: Any) -> Gtk.Widget:
    """The Editor page.

    Contract from `gui/surfaces/__init__.py`: a surface must not crash the
    window when the data underneath is missing, corrupt or stale. So the page is
    built inside a try, and a failure becomes an honest status page rather than
    a sidebar row that throws every time it is selected.
    """
    try:
        page = _Page()
        page.stack.set_vexpand(True)
        page.stack.add_named(page.status, "status")
        page.show_list()
        return Adw.NavigationPage(child=page.stack, title=TITLE)
    except Exception:  # noqa: BLE001 - a surface that raises loses a sidebar row
        logger.warning("The editor surface could not be built", exc_info=True)
        return Adw.NavigationPage(
            child=common.empty_state(
                ICON, "The editor could not start",
                "This is a bug. The reason is in the log under "
                "`shani_chronoa.gui.surfaces.editor`."),
            title=TITLE)