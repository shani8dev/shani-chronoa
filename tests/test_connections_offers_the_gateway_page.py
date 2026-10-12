"""The Connections panel named the Settings page that grants its channels, and offered no way there.

Measured on the rendered panel, for two channels at once:

    Chat channels (Telegram, WhatsApp)
    Chronoa is accepting it
    not accepted - add 'telegram' to Gateways in Settings, Privacy
    not accepted - add 'whatsapp' to Gateways in Settings, Privacy
    [BUTTON] Copy

One button, and it copies. The two sentences that say what is wrong name the page
that fixes it and offer nothing to press - the dead end
`tests/test_dead_ends_have_buttons.py` exists for, now the fifth occurrence
after `desktop.py`, `listening.py`, `goals.py` and `phone.py`.

`gateways` is written by `settings_window/privacy.py`'s `_gateway_rows` and
nowhere else, so `settings:privacy` is not a guess.

**One button for the group, not one per row.** The fix is the same edit to the
same list for every channel, so a button per row would be three identical buttons
leading to one page. That is why the count is asserted as one and not as
"one per unaccepted channel" - the latter would pass the per-row version and fail
the one on screen.

The negatives matter as much: when every channel is already accepted there is
nothing to fix, and the button would open a page and change nothing.
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
from shani_chronoa.gui.surfaces import connections  # noqa: E402

ROUTE = "Open Privacy settings"


class _Config:
    def __init__(self, gateways=""):
        self._gateways = gateways

    def get(self, key, default=""):
        return self._gateways if key == "gateways" else str(default)

    def get_bool(self, key, default=False):
        return bool(default)

    def get_double(self, key, default=0.0):
        return float(default)


class _App:
    def __init__(self, gateways=""):
        self.config = _Config(gateways)
        self.window = None
        self.tts = None
        self.stt = None

    def activate_action(self, name, arg):
        pass


def _walk(node, out=None):
    out = [] if out is None else out
    if isinstance(node, Gtk.Widget):
        out.append(node)
    child = node.get_first_child()
    while child is not None:
        _walk(child, out)
        child = child.get_next_sibling()
    return out


def _routes(page):
    return [w for w in _walk(page)
            if isinstance(w, Gtk.Button) and w.get_label() == ROUTE]


def test_an_unaccepted_channel_offers_the_page_that_grants_it(monkeypatch):
    """The positive case, asserted on the **route** rather than the label."""
    reached = []
    monkeypatch.setattr(pages, "show",
                        lambda target, application=None, config=None:
                        reached.append(target) or True)

    page = connections.build(_App(gateways=""))

    found = _routes(page)
    assert len(found) == 1, (
        f"expected one route for the channel group, found {len(found)}. The "
        "fix is the same edit to the same list for every channel, so a button "
        "per row would be several identical buttons leading to one page - and a "
        "count of 'one per channel' would pass that version.")
    found[0].emit("clicked")
    # A literal, not a constant read from the module: asserting against the
    # module's own value moves both sides at once.
    assert reached == ["settings:privacy"], (
        f"the press reached {reached!r}, not the page that writes 'gateways'")


def test_one_unaccepted_channel_is_enough():
    """**The control.** The button appears for a single unaccepted channel too,
    not only when the whole list is empty."""
    assert len(_routes(connections.build(_App(gateways="telegram")))) == 1


def test_every_channel_accepted_gets_no_button():
    """**The negative.** Nothing to fix, so the press would open Privacy
    settings and change nothing - the dead end `devices.py` calls out."""
    every = ",".join(connections.CHANNELS)
    assert not _routes(connections.build(_App(gateways=every))), (
        "a route was offered when every channel was already accepted")


def test_the_panel_always_names_the_channels_it_will_not_accept():
    """The sentence is the fact; the button is only the way to act on it. Both
    have to be on screen, or the other is noise."""
    page = connections.build(_App(gateways="telegram"))
    said = []
    for widget in _walk(page):
        if isinstance(widget, Adw.ActionRow):
            said.append(widget.get_subtitle() or "")

    assert any("not accepted" in s for s in said), (
        "the panel offers a route but no longer says what is wrong")
    assert any("Chronoa is accepting it" in s for s in said), (
        "the accepted channel lost its sentence when the route was added")


def test_the_route_is_the_page_that_actually_writes_the_key():
    """Why `settings:privacy` is the target, asserted against the source. If the
    row moves, this fails and the route follows."""
    import pathlib

    window = pathlib.Path(__file__).resolve().parents[1] / "usr/lib/shani-chronoa" \
        / "shani_chronoa" / "settings_window"
    writers = [p.name for p in window.glob("*.py")
               if '"gateways"' in p.read_text(encoding="utf-8", errors="replace")]
    assert writers == ["privacy.py"], (
        f"'gateways' is written by {writers!r}, so the route 'settings:privacy' "
        "points at a page that cannot change the answer")