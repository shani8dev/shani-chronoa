"""Artifact download store: where models, voices, documents, exports live.

A world-class artifact store is both a catalog and a manager:
- **Catalog**: what's available, what's installed, what's in progress
- **Manager**: download, resume, verify integrity, organize, clean

This panel shows three perspectives:
1. **Downloads** — files you have downloaded (models, voices, documents)
2. **Exports** — files you created (conversations, facts, reports)
3. **System** — where the runtime reads its defaults (stash, cache, data home)

The store knows about:
- Whisper.cpp models (`.pt`, `.bin`)
- Piper voices (`.phonemes`, `.json`)
- Ollama models (`.gguf`)
- Hugging Face models (`.gguf`)
- Code snippets (Python, shell, etc.)
- Exported conversations and facts
- Any other file that could be shared between sessions

All artifacts are scoped to the user's home (`~/.local/share/shani-chronoa`) to respect
local-first and keep nothing in the package itself.
"""

from __future__ import annotations

import datetime
import hashlib
import logging
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Any, Dict, List, Optional

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gio, Gtk, Pango  # noqa: E402

from shani_chronoa.gui.surfaces import common  # noqa: E402
from shani_chronoa.words import plural

logger = logging.getLogger(__name__)

TITLE = "Artifacts"
# Not the download arrow: Export uses `document-save-symbolic`, which Adwaita
# draws as the same arrow, so the two neighbouring rows had one glyph.
ICON = "folder-documents-symbolic"
SECTION = "Acting"

SUBTITLE = (
    "Where models, voices, documents and exports are stored. "
    "Browse, search, download and clean up your local files."
)


# ---------------------------------------------------------------------------
# Artifact data model
# ---------------------------------------------------------------------------

class ArtifactCategory(str, Enum):
    """The categories the filter offers.

    **This was missing and the module could not run without it.**
    `_CategoryFilter` iterated `ArtifactCategory` to build its toggle buttons and
    raised `NameError: name 'ArtifactCategory' is not defined`, which took the
    whole Artifacts surface down at build time - the same class of defect this
    repository has already found once in `midi.py` (`NameError: name 'os' is
    not defined` on a line nothing imported).

    The members are exactly the strings `_artifact_category()` returns, and they
    are `str` subclasses so a member can be compared to, stored in, and used as
    the `Artifact.category` field without conversion. That is why `ALL` can
    stand in for "no filter" rather than being a separate sentinel the call
    sites would each have to special-case.
    """

    ALL = "all"
    MODEL = "model"
    VOICE = "voice"
    DOCUMENT = "document"
    EXPORT = "export"


@dataclass
class Artifact:
    """One artifact (file) in the store."""
    name: str
    path: Path
    category: str
    size: int
    modified: datetime.datetime
    checksum: Optional[str]
    description: str


# ---------------------------------------------------------------------------
# Artifact discovery
# ---------------------------------------------------------------------------

def _discover_artifacts(home: Path) -> List[Artifact]:
    """Walk `home/.local/share/shani-chronoa` and return known artifact paths.

    The artifact store knows about:
    - Model caches (whisper.cpp, piper, ollama, huggingface)
    - Voice packs (piper, kokoro)
    - Exported files (conversations, facts)
    - Application caches and temporary downloads
    """
    artifacts: List[Artifact] = []
    artifacts_dir = home / ".local" / "share" / "shani-chronoa"

    if not artifacts_dir.exists():
        return artifacts

    for entry in artifacts_dir.rglob("*"):
        if entry.is_file() and not entry.is_symlink():
            try:
                stat = entry.stat()
                size = stat.st_size
                mtime = datetime.datetime.fromtimestamp(stat.st_mtime, tz=datetime.timezone.utc)
                
                # Determine category based on path and filename
                category = _artifact_category(entry)
                
                # Simple description based on file extension and location
                desc = _artifact_description(entry, category)
                
                # Compute checksum for small files (<= 10MB)
                checksum = None
                if size <= 10 * 1024 * 1024:
                    try:
                        checksum = _compute_sha256(entry)
                    except (OSError, IOError):
                        pass

                artifacts.append(Artifact(
                    name=entry.name,
                    path=entry,
                    category=category,
                    size=size,
                    modified=mtime,
                    checksum=checksum,
                    description=desc,
                ))
            except (OSError, IOError) as exc:
                logger.warning("Could not read artifact %s: %s", entry, exc)

    # Sort by name for stable ordering
    artifacts.sort(key=lambda a: a.path)
    return artifacts


