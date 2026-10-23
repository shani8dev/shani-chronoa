"""The review page restates its counts after every "Remove".

Found by an AI agent driving the real wizard through shani-testbed's MCP server
(2026-10-23): after removing two of three items, the list and the Total row
showed one download while the button still read "Download 3 thing(s)" and the
consent switch still offered "1309 MB for the 3 thing(s) listed above". The
button label and the consent subtitle were computed once, before the first
render, and `render()` - which every Remove press calls - rebuilt only the rows.
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


def _review():
    win = setup_wizard.build_window(Gtk.Application(
        application_id="test.wizard.reviewcounts", flags=Gio.ApplicationFlags.NON_UNIQUE))
    return next(p for p in _walk(win) if isinstance(p, Adw.NavigationPage) and p.get_tag() == "review")


def _state(page):
    nodes = _walk(page)
    go = next(b for b in nodes if isinstance(b, Gtk.Button)
              and (b.get_label() or "").startswith(("Download ", "Continue")))
    consent = next(r for r in nodes if isinstance(r, Adw.SwitchRow))
    removes = [b for b in nodes if isinstance(b, Gtk.Button) and b.get_label() == "Remove"]
    return go, consent, removes


def test_remove_restates_the_button_and_the_consent_line():
    page = _review()
    go, consent, removes = _state(page)
    if not removes:
        pytest.skip("this machine's defaults put nothing on the review page")
    before = len(removes)
    assert go.get_label() == f"Download {before} thing(s)"
    removes[0].emit("clicked")
    go, consent, removes = _state(page)
    after = len(removes)
    assert after == before - 1
    want = f"Download {after} thing(s)" if after else "Continue"
    assert go.get_label() == want, f"button still says {go.get_label()!r} with {after} item(s) listed"
    assert f"for the {after} thing(s)" in consent.get_subtitle(), consent.get_subtitle()
