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
import os
import shutil
import sys
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gdk, Gio, GLib, Gtk, Pango  # noqa: E402

from shani_chronoa import conversation_store  # noqa: E402
from shani_chronoa.gui.surfaces import common  # noqa: E402

logger = logging.getLogger(__name__)

TITLE = "Artifacts"
ICON = "folder-download-symbolic"
SECTION = "Desktop and system"

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

    def _on_category_toggled(self, button: Gtk.ToggleButton) -> None:
        pass  # Rendering is handled by the view


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
        
        # Checksum (if available)
        if artifact.checksum:
            # Ellipsised, and `max_width_chars` lowered to 24 to match what is
            # actually shown ("SHA256: " plus 16 hex digits). With only
            # `set_max_width_chars(30)` and no wrap or ellipsise this label
            # demanded 194px on its own and put the whole row at 528px in a
            # 480px window - a sideways scrollbar under the page, which is
            # exactly what the layout contract exists to catch.
            checksum_label = Gtk.Label(label=f"SHA256: {artifact.checksum[:16]}...", xalign=0.0)
            checksum_label.add_css_class("artifact-checksum")
            checksum_label.set_ellipsize(Pango.EllipsizeMode.END)
            checksum_label.set_max_width_chars(24)
            meta.append(checksum_label)
        
        info_box.append(meta)
        self.append(info_box)

        # Path button
        path_btn = Gtk.Button(label="Show")
        path_btn.set_tooltip_text("Show in file manager")
        path_btn.connect("clicked", self._on_path_clicked, artifact.path)
        path_btn.add_css_class("path-button")
        self.append(path_btn)

    def _format_size(self, size: int) -> str:
        """Format size in human-readable units."""
        for unit in ["B", "KB", "MB", "GB", "TB"]:
            if size < 1024.0 or unit == "TB":
                return f"{size:.1f} {unit}"
            size /= 1024.0

    def _on_path_clicked(self, button: Gtk.Button, path: Path) -> None:
        """Open the artifact's directory in the file manager."""
        try:
            if sys.platform == "darwin":
                subprocess.run(["open", "--", str(path.parent)], check=False)
            elif sys.platform == "win32":
                subprocess.run(["explorer", "/select:", str(path)], check=False)
            else:  # Linux, BSD, etc.
                subprocess.run(["xdg-open", str(path.parent)], check=False)
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

        # Filter section
        filter_section = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        filter_section.add_css_class("section")
        
        filter_label = Gtk.Label(label="Filter by category")
        filter_label.add_css_class("dim-label")
        filter_section.append(filter_label)
        
        self._category_filter = _CategoryFilter()
        filter_section.append(self._category_filter)
        
        self.append(filter_section)

        # Search section
        search_section = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=12)
        search_section.add_css_class("section")
        
        search_entry = Gtk.Entry()
        search_entry.set_placeholder_text("Search artifacts...")
        search_entry.set_hexpand(True)
        search_entry.connect("changed", self._on_search_text_changed)
        
        clear_btn = Gtk.Button(label="Clear")
        clear_btn.set_tooltip_text("Clear search")
        clear_btn.connect("clicked", self._on_clear_search)
        
        search_section.append(search_entry)
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
        self.append(self._status_row_box)
        self._status_label = Gtk.Label(label="Loading...")
        self._status_label.add_css_class("dim-label")
        self.append(self._status_label)

        # Load artifacts
        self._load_artifacts()

    # -- actions ---------------------------------------------------------

    def _load_artifacts(self) -> None:
        """Load artifacts from the home directory."""
        try:
            home = Path.home()
            self._artifacts = _discover_artifacts(home)
            self._apply_filters()
        except Exception as exc:  # noqa: BLE001
            logger.warning("Could not load artifacts", exc_info=True)
            self._show_error(f"Failed to load artifacts: {exc}")

    def _on_category_toggled(self, button: Gtk.ToggleButton) -> None:
        # Update selected category
        for cat, btn in self._category_filter._buttons.items():
            if btn.get_active():
                self._selected_category = cat
                break
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
        """Find the search entry in the widget hierarchy."""
        for child in self.get_children():
            if isinstance(child, Gtk.Box):
                # Look for the search section by finding a Gtk.Entry with placeholder
                for subchild in child.get_children():
                    if isinstance(subchild, Gtk.Entry) and "Search" in subchild.get_placeholder_text():
                        return subchild
        return None

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
            empty = Gtk.Label(label="No artifacts found.")
            empty.add_css_class("dim-label")
            self._list_box.append(empty)
            self._status_label.set_text(f"{len(self._artifacts)} total artifacts")
            self._set_status(
                common.STATUS_ATTENTION if not self._artifacts else common.STATUS_OK,
                ("Nothing in the home directory looks like an artifact" if not self._artifacts
                 else "No artifact matches the current filter"),
                f"{len(self._artifacts)} artifact(s) found before filtering")
            return

        # Render artifacts
        for artifact in self._filtered:
            self._list_box.append(_ArtifactRow(artifact))

        self._status_label.set_text(f"{len(self._filtered)} of {len(self._artifacts)} artifacts")
        self._set_status(
            common.STATUS_OK,
            f"{len(self._filtered)} of {len(self._artifacts)} artifact(s) shown",
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
