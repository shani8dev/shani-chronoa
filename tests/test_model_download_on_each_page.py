"""Every page that needs a model offers its Download when it is missing.

Measured 2026-10-15: of nine model kinds, only speech had a download on the
page that uses it; the Models panel's "Set up" buttons were all disabled and
said they opened "on its page" while opening Welcome; Piper voices always read
"Not downloaded". Setup stays the one place that downloads - these buttons
land on the right page of it.
"""
import pathlib
import sys

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "usr" / "lib" / "shani-chronoa"))

from shani_chronoa import model_status  # noqa: E402

SETUP_PAGES = {"brain", "ears", "voice", "eyes", "imagine", "memory", "photos", "sounds", "speakers"}


def test_every_kind_lands_on_a_real_setup_page():
    assert {k.setup_page for k in model_status.KINDS.values()} <= SETUP_PAGES
    assert set(model_status.KINDS) >= {"llm", "stt", "voice", "vision", "imagine", "memory",
                                       "photos", "sounds", "speakers"}


def test_a_check_that_raises_reads_as_missing(monkeypatch):
    kind = model_status.KINDS["vision"]
    monkeypatch.setitem(model_status.KINDS, "vision", model_status.Kind(
        kind.key, kind.label, kind.enables, kind.setup_page, lambda: 1 / 0))
    assert model_status.installed("vision") is False
    assert model_status.installed("nope") is None


class _App:
    def __init__(self):
        self.activated = []

    def activate_action(self, name, value):
        self.activated.append((name, value.unpack() if value is not None else None))


def _gtk():
    gi = pytest.importorskip("gi")
    gi.require_version("Gtk", "4.0")
    from gi.repository import Gtk
    if not Gtk.init_check():
        pytest.skip("no display")
    return Gtk


def _buttons(widget, acc=None):
    Gtk = _gtk()
    acc = [] if acc is None else acc
    if isinstance(widget, Gtk.Button):
        acc.append(widget)
    child = widget.get_first_child()
    while child is not None:
        _buttons(child, acc)
        child = child.get_next_sibling()
    return acc


def _kind_set(monkeypatch, present: bool):
    for key, kind in list(model_status.KINDS.items()):
        monkeypatch.setitem(model_status.KINDS, key, model_status.Kind(
            kind.key, kind.label, kind.enables, kind.setup_page, lambda p=present: p))


def test_a_missing_model_banner_opens_setup_on_its_page(monkeypatch):
    _gtk()
    from shani_chronoa.gui.surfaces import common
    _kind_set(monkeypatch, False)
    app = _App()
    banner = common.model_banner(app, "memory", "search by meaning")
    assert banner is not None
    (download,) = [b for b in _buttons(banner) if b.get_label() == "Download"]
    download.emit("clicked")
    assert app.activated == [("setup-page", "memory")]


def test_nothing_missing_adds_nothing(monkeypatch):
    Gtk = _gtk()
    from shani_chronoa.gui.surfaces import common
    _kind_set(monkeypatch, True)
    content = Gtk.Label(label="x")
    assert common.model_banner(_App(), "stt") is None
    assert common.with_model_banners(_App(), content, ["stt", ("memory", "y")]) is content


def test_the_models_panel_set_up_lands_on_the_cards_page(monkeypatch):
    _gtk()
    from shani_chronoa.gui.surfaces import models
    app = _App()
    models._activate(app, {"kind": "vision", "installed": False}, None, lambda: None)
    assert app.activated == [("setup-page", "eyes")]