def _artifact_category(path: Path) -> str:
    """Determine which category an artifact belongs to."""
    # Look at the parent directory name
    parent_name = path.parent.name.lower()
    
    # Model stores
    if "llm-models" in parent_name or "models" in parent_name or "model" in parent_name:
        return "model"
    
    # Voice stores  
    if "voices" in parent_name or "piper" in parent_name or "voice" in parent_name:
        return "voice"
    
    # Documents
    if "documents" in parent_name or "docs" in parent_name:
        return "document"
    
    # Exports (conversation stores)
    if path.name.startswith("chronoa-conversation-export"):
        return "export"
    if path.name.startswith("chronoa-facts-export"):
        return "export"
    
    # Conversation stores themselves
    if path.name.endswith(".jsonl") and "sessions" in str(path.parent):
        return "export"
    
    # Default based on extension
    ext = path.suffix.lower()
    if ext in (".pt", ".bin", ".gguf", ".safetensors", ".onnx"):
        return "model"
    elif ext in (".phonemes", ".json"):
        return "voice"
    elif ext in (".md", ".txt", ".docx", ".xlsx", ".pdf"):
        return "document"
    elif ext in (".json", ".md", ".txt"):
        # Check if it's in the conversation store
        if "sessions" in str(path.parent):
            return "export"
        return "document"
    
    return "all"


def _artifact_description(path: Path, category: str) -> str:
    """Human-readable description of an artifact."""
    parent_name = path.parent.name
    
    if category == "model":
        if "whisper" in parent_name.lower():
            return f"Whisper.cpp speech model ({path.suffix})"
        elif "ollama" in parent_name.lower():
            return f"Ollama model ({path.suffix})"
        elif "huggingface" in parent_name.lower():
            return f"Hugging Face model ({path.suffix})"
        else:
            return f"Model cache ({path.suffix})"
    
    elif category == "voice":
        if "piper" in parent_name.lower():
            return f"Piper voice pack ({path.name})"
        else:
            return f"Voice data ({path.suffix})"
    
    elif category == "document":
        if path.suffix == ".md":
            return "Markdown document"
        elif path.suffix == ".txt":
            return "Text document"
        elif path.suffix == ".pdf":
            return "PDF document"
        elif path.suffix in (".docx", ".xlsx", ".pptx"):
            return "Office document"
        else:
            return f"Document ({path.suffix})"
    
    elif category == "export":
        if "chronoa-conversation-export" in path.name:
            return "Exported conversation"
        elif "chronoa-facts-export" in path.name:
            return "Exported facts"
        else:
            return "Export file"
    
    return "Artifact"


def _compute_sha256(path: Path) -> str:
    """Compute SHA256 checksum of a file."""
    sha256 = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(4096), b""):
            sha256.update(chunk)
    return sha256.hexdigest()


# ---------------------------------------------------------------------------
# GTK widgets
# ---------------------------------------------------------------------------

