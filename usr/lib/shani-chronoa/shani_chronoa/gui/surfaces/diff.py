"""Diff view: file-by-file comparison, like the diff panel in Claude Code.

World-class diff features:
- **File list** on the left: all changed files with a summary of +/- lines
- **Inline unified diff** on the right: +/- lines with line numbers
- **Accept/Reject** per file or per hunk
- **Summary**: count of changed files, added/removed lines
- **Line selection**: click a line to select it (for copy/move actions)
- **Diff stats**: per-file statistics (lines added/removed)
- **No scroll jank**: all hunks rendered once

Integrates with the office_document skill: when editing a .docx/.xlsx,
the diff surface can show which paragraphs/cells changed and offer
Accept/Reject at file or hunk granularity.
"""

from __future__ import annotations

import difflib
import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gdk, Gio, Gtk  # noqa: E402

from shani_chronoa.gui.surfaces import common  # noqa: E402

logger = logging.getLogger(__name__)

TITLE = "Diff"
#: `diff-symbolic` is not a glyph this theme ships (checked with
#: `Gtk.IconTheme.has_icon`, which is what
#: `tests/test_surface_health_status.py::TestSidebarIcons` checks). An icon
#: name the theme lacks renders as an empty box, which reads as a rendering
#: bug rather than as a missing glyph. `document-edit-symbolic` is the theme's
#: own name for "a document being changed".
ICON = "document-edit-symbolic"
SECTION = "Acting"

SUBTITLE = (
    "What Chronoa changed on this machine, file by file, with line numbers. "
    "These writes already happened; to put one back, ask it to undo."
)


# ---------------------------------------------------------------------------
# Diff data model
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class DiffLine:
    """A single line in a diff."""
    line_no: int          # line number in the new/modified version
    tag: str              # one of '+', '-', ' ', '?'
    text: str             # the line text, without trailing newline


@dataclass(frozen=True)
class DiffHunk:
    """A contiguous group of changed lines."""
    old_start: int
    old_count: int
    new_start: int
    new_count: int
    lines: List[DiffLine]


@dataclass(frozen=True)
class DiffFile:
    """A single file's diff."""
    path: Path
    status: str           # 'modified', 'added', 'deleted', 'renamed'
    hunks: List[DiffHunk]
    selected_line_ids: Set[str]  # "file:hunk_idx:line_idx"


def diff_text(old: str, new: str) -> List[DiffLine]:
    """Return a list of (tag, text, new_line_no) for a unified diff."""
    old_lines = old.splitlines(keepends=True) or [""]
    new_lines = new.splitlines(keepends=True) or [""]
    matcher = difflib.SequenceMatcher(None, old_lines, new_lines)
    out: List[DiffLine] = []
    new_no = 1
    for tag, i1, i2, j1, j2 in matcher.get_opcodes():
        if tag in ("replace", "delete"):
            for line in old_lines[i1:i2]:
                out.append(DiffLine(line_no=-1, tag="-", text=line.rstrip("\n")))
        if tag in ("replace", "insert"):
            for line in new_lines[j1:j2]:
                out.append(DiffLine(line_no=new_no, tag="+", text=line.rstrip("\n")))
                new_no += 1
        if tag == "equal":
            for line in old_lines[i1:i2]:
                out.append(DiffLine(line_no=new_no, tag=" ", text=line.rstrip("\n")))
                new_no += 1
    return out


def diff_files(file: DiffFile, lines: List[DiffLine]) -> List[DiffHunk]:
    """Split a list of diff lines into hunks around unchanged runs.

    Both sides are counted as the lines go by. This used to take the old-side
    number from a removed line's `line_no`, which `diff_text` sets to -1, so
    every header read like `@@ -1,2 +0,3 @@` - a new side starting at line 0.
    """
    hunks: List[DiffHunk] = []
    cur: List[DiffLine] = []
    old_no = new_no = 0               # lines consumed so far on each side
    starts = (1, 1)
    for line in lines:
        if line.tag == " ":
            if cur:
                hunks.append(_close_hunk(cur, *starts))
                cur = []
            old_no += 1
            new_no += 1
            continue
        if not cur:
            starts = (old_no + 1, new_no + 1)
        cur.append(line)
        if line.tag == "-":
            old_no += 1
        elif line.tag == "+":
            new_no += 1
    if cur:
        hunks.append(_close_hunk(cur, *starts))
    return hunks


