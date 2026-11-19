"""A newcomer's empty state must fit the window Chronoa opens at.

Measured at the 380px minimum: prompt (18) + hints (37) + chips (194) already
fill the ~130px transcript viewport, and the home board added 267px below
them. The suggestion chips are the only "what can I ask" answer in the empty
state; the board's Today/recent/quick-action tiles are reachable from the
header and palette at this width. Wide windows keep the board.
"""
import pathlib
import sys

import pytest

gi = pytest.importorskip("gi")
gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gtk, GLib  # noqa: E402

Adw.init()
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "usr" / "lib" / "shani-chronoa"))
from shani_chronoa.gui.window import ChronoaWindow  # noqa: E402


class _App(Gtk.Application):
    def __init__(self):
        super().__init__(application_id="test.chronoa.emptyfit")

    def activate_action(self, *a):
        return None

    def current_llm_kind(self):
        return "none"


class _Cfg:
    privacy_mode = False
    wake_word_enabled = False
    barge_in_vad_enabled = False
    reply_style = "ordinary"

    def sense_allowed(self, key):
        return False

    def get_bool(self, key, default=False):
        return False


def _rows(width, height):
    win = ChronoaWindow(_App(), config=_Cfg())
    win.set_default_size(width, height)
    win._apply_narrow_layout()
    ctx = GLib.MainContext.default()
    for _ in range(20):
        while ctx.pending():
            ctx.iteration(False)
    rows = []
    child = win._transcript._rows.get_first_child()
    while child is not None:
        rows.append(child)
        child = child.get_next_sibling()
    narrow = win._narrow_applied
    win.destroy()
    return rows, narrow


def test_a_narrow_empty_state_has_no_home_board():
    rows, narrow = _rows(380, 487)
    assert narrow is True
    assert not any("home-board" in r.get_css_classes() for r in rows), (
        "the 267px board pushed the suggestion chips below the fold")


def test_a_wide_empty_state_keeps_the_home_board():
    rows, narrow = _rows(760, 700)
    assert narrow is False
    assert any("home-board" in r.get_css_classes() for r in rows)


def test_both_keep_the_placeholder_hints_and_chips():
    for width, height in ((380, 487), (760, 700)):
        rows, _ = _rows(width, height)
        kinds = [type(r).__name__ for r in rows]
        assert kinds.count("Label") >= 2, kinds
        assert "SuggestionBar" in kinds, kinds
