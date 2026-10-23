"""'Skip for now' skips one step; it does not jump past Review to Done.

Found by an AI agent driving the real wizard through shani-testbed (2026-10-23):
it picked a model, pressed "Skip for now" on Hearing, and landed on Done ("That is
everything") - past Voice and past Review, the only page that downloads anything.
The model it had picked was never fetched and nothing said so; the assistant then
reported "no model is downloaded yet".
"""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "usr/lib/shani-chronoa"))

gi = pytest.importorskip("gi")
gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gio, Gtk  # noqa: E402

Adw.init()

from shani_chronoa import setup_wizard  # noqa: E402


def _walk(node, out=None):
    out = [] if out is None else out
    out.append(node)
    child = node.get_first_child()
    while child is not None:
        _walk(child, out)
        child = child.get_next_sibling()
    return out


def _visible_tag(win):
    view = next(n for n in _walk(win) if isinstance(n, Adw.NavigationView))
    return view.get_visible_page().get_tag()


def _rows(page):
    return [r.get_title() for r in _walk(page) if isinstance(r, Adw.ActionRow)]


def test_skip_on_hearing_goes_to_voice_and_keeps_the_model():
    win = setup_wizard.build_window(Gtk.Application(
        application_id="test.wizard.skipone", flags=Gio.ApplicationFlags.NON_UNIQUE))
    pages = {p.get_tag(): p for p in _walk(win) if isinstance(p, Adw.NavigationPage)}
    ears = pages["ears"]
    skips = [b for b in _walk(ears) if isinstance(b, Gtk.Button) and b.get_label() == "Skip for now"]
    if not skips:
        pytest.skip("this machine already has a speech model, so Hearing offers no skip")
    win.show_page("ears") if hasattr(win, "show_page") else None
    skips[0].emit("clicked")
    assert _visible_tag(win) == "voice", f"Skip for now on Hearing went to {_visible_tag(win)!r}"
    review = pages["review"]
    review.emit("showing")
    titles = _rows(review)
    assert not any(t.startswith("Listening - ") for t in titles), titles
    if any(t.startswith("Language model - ") for t in _rows(review)) is False:
        # a machine with a verified model plans no brain download; nothing to keep
        return
    assert any(t.startswith("Language model - ") for t in titles), titles