class _CategoryFilter(Gtk.Box):
    """Filter by category using toggle buttons."""

    def __init__(self) -> None:
        super().__init__(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        self.add_css_class("category-filter")
        self._buttons: Dict[str, Gtk.ToggleButton] = {}
        categories = [cat.value for cat in ArtifactCategory if cat != ArtifactCategory.ALL]
        
        for cat in categories:
            btn = Gtk.ToggleButton(label=cat.title())
            btn.set_tooltip_text(f"Show {cat} artifacts")
            btn.set_active(False)
            btn.connect("toggled", self._on_category_toggled)
            self._buttons[cat] = btn
            self.append(btn)

    #: **Set by the view, which owns what is shown.** This handler used to be
    #: `pass`, so every button was connected to a no-op: ticking a category
    #: changed the button and nothing else. Measured - `_selected_category` is
    #: written in one place only, inside the view's own copy of this handler,
    #: and that copy was connected to nothing, so it stayed `"all"` for the
    #: life of the window and the filter never filtered.
    on_toggle = None

    def _on_category_toggled(self, button: Gtk.ToggleButton) -> None:
        """Hand the click up.

        The filter owns the buttons and knows which one is active; the view
        owns `_selected_category` and `_apply_filters`. This is the whole of
        the connection between them.
        """
        if self.on_toggle is not None:
            self.on_toggle(button)


class _ArtifactRow(Gtk.Box):
    """One row per artifact in the list."""

    def __init__(self, artifact: Artifact) -> None:
        super().__init__(orientation=Gtk.Orientation.HORIZONTAL, spacing=12)
        self.set_valign(Gtk.Align.CENTER)
        self.set_hexpand(True)
        self._artifact = artifact

        # Icon
        icon_name = _artifact_icon(artifact.category)
        icon = Gtk.Image.new_from_icon_name(icon_name)
        icon.set_size_request(32, 32)
        icon.add_css_class("artifact-icon")
        self.append(icon)

        # Info column
        info_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        
        # Name with path tooltip.
        # Ellipsised and capped, not wrapped: a filename should truncate, and an
        # uncapped label reports its whole natural width as the row's minimum,
        # which is what pushed this row to 528px in a 480px window and put a
        # sideways scrollbar under the page. The full path is in the tooltip, so
        # nothing is lost by showing less of it.
        name = Gtk.Label(label=artifact.name, xalign=0.0)
        name.add_css_class("artifact-name")
        name.set_tooltip_text(str(artifact.path))
        name.set_ellipsize(Pango.EllipsizeMode.END)
        name.set_max_width_chars(34)
        info_box.append(name)

        # Description. `set_max_width_chars` alone does not wrap - it caps the
        # wrap width *if* the label wraps - so `set_wrap(True)` has to come too or
        # the description reports its full unwrapped run.
        desc = common.wrap_label(Gtk.Label(label=artifact.description, xalign=0.0), chars=40)
        desc.add_css_class("dim-label")
        info_box.append(desc)
        
        # Metadata row
        meta = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=12)
        
        # Size
        size_label = Gtk.Label(label=self._format_size(artifact.size), xalign=0.0)
        meta.append(size_label)
        
        # Modified date
        mod_text = artifact.modified.strftime("%Y-%m-%d %H:%M")
        time_label = Gtk.Label(label=mod_text, xalign=0.0)
        time_label.add_css_class("dim-label")
        meta.append(time_label)
        
        # Checksum: in the tooltip, not on the row. Sixteen hex digits are noise
        # to a person deciding what to delete; the full sum is one hover away.
        if artifact.checksum:
            name.set_tooltip_text(f"{artifact.path}\nSHA256 {artifact.checksum}")
        
        info_box.append(meta)
        self.append(info_box)

        # Open, Show, Trash: the panel promises "clean up" and had only Show.
        actions = Gtk.Box(spacing=4, valign=Gtk.Align.CENTER)
        open_btn = Gtk.Button(icon_name="document-open-symbolic", tooltip_text="Open")
        open_btn.connect("clicked", lambda *_: self._open(artifact.path))
        path_btn = Gtk.Button(label="Show")
        path_btn.set_tooltip_text("Show in file manager")
        path_btn.connect("clicked", self._on_path_clicked, artifact.path)
        path_btn.add_css_class("path-button")
        trash_btn = Gtk.Button(icon_name="user-trash-symbolic",
                               tooltip_text="Move to Trash (it can be restored from there)")
        trash_btn.add_css_class("destructive-action")
        trash_btn.connect("clicked", lambda *_: self._confirm_trash(artifact))
        for button in (open_btn, path_btn, trash_btn):
            button.add_css_class("flat")
            button.update_property([Gtk.AccessibleProperty.LABEL],
                                   [button.get_tooltip_text() or button.get_label()])
            actions.append(button)
        self.append(actions)

    def _open(self, path: Path) -> None:
        try:
            Gio.AppInfo.launch_default_for_uri(path.as_uri(), None)
        except Exception as exc:  # noqa: BLE001
            logger.warning("Could not open %s: %s", path, exc)

    def _confirm_trash(self, artifact: "Artifact") -> None:
        """Ask, then move to the Trash - reversible, unlike deleting."""
        dialog = Adw.AlertDialog(
            heading=f"Move {artifact.name} to the Trash?",
            body=(f"{self._format_size(artifact.size)} - {artifact.description}. "
                  "You can restore it from the Trash."))
        dialog.add_response("cancel", "Cancel")
        dialog.add_response("trash", "Move to Trash")
        dialog.set_response_appearance("trash", Adw.ResponseAppearance.DESTRUCTIVE)
        dialog.set_default_response("cancel")

        def chosen(_d, response: str) -> None:
            if response != "trash":
                return
            try:
                Gio.File.new_for_path(str(artifact.path)).trash(None)
            except Exception as exc:  # noqa: BLE001 - said on the row, not raised
                self.set_tooltip_text(f"Could not move it to the Trash: {exc}")
                return
            parent = self.get_parent()
            if parent is not None:
                parent.remove(self)
        dialog.connect("response", chosen)
        dialog.present(self.get_root())

    def _format_size(self, size: int) -> str:
        """Format size in human-readable units."""
        for unit in ["B", "KB", "MB", "GB", "TB"]:
            if size < 1024.0 or unit == "TB":
                return f"{size:.1f} {unit}"
            size /= 1024.0

    def _on_path_clicked(self, button: Gtk.Button, path: Path) -> None:
        """Open the artifact's directory in the file manager.

        Through GIO, not `subprocess`. This module never imported `subprocess`,
        so every press raised `NameError` - and the `except Exception` below
        turned that into a log line, so the button silently did nothing on
        every machine (found by pyflakes, 2026-10-08). GIO also returns at once
        rather than blocking the window on `xdg-open`.
        """
        try:
            Gio.AppInfo.launch_default_for_uri(path.parent.as_uri(), None)
        except Exception as exc:
            logger.warning("Could not open file manager: %s", exc)


