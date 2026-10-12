"""The rail's "Made" section - and the file-existence claim it makes."""

import sys
from collections import deque
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "usr/lib/shani-chronoa"))

import gi  # noqa: E402

gi.require_version("Gtk", "4.0")
from gi.repository import Gtk  # noqa: E402

from shani_chronoa.gui import rail as rail_module  # noqa: E402


def _rail(made):
    return rail_module.NowRail(get_artifacts=lambda: list(made))


def _labels(rail):
    """Every label, in the order they appear in the tree.

    Breadth-first with `popleft`, because the first version used a list as a
    stack and so returned the tree *backwards* - and then "newest first" came
    out looking like "newest last". A helper that lies about ordering is worse
    than no helper.
    """
    out, queue = [], deque([rail])
    while queue:
        node = queue.popleft()
        if isinstance(node, Gtk.Label):
            out.append(node.get_label())
        if hasattr(node, "get_first_child"):
            child = node.get_first_child()
            while child is not None:
                queue.append(child)
                child = child.get_next_sibling()
    return out


def test_a_file_that_was_made_is_listed_in_the_rail(tmp_path):
    made = tmp_path / "cat.png"
    made.write_bytes(b"\x89PNG\r\n\x1a\n")
    rail = _rail([made])
    labels = _labels(rail)
    assert "Made" in labels, f"no Made section: {labels}"
    assert "cat.png" in labels, f"the file made is not in the rail: {labels}"


def test_the_newest_is_first(tmp_path):
    """The file just made is the one being looked for."""
    older, newer = tmp_path / "first.png", tmp_path / "second.png"
    for path in (older, newer):
        path.write_bytes(b"\x89PNG\r\n\x1a\n")
    labels = _labels(_rail([older, newer]))
    assert labels.index("second.png") < labels.index("first.png"), (
        f"the rail is chronological, so the newest file is last: {labels}")


def test_a_file_deleted_since_it_was_made_is_dropped(tmp_path):
    """A row offering to open something that is not there is worse than no row."""
    here, gone = tmp_path / "here.png", tmp_path / "gone.png"
    here.write_bytes(b"\x89PNG\r\n\x1a\n")
    gone.write_bytes(b"\x89PNG\r\n\x1a\n")
    gone.unlink()

    labels = _labels(_rail([here, gone]))
    assert "here.png" in labels
    assert "gone.png" not in labels, f"a deleted file is still listed: {labels}"
    assert any("removed" in text for text in labels), (
        f"it was dropped silently rather than accounted for: {labels}")


def test_an_empty_session_says_so_rather_than_saying_nothing(tmp_path):
    labels = _labels(_rail([]))
    assert any("Nothing made" in text for text in labels), (
        f"an empty Made section reads as a broken rail: {labels}")


def test_a_directory_is_shown_as_a_folder_and_gets_the_folder_icon(tmp_path):
    folder = tmp_path / "page-images"
    folder.mkdir()
    assert rail_module.NowRail._icon_for(folder) == "folder-symbolic"
    labels = _labels(_rail([folder]))
    assert "page-images" in labels
    assert any("folder" in text for text in labels), (
        f"a folder reported as though it were a file: {labels}")


def test_every_icon_the_made_section_can_choose_exists_on_this_machine():
    """Every `-symbolic` in this package is checked by
    `test_ui_layout_contract.py`; this is the one that would otherwise be a
    blank square with nothing in it."""
    theme = Gtk.IconTheme.new()
    theme.add_search_path("/usr/share/icons")
    chosen = {rail_module.NowRail._icon_for(Path(f"x{suffix}"))
              for suffix in (".png", ".jpg", ".md", ".zip", ".sh", "")}
    chosen.add(rail_module.NowRail._icon_for(Path(".")))
    for name in sorted(chosen):
        assert theme.has_icon(name), f"{name} does not exist in this theme"
