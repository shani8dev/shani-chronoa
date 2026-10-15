"""Panels show `~/...`, with the full path on hover (MAIN-UI-TODO 10h)."""
import os
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "usr" / "lib" / "shani-chronoa"))

from shani_chronoa.gui.surfaces import common  # noqa: E402

def _home():
    return os.path.expanduser("~")  # conftest moves HOME per test


def test_home_is_written_as_a_tilde():
    HOME = _home()
    assert common.home_paths(HOME + "/.config/a.json") == "~/.config/a.json"
    assert common.home_paths(f"saved in {HOME}, then") == "saved in ~, then"
    assert common.home_paths("/tmp/x") == "/tmp/x"


def test_someone_elses_home_is_not_shortened():
    HOME = _home()
    assert common.home_paths(HOME + "2/x") == HOME + "2/x"


def test_paths_markup_shows_the_tilde_fixed_width():
    HOME = _home()
    assert "<tt>~/.config/a.json</tt>" in common.paths_markup(f"rules in {HOME}/.config/a.json")


def test_rows_shorten_and_keep_the_full_path_on_hover():
    HOME = _home()
    import gi
    gi.require_version("Gtk", "4.0")
    from gi.repository import Gtk
    if not Gtk.init_check():
        import pytest
        pytest.skip("no display")
    row = common.row("Rules", f"{HOME}/.config/shani-chronoa/triggers.json")
    subtitle = row.get_subtitle() if hasattr(row, "get_subtitle") else ""
    if subtitle:
        assert subtitle.startswith("~/"), subtitle
    assert HOME in (row.get_tooltip_text() or ""), "the full path is gone"