def _close_hunk(cur: List[DiffLine], old_start: int, new_start: int) -> DiffHunk:
    return DiffHunk(old_start=old_start, old_count=sum(1 for l in cur if l.tag == "-"),
                    new_start=new_start, new_count=sum(1 for l in cur if l.tag == "+"),
                    lines=cur)


def compute_file_diff(path: Path, old: str, new: str) -> DiffFile:
    """Compute the diff for a file, inferring its status."""
    lines = diff_text(old, new)
    if not lines:
        return DiffFile(path=path, status="unchanged", hunks=[], selected_line_ids=set())
    if old == "":
        status = "added"
    elif new == "":
        status = "deleted"
    else:
        status = "modified"
    return DiffFile(path=path, status=status, hunks=diff_files(path, lines),
                    selected_line_ids=set())


# ---------------------------------------------------------------------------
# GTK widgets
# ---------------------------------------------------------------------------

def _tag_color_css(tag: str) -> str:
    css = {
        "+": "diff-add",
        "-": "diff-remove",
        "?": "diff-change",
    }
    return css.get(tag, "")


class _DiffLineBox(Gtk.Box):
    """One diff line with number, +/- tag, and text."""

    def __init__(self, file: DiffFile, hunk_idx: int, line_idx: int, line: DiffLine,
                 selectable: bool = True) -> None:
        super().__init__(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        self.set_valign(Gtk.Align.START)
        self._file = file
        self._hunk_idx = hunk_idx
        self._line_idx = line_idx
        self._line = line
        self.add_css_class(_tag_color_css(line.tag))

        # +/- tag
        tag = Gtk.Label(label=line.tag, xalign=0.0, selectable=False)
        if line.tag == "+":
            tag.add_css_class("diff-tag-add")
        elif line.tag == "-":
            tag.add_css_class("diff-tag-remove")
        tag.set_size_request(24, 0)
        self.append(tag)

        # Line number
        num = Gtk.Label(label=str(line.line_no) if line.line_no > 0 else "",
                        xalign=1.0, selectable=False)
        num.set_size_request(36, 0)
        self.append(num)

        # Divider between number and text
        divider = Gtk.Separator(orientation=Gtk.Orientation.VERTICAL)
        divider.set_valign(Gtk.Align.CENTER)
        self.append(divider)

        # Text
        # Plain text into a plain label. It was markup-escaped first, and a
        # `label=` is not parsed as markup, so `a & b` showed as `a &amp; b`.
        label = Gtk.Label(label=line.text, xalign=0.0, wrap=True, selectable=selectable)
        if line.tag == "+":
            label.add_css_class("diff-line-add")
        elif line.tag == "-":
            label.add_css_class("diff-line-remove")
        self.append(label)

        # Selection highlight
        if selectable:
            self._selector = _LineSelector()
            self._selector.connect("clicked", self._on_select, file, hunk_idx, line_idx)
            # Put selector on the left, before +/-.
            #
            # GTK4 removed `Gtk.Container.get_children()` and `get_child(int)`,
            # so the two lines this replaces raised `AttributeError` on every
            # selectable diff line; `children` was assigned and never read.
            #
            # `prepend()` is the whole of the fix and needs no remove-then-insert:
            # a GTK4 `Gtk.Box` has no `insert_child_at_index` and no
            # `reorder_child`, which is what an earlier attempt here reached for
            # and got `AttributeError` for. Measured on the installed GTK 4.22.4.
            self.prepend(self._selector)

    def _on_select(self, selector: _LineSelector, file: DiffFile, hunk_idx: int,
                   line_idx: int) -> None:
        key = f"{file.path}:{hunk_idx}:{line_idx}"
        if key in file.selected_line_ids:
            file.selected_line_ids.discard(key)
        else:
            file.selected_line_ids.add(key)

    @property
    def selected(self) -> bool:
        return self._selector.selected


class _LineSelector(Gtk.Button):
    """A small handle to select a diff line (like in diff tools)."""

    def __init__(self) -> None:
        super().__init__()
        self.add_css_class("diff-line-select")
        self.set_valign(Gtk.Align.START)
        self.set_halign(Gtk.Align.START)
        self.set_size_request(24, 24)
        # `set_relief()`/`Gtk.ReliefStyle` are GTK3 and do not exist in GTK4 -
        # `Gtk.ReliefStyle` is not even an attribute, so this raised
        # `AttributeError` while building the line-select button, before the
        # panel could draw anything. "Flat" is the GTK4 spelling of the same
        # intent, and is what the rest of this tree uses for it.
        self.add_css_class("flat")
        self._selected = False

    @property
    def selected(self) -> bool:
        return self._selected

    def __click__(self) -> None:
        self._selected = not self._selected
        self.queue_draw()


def _make_css():
    css = Gtk.CssProvider()
    # Translucent tints, not fixed light colours: `#e6ffec` behind the theme's
    # own (light) text on a dark desktop was white on near-white - every added
    # line in the panel was unreadable, and so was the chat's inline diff, which
    # used the same class name. A tint over whatever is behind works both ways.
    css.load_from_data(
        b"""
        .diff-add { background-color: rgba(34,197,94,0.16); }
        .diff-remove { background-color: rgba(239,68,68,0.16); }
        .diff-change { background-color: rgba(245,158,11,0.16); }
        .diff-tag-add { color: rgba(34,197,94,0.95); font-weight: bold; }
        .diff-tag-remove { color: #ef4444; font-weight: bold; }
        .diff-line-add { color: rgba(34,197,94,0.95); }
        .diff-line-remove { color: #ef4444; }
        .diff-line-select { min-width: 4px; }
        .diff-line-select:selected { background-color: #888; }
        .diff-hunk { border-bottom: 1px solid alpha(currentColor, 0.15); padding: 4px 0; }
        .diff-hunk-header { opacity: 0.65; font-size: 0.9em; margin-bottom: 2px; }
        .diff-file-item { padding: 4px 8px; }
        .diff-file-item:selected { background-color: alpha(@accent_bg_color, 0.2); }
        .diff-file-item.changed { border-left: 3px solid #0366d6; }
        .diff-stats { opacity: 0.65; font-size: 0.85em; }
        .diff-accept { background-color: #1a7f37; color: white; border: none; border-radius: 4px; }
        .diff-reject { background-color: #cf222e; color: white; border: none; border-radius: 4px; }
        .diff-toolbar { padding: 6px; border-bottom: 1px solid alpha(currentColor, 0.15); }
        """)
    return css


# ---------------------------------------------------------------------------
# File list
# ---------------------------------------------------------------------------

class _DiffFileItem(Gtk.Box):
    """One file in the left-hand file list."""

    def __init__(self, file: DiffFile, selected: bool = False,
                 applied: bool = False) -> None:
        super().__init__(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        self.set_valign(Gtk.Align.CENTER)
        self._file = file
        self._selected = selected
        self._applied = applied
        self.add_css_class("diff-file-item")
        if selected:
            self.add_css_class("diff-file-item-selected")

        # Status icon
        icon = Gtk.Image.new_from_icon_name(self._status_icon())
        icon.set_size_request(14, 14)
        self.append(icon)

        # Filename
        name = Gtk.Label(label=str(file.path.name), xalign=0.0, wrap=True)
        name.set_xalign(0.0)
        name.set_margin_start(2)
        self.append(name)

        # Stats
        added = sum(1 for h in file.hunks for l in h.lines if l.tag == "+")
        removed = sum(1 for h in file.hunks for l in h.lines if l.tag == "-")
        stats = f"{added}+ {removed}-"
        stats_label = Gtk.Label(label=stats, xalign=1.0)
        stats_label.set_halign(Gtk.Align.END)
        stats_label.add_css_class("dim-label")
        stats_label.set_size_request(40, 0)
        self.append(stats_label)

        # Accept/Reject, for a change that has NOT been written yet. Absent
        # for one already on disk: these handlers are only ever wired by a
        # caller that stages a preview before writing, and no such caller
        # exists, so drawing them would put two dead buttons on every row.
        if not applied:
            buttons = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=4)
            accept = Gtk.Button(label="Accept")
            accept.set_tooltip_text("Accept all changes in this file")
            accept.connect("clicked", self._on_accept)
            buttons.append(accept)
            reject = Gtk.Button(label="Reject")
            reject.set_tooltip_text("Reject all changes in this file")
            reject.connect("clicked", self._on_reject)
            buttons.append(reject)
            self.append(buttons)

        self._name = name
        # `button-release-event` is a GTK3 signal and does not exist on a GTK4
        # widget, so connecting it raised `TypeError: unknown signal name` and
        # `_DiffFileItem` could not be constructed at all - which is why this
        # panel has never rendered. A `Gtk.GestureClick` is the GTK4 equivalent
        # and is the right shape here anyway: this is a `Gtk.Box`, not a button,
        # and it holds two real buttons that must keep their own clicks.
        click = Gtk.GestureClick()
        click.set_button(1)
        click.connect("pressed", self._on_click)
        self.add_controller(click)
        # The pointer cursor used to be requested from CSS (`cursor: pointer`),
        # which GTK4's parser rejects - `No property named "cursor"`, printed on
        # every window build for as long as the rule was there. `set_cursor` is
        # the supported route, and it only exists from GTK 4.10, so this is
        # guarded rather than assumed: on an older GTK the row still works, it
        # just shows the default arrow.
        if hasattr(self, "set_cursor"):
            try:
                self.set_cursor(Gdk.Cursor.new_from_name("pointer"))
            except Exception:  # noqa: BLE001 - a cursor is not worth a failed row
                pass

    def _status_icon(self) -> str:
        return {"modified": "document-edit-symbolic", "added": "list-add-symbolic",
                "deleted": "list-remove-symbolic", "unchanged": ""}[self._file.status]

    def _on_accept(self, button: Gtk.Button) -> None:
        if self.on_accept:
            self.on_accept(self._file)

    def _on_reject(self, button: Gtk.Button) -> None:
        if self.on_reject:
            self.on_reject(self._file)

    def _on_click(self, gesture: "Gtk.GestureClick", n_press: int, x: float, y: float) -> None:
        # A `Gtk.GestureClick` emits `pressed` with (gesture, n_press, x, y) -
        # there is no `Gdk.Event` to inspect for a press type, because the
        # signal *is* the press.
        if self.on_select:
            self.on_select(self._file)

    @property
    def selected(self) -> bool:
        return self._selected

    @selected.setter
    def selected(self, value: bool) -> None:
        self._selected = value
        if value:
            self.add_css_class("diff-file-item-selected")
        else:
            self.remove_css_class("diff-file-item-selected")

    on_accept = None
    on_reject = None
    on_select = None


# ---------------------------------------------------------------------------
# Hunk view
# ---------------------------------------------------------------------------

class _DiffHunkView(Gtk.Box):
    """Render one hunk with all its lines."""

    def __init__(self, file: DiffFile, hunk_idx: int, hunk: DiffHunk,
                 selectable: bool = True) -> None:
        super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        self.add_css_class("diff-hunk")

        # GTK4 has no `markup` constructor property on `Gtk.Label` - passing one
        # raises `TypeError: gobject 'GtkLabel' doesn't support property
        # 'markup'`. `set_markup()` is the spelling in every version.
        header = Gtk.Label()
        # The class goes on the widget, not in the markup: Pango rejects
        # `class` on `<span>` ("Attribute 'class' is not allowed on the <span>
        # tag"), which meant the hunk header was never styled even once the
        # markup parsed.
        header.add_css_class("diff-hunk-header")
        # `set_text`, not `set_markup`. The header is styled by its CSS class,
        # so markup bought nothing, and a `@@` hunk header is Pango markup's
        # least favourite string in the world: any later addition of `&`, `<` or
        # `>` to these numbers would need escaping that this f-string does not
        # do. (Checked rather than assumed: `get_text()` on the built label
        # returns exactly `@@ -1,1 +0,1 @@`, and a plain label with the same
        # string renders identically either way.)
        header.set_text(
            f"@@ -{hunk.old_start},{hunk.old_count} "
            f"+{hunk.new_start},{hunk.new_count} @@")
        self.append(header)

        for line_idx, line in enumerate(hunk.lines):
            self.append(_DiffLineBox(file, hunk_idx, line_idx, line, selectable=selectable))


# ---------------------------------------------------------------------------
# Main diff view
# ---------------------------------------------------------------------------

class _DiffView(Gtk.Box):
    """The diff view: file list on the left, diff on the right."""

    def __init__(self, app: Any) -> None:
        super().__init__(orientation=Gtk.Orientation.HORIZONTAL, spacing=12)
        self.set_halign(Gtk.Align.FILL)
        self.set_valign(Gtk.Align.FILL)
        self._app = app
        self._files: Dict[Path, DiffFile] = {}
        self._selected: Set[Path] = set()
        #: Whether what is on screen has already been written to disk. The
        #: panel's whole honesty turns on this: Accept/Reject are only drawn
        #: for a change that has *not* happened yet, and nothing in this app
        #: stages one, so in practice they are absent rather than inert.
        self._applied = False
        self.on_accept_file = None
        self.on_reject_file = None
        #: The panel's health, and the row it draws from it. `build()` hands the
        #: recorder's `status()` to the sidebar dot; `_render_files` redraws the
        #: row whenever the diff set changes, so the two read one value.
        self.status_recorder = common.StatusRecorder()
        self._status_row_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)

        # Add CSS
        css = _make_css()
        Gtk.StyleContext.add_provider_for_display(
            Gdk.Display.get_default() or Gtk.Display.get_default(), css,
            Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION)

        # The status row is a full-width header, and this box is horizontal (the
        # file list beside the diff), so it cannot be a child of it: `build()`
        # stacks `_status_row_box` above this view instead. The initial draw
        # happens here so the panel never renders one frame without the row the
        # sidebar dot is already answering from.
        self._sync_status([], 0)

        # Left: file list.
        # `_files_box` is kept, and everything below mutates it directly, because
        # going back through the scroller does not work: `set_child()` wraps a
        # non-scrollable child - a plain `Gtk.Box` - in a `Gtk.Viewport`, so
        # `get_child()` returns the *Viewport*, and `Gtk.Viewport` in GTK4 has
        # `set_child()` rather than `append`/`remove`. Reaching through it raised
        # `AttributeError: 'Viewport' object has no attribute 'remove'` on every
        # single render, so this panel never drew at all.
        #
        # The right pane three lines below already does it the other way - it
        # keeps `self._diff_box` and mutates that - and this was the odd one out.
        self._files_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        self._files_box.set_hexpand(False)
        self._files_scrolled = Gtk.ScrolledWindow()
        # The width is asked for on the scroller, not on the box inside it: a
        # 320px box in a scroller that asked for nothing was clipped to a few
        # characters - rendered, `groceries.md` read `gro`.
        self._files_scrolled.set_min_content_width(220)
        self._files_scrolled.set_vexpand(True)
        self._files_scrolled.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        self._files_scrolled.set_child(self._files_box)
        self.append(self._files_scrolled)

        # Right: diff view
        right = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        right.set_hexpand(True)
        right.set_halign(Gtk.Align.FILL)

        # Toolbar
        toolbar = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=12)
        toolbar.add_css_class("diff-toolbar")
        self._count_label = Gtk.Label(label="0 files changed")
        toolbar.append(self._count_label)
        # Into the right-hand column, above the diff. It was appended to this
        # horizontal box, so it became a third column drawn between the file
        # list and the diff, on top of the file name.
        right.append(toolbar)

        # Diff content
        self._diff_scrolled = Gtk.ScrolledWindow()
        self._diff_scrolled.set_policy(Gtk.PolicyType.AUTOMATIC, Gtk.PolicyType.AUTOMATIC)
        # Without this the scroller got its minimum height - about one line -
        # and a five-line change showed its first line and nothing else.
        self._diff_scrolled.set_vexpand(True)
        self._diff_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        self._diff_scrolled.set_child(self._diff_box)
        right.append(self._diff_scrolled)
        self.append(right)

        # Initial render
        self._render_files()

    def set_diffs(self, files: Dict[Path, Tuple[str, str]],
                  applied: bool = False) -> None:
        """Set the diffs to show: path -> (old, new).

        `applied=True` says the change is **already on disk** - which is the
        only kind this panel has ever had. Accept/Reject are then not drawn,
        because nothing here decides whether a write happens: the write
        happened, and the way back is the `undo_last_change` skill. A button
        labelled "Reject" that calls no handler is a control that lies, and a
        button that claimed to undo something the panel cannot undo would be
        worse.
        """
        self._files.clear()
        self._applied = applied
        for path, (old, new) in files.items():
            self._files[path] = compute_file_diff(path, old, new)
        self._render_files()

    def load_recent_changes(self) -> int:
        """Stage what Chronoa has already changed, from the undo ring.

        This is what makes the panel a preview rather than a shell: without it
        `set_diffs` had no caller anywhere in the app, so the page could only
        ever render "Nothing to compare yet" and the sidebar dot answered
        about a panel that never saw a diff. Returns how many were staged.
        """
        try:
            from shani_chronoa.skills.undo_last_change import recent_changes
            pairs = recent_changes()
        except Exception as exc:  # noqa: BLE001 - a broken ring is an empty panel, not a crash
            logger.info("Could not read the undo ring for a preview: %s", exc)
            return 0
        if not pairs:
            # Cleared rather than left alone: an undo empties the ring, and a
            # panel still listing the undone file would be describing the past.
            if self._files:
                self.set_diffs({}, applied=True)
            return 0
        self.set_diffs({path: (before, after) for path, before, after in pairs},
                       applied=True)
        return len(pairs)

    def _render_files(self) -> None:
        # Left: file list. Mutated through `_files_box`, never through the
        # scroller - see the note where it is built.
        child = self._files_box
        while child.get_first_child() is not None:
            child.remove(child.get_first_child())
        for path, f in self._files.items():
            if f.status == "unchanged":
                continue
            item = _DiffFileItem(f, applied=self._applied)
            item.on_accept = self._accept_file
            item.on_reject = self._reject_file
            item.on_select = self._select_file
            if path in self._selected:
                item.selected = True
            child.append(item)

        # Right: diff content
        child = self._diff_box.get_first_child()
        while child is not None:
            next_child = child.get_next_sibling()
            self._diff_box.remove(child)
            child = next_child

        changed = [f for f in self._files.values() if f.status != "unchanged"]
        total_added = sum(sum(1 for h in f.hunks for l in h.lines if l.tag == "+")
                          for f in changed)
        # The panel's own health, written through the shared recorder so the
        # sidebar dot and the row below it cannot disagree. It is recorded here,
        # beside the count the two are both derived from, rather than in a
        # second place that would be free to fall out of date.
        self._sync_status(changed, total_added)
        total_removed = sum(sum(1 for h in f.hunks for l in h.lines if l.tag == "-")
                            for f in changed)
        self._count_label.set_text(
            f"{len(changed)} file(s) changed - {total_added}+ {total_removed}-")
        # With nothing changed the toolbar said "0 file(s) changed - 0+ 0-" under
        # a status row saying the same, over a label saying "No changes." -
        # three statements of one fact. The status row keeps it; the empty
        # state says what will appear here.
        self._count_label.get_parent().set_visible(bool(changed))
        self._files_scrolled.set_visible(bool(changed))

        if not changed:
            self._diff_box.append(common.empty_state(
                ICON, "No changes to show",
                "When Chronoa edits or writes a file, the change appears here "
                "line by line, and in the conversation under the step that made it."))
            return

        for path, f in self._files.items():
            if f.status == "unchanged":
                continue
            if f.hunks:
                for hunk_idx, hunk in enumerate(f.hunks):
                    self._diff_box.append(_DiffHunkView(f, hunk_idx, hunk))
            else:
                self._diff_box.append(
                    Gtk.Label(label=f"No hunks (empty change) - {f.status}"))

    def _sync_status(self, changed: "List[DiffFile]", added: int) -> None:
        """Record and redraw this panel's health row, in one place.

        `common.StatusRecorder` exists because a status written in one place and
        computed in another is a dot that lies; the row is rebuilt (rather than
        the old label edited in place) because `common.status_row` is what
        applies the state CSS class the dot promises.
        """
        child = self._status_row_box.get_first_child()
        while child is not None:
            following = child.get_next_sibling()
            self._status_row_box.remove(child)
            child = following
        if not self._files:
            self._status_row_box.append(self.status_recorder.row(
                common.STATUS_OK, "Nothing to compare yet",
                "Chronoa shows here the files it has changed and can still undo."))
        elif not changed:
            self._status_row_box.append(self.status_recorder.row(
                common.STATUS_OK, "Nothing changed since the last pre-image",
                f"{len(self._files)} file(s) in the undo ring, every one back "
                "where it started"))
        elif self._applied:
            self._status_row_box.append(self.status_recorder.row(
                common.STATUS_OK,
                f"{len(changed)} file(s) Chronoa changed (already written)",
                f"{added} line(s) added. To put one back, ask Chronoa to "
                "undo the last change - this panel does not decide that."))
        else:
            self._status_row_box.append(self.status_recorder.row(
                common.STATUS_ATTENTION,
                f"{len(changed)} file(s) awaiting your decision",
                f"{added} line(s) added; nothing is written until you accept"))

    def _accept_file(self, file: DiffFile) -> None:
        if self.on_accept_file:
            self.on_accept_file(file)

    def _reject_file(self, file: DiffFile) -> None:
        if self.on_reject_file:
            self.on_reject_file(file)

    def _select_file(self, file: DiffFile) -> None:
        if file.path in self._selected:
            self._selected.discard(file.path)
        else:
            self._selected.add(file.path)
        self._render_files()