def _artifact_icon(category: str) -> str:
    """Icon name for an artifact category."""
    return {
        "model": "media-optical-symbolic",
        "voice": "audio-input-microphone-symbolic",
        "document": "text-x-generic-symbolic",
        "export": "document-save-symbolic",
        "system": "applications-system-symbolic",
        "all": "folder-symbolic",
    }.get(category, "unknown")


# ---------------------------------------------------------------------------
# Main artifact store view
# ---------------------------------------------------------------------------

def _human(size: float) -> str:
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if size < 1024 or unit == "TB":
            return f"{size:.1f} {unit}" if unit != "B" else f"{int(size)} B"
        size /= 1024


class _ArtifactStoreView(Gtk.Box):
    """The artifact store: artifact list + filters + actions."""

    def __init__(self, app: Any) -> None:
        super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        self._app = app
        self._artifacts: List[Artifact] = []
        self._filtered: List[Artifact] = []
        self._selected_category: str = "all"
        self._search_text: str = ""

        # **No title block here.** There was a `title-1` "Artifact Store" heading
        # and a second subtitle inside the content, while `common.surface()`
        # already renders `TITLE` in the header bar and `SUBTITLE` under it - so
        # the panel showed its name twice, in two different sizes, with two
        # different sentences under each. The window owns the title; a panel
        # that repeats it is a second heading for the same page.

        # Space used, by category - what "clean up" is weighed against.
        from shani_chronoa.gui import charts
        self._space_card, space_body = charts.card("Space used", "drive-harddisk-symbolic")
        self._space_card.set_margin_bottom(8)
        self._space_total = Gtk.Label(xalign=0.0)
        self._space_total.add_css_class("title-3")
        space_body.append(self._space_total)
        self._space_bars = charts.HBars(colour=charts.PURPLE)
        space_body.append(self._space_bars)
        self.append(self._space_card)

        # Filter section
        filter_section = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        filter_section.add_css_class("section")
        
        filter_label = Gtk.Label(label="Filter by category")
        filter_label.add_css_class("dim-label")
        filter_section.append(filter_label)
        
        self._category_filter = _CategoryFilter()
        # **Without this the four category buttons are inert.** The filter
        # builds them and connects them to its own handler, which did nothing;
        # the handler that filters lives here and was connected to nothing.
        self._category_filter.on_toggle = self._on_category_toggled
        filter_section.append(self._category_filter)
        
        self.append(filter_section)

        # Search section
        search_section = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=12)
        search_section.add_css_class("section")
        
        search_entry = Gtk.Entry()
        search_entry.set_placeholder_text("Search artifacts...")
        search_entry.set_hexpand(True)
        search_entry.connect("changed", self._on_search_text_changed)
        # **Held, not searched for.** `Clear` used to find this entry by
        # walking the widget tree for a placeholder containing "Search" - and
        # the walk called `get_children()`, which **a GTK4 `Gtk.Box` does not
        # have** (measured: `hasattr(Gtk.Box, "get_children")` is False). So
        # pressing **Clear** raised
        # `AttributeError: '_ArtifactStoreView' object has no attribute
        # 'get_children'` on every press, on a button that is wired at line
        # 435. The entry is built once, right here, and already connected -
        # so holding it is exact, and a search for it by its own placeholder
        # is a second way to answer a question this already knows the answer
        # to. `tools/member_sweep.py` found it; no test and no lint did.
        self._search_entry = search_entry
        
        clear_btn = Gtk.Button(label="Clear")
        clear_btn.set_tooltip_text("Clear search")
        clear_btn.connect("clicked", self._on_clear_search)
        
        search_section.append(search_entry)
        self._search_section = search_section
        search_section.append(clear_btn)
        self.append(search_section)

        # **No column header.** There was a horizontal bar reading
        # "Name | Description | Metadata" above a list whose rows are not columns
        # at all - each row is an icon beside a *stacked* block of name, then
        # description, then metadata (see `_Row`). Two independent boxes cannot
        # align their children, so the headings never sat over the thing they
        # named: on the rendered panel "Metadata" sat above the file name. A
        # heading that points at the wrong line is worse than no heading, and
        # the row already labels itself - name in bold, description dimmed,
        # size and date on a third line.

        # Artifact list
        self._list_scrolled = Gtk.ScrolledWindow()
        self._list_scrolled.set_policy(Gtk.PolicyType.AUTOMATIC, Gtk.PolicyType.AUTOMATIC)
        self._list_scrolled.set_vexpand(True)
        self._list_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        self._list_scrolled.set_child(self._list_box)
        self.append(self._list_scrolled)

        # Status bar
        #: The panel's health, written once and read twice - the label in the
        #: panel and the dot on the sidebar row - through the shared recorder
        #: (`common.StatusRecorder`). The label used to be a free-standing
        #: Gtk.Label whose text the sidebar could not see, so this panel
        #: answered "no status at all" to every walker even while it was
        #: rendering a count. `window._panel_status` needs a `status()`
        #: callable on the page.
        self.status_recorder = common.StatusRecorder()
        self._status_row_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        self._status_row_box.append(self.status_recorder.row(
            common.STATUS_UNKNOWN, "Looking for artifacts", "The home directory is being read."))
        # First, as on every other panel: it was the last thing on the page,
        # under the list it summarises, with "No artifacts found." above it and
        # a stray "0 total artifacts" below - three lines in the wrong order.
        self.append(self._status_row_box)
        self.reorder_child_after(self._status_row_box, None)
        # The count belongs to the search it counts, so it sits in that row,
        # before Clear.
        self._status_label = Gtk.Label(label="Loading...")
        self._status_label.add_css_class("dim-label")
        self._search_section.insert_child_after(
            self._status_label, self._search_section.get_first_child())

        # Load artifacts
        self._load_artifacts()

    # -- actions ---------------------------------------------------------

    def _show_space(self) -> None:
        from collections import defaultdict
        sizes = defaultdict(int)
        for artifact in self._artifacts:
            name = str(getattr(artifact.category, "value", artifact.category)).split(".")[-1]
            # The enum's catch-all doubles as the "all" filter; in a size chart
            # it means files that fit no category.
            label = "Other" if name.lower() == "all" else name.replace("_", " ").capitalize()
            sizes[label] += artifact.size
        total = sum(sizes.values())
        self._space_card.set_visible(bool(total))
        self._space_total.set_text(f"{_human(total)} in {len(self._artifacts)} files")
        rows = sorted(sizes.items(), key=lambda kv: -kv[1])
        self._space_bars.set(rows, values=[_human(v) for _k, v in rows])

    def _load_artifacts(self) -> None:
        """Load artifacts from the home directory."""
        try:
            home = Path.home()
            self._artifacts = _discover_artifacts(home)
            self._show_space()
            self._apply_filters()
        except Exception as exc:  # noqa: BLE001
            logger.warning("Could not load artifacts", exc_info=True)
            self._show_error(f"Failed to load artifacts: {exc}")

    def _on_category_toggled(self, button: Gtk.ToggleButton) -> None:
        """Recompute which category is showing from the buttons, every time.

        **Unticking the last one goes back to `ALL`.** This assigned only
        inside `if btn.get_active()`, so with no button active the previous
        category simply survived - and the list could never return to showing
        everything. Measured before this change: tick **Document**, untick it,
        and `_selected_category` stayed `"document"`.
        """
        selected = ArtifactCategory.ALL.value
        for cat, btn in self._category_filter._buttons.items():
            if btn.get_active():
                selected = cat
                break
        self._selected_category = selected
        self._apply_filters()

    def _on_search_text_changed(self, entry: Gtk.Entry) -> None:
        self._search_text = entry.get_text().lower()
        self._apply_filters()

    def _on_clear_search(self, button: Gtk.Button) -> None:
        entry = self._find_search_entry()
        if entry:
            entry.set_text("")
        self._search_text = ""
        self._apply_filters()

    def _find_search_entry(self) -> Optional[Gtk.Entry]:
        """The search entry, which `__init__` built and this class holds.

        **Named for what it did, kept for what it is.** It walked the widget
        tree and matched on the placeholder text; it now returns the reference.
        Returning `None` rather than raising keeps `Clear` safe on a view whose
        entry was never built, which is the only case that can reach it now.
        """
        return getattr(self, "_search_entry", None)

    def _apply_filters(self) -> None:
        """Get the artifacts that match the current filters."""
        self._filtered = []
        for artifact in self._artifacts:
            # Category filter
            if self._selected_category != "all" and artifact.category != self._selected_category:
                continue
            
            # Search filter
            if self._search_text:
                searchable = (
                    artifact.name.lower() +
                    artifact.description.lower() +
                    str(artifact.path).lower()
                )
                if self._search_text not in searchable:
                    continue
            
            self._filtered.append(artifact)
        
        self._render_list()

    def _render_list(self) -> None:
        """Render the filtered artifact list."""
        # Clear previous list
        child = self._list_box.get_first_child()
        while child is not None:
            next_child = child.get_next_sibling()
            self._list_box.remove(child)
            child = next_child

        if not self._filtered:
            empty = common.empty_state(
                ICON,
                "No artifacts yet" if not self._artifacts else "Nothing matches",
                "Models, voices, documents and exports Chronoa saves will be "
                "listed here." if not self._artifacts else
                "No artifact matches the category or the search. Clear them to see all.")
            empty.set_vexpand(True)
            self._list_box.append(empty)
            self._status_label.set_text(f"{len(self._artifacts)} total artifacts")
            self._set_status(
                common.STATUS_OK,
                ("Nothing in the home directory looks like an artifact" if not self._artifacts
                 else "No artifact matches the current filter"),
                f"{plural(len(self._artifacts), 'artifact')} found before filtering")
            return

        # Render artifacts
        for artifact in self._filtered:
            self._list_box.append(_ArtifactRow(artifact))

        self._status_label.set_text(f"{len(self._filtered)} of {len(self._artifacts)} artifacts")
        self._set_status(
            common.STATUS_OK,
            f"{len(self._filtered)} of {plural(len(self._artifacts), 'artifact')} shown",
            "the filter above narrows what the list holds")

    def _set_status(self, status: str, summary: str, detail: str = "") -> None:
        """Record the panel's health *and* redraw its row, in one place.

        Rebuilding the row rather than swapping the word inside the old label
        is the point: `common.status_row` is what applies the state CSS class,
        so a label edited in place is exactly the case
        `test_the_dot_agrees_with_the_row_the_panel_shows` exists to catch - the
        sidebar would claim a colour the panel never drew.
        """
        child = self._status_row_box.get_first_child()
        while child is not None:
            following = child.get_next_sibling()
            self._status_row_box.remove(child)
            child = following
        self._status_row_box.append(self.status_recorder.row(status, summary, detail))

    def _show_error(self, message: str) -> None:
        """Show an error message."""
        error = Gtk.Label(label=message)
        error.add_css_class("error-label")
        self.append(error)
        # A panel that hit a real fault says so in the sidebar dot too; an
        # unreadable home directory is not "nothing found".
        self._set_status(common.STATUS_ATTENTION, "The artifact list could not be read",
                         message[:300])


# ---------------------------------------------------------------------------
# Build
# ---------------------------------------------------------------------------

def build(app: Any) -> Gtk.Widget:
    """Build the artifact store page for `app`."""
    page, set_content = common.surface(TITLE, SUBTITLE)
    view = _ArtifactStoreView(app)
    set_content(view)
    page.view = view
    #: The sidebar's health dot reads this; the panel's row is drawn from the
    #: same recorder. Without it every surface walker hit
    #: `AttributeError: 'NavigationPage' object has no attribute 'status'`.
    page.status = view.status_recorder.status
    return page
