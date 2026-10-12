"""The Desktop panel named the page that grants its gates, and offered no way there.

`tests/test_dead_ends_have_buttons.py` exists because five panels said where a
switch was - "Grant it in Settings, under Privacy", "turn the key on in Settings" -
without providing a way to reach any of them. It fixed `devices` and `calendar`,
and named the shape. `desktop.py` had the same shape and was not in that file's
coverage.

Measured on the built panel before this change: 125 widgets, **zero** buttons, and
two rows whose own module docstring says

    No gate is written from this panel: `global-shortcut-enabled` and
    `document-search-enabled` are granted in Settings, and the Settings and
    Privacy panels are where that happens.

So a person told exactly which two keys were off, and which page granted them,
had nothing to click. Both keys are written in `settings_window/privacy.py` and
nowhere else, so `settings:privacy` is not a guess - it is the only place the
answer can be changed.

**The button is offered only when the gate is off**, following `devices.py`'s
rule that a button opening Settings to change nothing is a worse dead end than
the sentence it replaced. The two other states are tested here as negatives,
because a guard that cannot fail is not a guard:

- gate on  -> no button; there is nothing to fix
- unreadable -> no button; "could not ask" is not "off", and offering one would
  claim a certainty the row has just said it lacks
"""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "usr/lib/shani-chronoa"))

gi = pytest.importorskip("gi")
gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gtk  # noqa: E402

Adw.init()

from shani_chronoa import pages  # noqa: E402
from shani_chronoa.gui.surfaces import desktop  # noqa: E402

BUTTON = "Open Privacy settings"


class _Config:
    """A settings object that answers the two keys, or refuses to."""

    def __init__(self, values=None, explode=False):
        self._values = values or {}
        self._explode = explode

    def get(self, key, default=""):
        return str(self._values.get(key, default))

    def get_bool(self, key, default=False):
        if self._explode:
            raise RuntimeError("the settings backend is not answering")
        return bool(self._values.get(key, default))

    def get_double(self, key, default=0.0):
        return float(default)


class _App:
    def __init__(self, config):
        self.config = config
        self.window = None
        self.tts = None
        self.stt = None
        self.hardware = None
        self.tool_tracker = None
        self.percept_store = None


def _walk(node, out=None):
    out = [] if out is None else out
    if isinstance(node, Gtk.Widget):
        out.append(node)
    child = node.get_first_child()
    while child is not None:
        _walk(child, out)
        child = child.get_next_sibling()
    return out


def _buttons(page):
    return [w for w in _walk(page) if isinstance(w, Gtk.Button) and w.get_label()]


@pytest.fixture
def shown(monkeypatch):
    """Record every `pages.show` instead of opening a window."""
    reached = []
    monkeypatch.setattr(pages, "show",
                        lambda target, application=None, config=None:
                        reached.append(target) or True)
    return reached


def _both_off():
    return {desktop.SHORTCUT_KEY: False, desktop.DOCUMENT_KEY: False}


def test_a_shut_gate_offers_the_page_that_opens_it(shown):
    """Both gates off -> one button per shut gate, and the press reaches Privacy.

    Two shut gates means two buttons, and that is the point: they are two
    independent settings and a person should be able to turn either on. The
    assertion is the *route*, not the label - a button with the right words that
    opens nothing is the orb.
    """
    page = desktop.build(_App(_Config(_both_off())))

    found = [b for b in _buttons(page) if b.get_label() == BUTTON]
    assert len(found) == 2, (
        f"expected a route on each of the two shut gates, found {len(found)}. "
        "A panel that names the page and offers no way there is the dead end "
        "this file exists to prevent.")

    found[0].emit("clicked")
    assert shown == ["settings:privacy"], (
        f"the press reached {shown!r}, not the page that grants the keys. Both "
        "keys are written in settings_window/privacy.py and nowhere else.")


def test_a_gate_that_is_already_on_gets_no_button(shown):
    """**The negative control.** With nothing wrong there is nothing to fix, and
    a button here would open Privacy settings and change nothing."""
    page = desktop.build(_App(_Config({desktop.SHORTCUT_KEY: True,
                                       desktop.DOCUMENT_KEY: True})))

    assert [b for b in _buttons(page) if b.get_label() == BUTTON] == [], (
        "a gate that is already on offered a button to open Privacy settings, "
        "where it would change nothing - a worse dead end than the sentence it "
        "replaced")
    assert shown == []


def test_an_unreadable_gate_gets_no_button(shown):
    """**The second negative control, and the one that is easy to get wrong.**

    "Could not ask" is a state of its own in this module and is never a
    negative - the docstring says a row cannot invent a fourth word. Offering
    the route on an unreadable gate would claim the gate is off, which is the
    one claim the row has just said it cannot make.
    """
    page = desktop.build(_App(_Config(explode=True)))

    assert [b for b in _buttons(page) if b.get_label() == BUTTON] == [], (
        "a gate that could not be read offered a button as though it were off")
    assert shown == []


def test_one_shut_gate_offers_one_route(shown):
    """One shut, one open: the button belongs to the shut one alone.

    Guards against a panel that shows the route whenever *either* key is off,
    which is how a person ends up on a page with two switches when one was the
    problem.
    """
    page = desktop.build(_App(_Config({desktop.SHORTCUT_KEY: True,
                                       desktop.DOCUMENT_KEY: False})))

    assert len([b for b in _buttons(page) if b.get_label() == BUTTON]) == 1