# ---------------------------------------------------------------------------
# Build
# ---------------------------------------------------------------------------

def build(app: Any) -> Gtk.Widget:
    """Build the diff page for `app`."""
    page, set_content = common.surface(TITLE, SUBTITLE)
    view = _DiffView(app)
    # Stage the preview at build time. Nothing else ever called `set_diffs`,
    # so before this the panel had no content and no way to acquire any: a
    # surface registered in the sidebar that renders one fixed sentence is the
    # dead-control class this repo keeps meeting.
    view.load_recent_changes()
    # The status row above the two panes, then the panes. Wrapped rather than
    # appended into the view, because the view is a horizontal box and a header
    # in the middle of one is not a header.
    shell = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
    shell.set_vexpand(True)
    shell.append(view._status_row_box)
    shell.append(view)
    set_content(shell)
    # Re-read on every showing. The window builds a panel once and keeps it, so
    # a load at build time alone froze the panel at the first time it was
    # opened: open Diff, let Chronoa write another file, open Diff again, and
    # the new file was not there.
    if hasattr(page, "connect") and common.adw_ready():
        page.connect("showing", lambda *_a: view.load_recent_changes())
    page.view = view
    page.set_diffs = view.set_diffs
    #: The sidebar dot reads this; the panel's row is drawn from the same
    #: recorder. Without it every surface walker hit
    #: `AttributeError: 'NavigationPage' object has no attribute 'status'`.
    page.status = view.status_recorder.status
    return page
