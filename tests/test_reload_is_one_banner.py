"""Every "read it again" is one banner, and every banner button is labelled.

**The shape of this change, and why it is not only tidiness.** Two panels had a
right-aligned `Reload` button above their content with no explanation. The button
said what it did only in a tooltip, and the panel gave you a reading without ever
saying *when* it was taken - which matters because the sidebar builds a panel once
and keeps it, so a model downloaded by the setup wizard two minutes ago is still
reported as absent until someone presses Reload. A person who does not know to
press it is reading a stale answer and has no way to tell.

So both are `common.banner` now: the reason and the control on one line.

Two claims are asserted here, and both are the ones that could silently not hold:

- **The buttons act.** A `Gtk.Button` with nothing connected read as complete in
  this repo before - the orb is the standing example - so the press is made and
  the panel is watched changing.
- **Every banner button is labelled.** `common.banner` grew a tooltip and an
  accessible label that default to the banner's own sentence, because the
  alternative was a text button announcing only "Reload", which is what these two
  panels had.

The organ panel's header refresh is deliberately *not* one of these. It is a
conventional header control with an icon, and turning it into a banner would move
a refresh out of the place every other app keeps one; the plan this came from
asked for that and it is the one item here declined, with the reason recorded in
`organs.py`'s own class.
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

from shani_chronoa import tts, voices  # noqa: E402
from shani_chronoa.gui.surfaces import common  # noqa: E402
from shani_chronoa.gui.surfaces import model as model_surface  # noqa: E402
from shani_chronoa.gui.surfaces import voice as voice_surface  # noqa: E402


class _StubConfig:
    def __init__(self, **values):
        self._values = values

    def get(self, key, default=""):
        return str(self._values.get(key, default))

    def get_bool(self, key, default=False):
        return bool(self._values.get(key, default))

    def get_double(self, key, default=0.0):
        return float(self._values.get(key, default))

    privacy_mode = True
    wake_word_enabled = False
    whisper_model = ""
    stt_backend = "whisper"
    language = "en"


class _App:
    def __init__(self):
        self.config = _StubConfig()
        self.window = None
        self.tts = None
        self.stt = None
        self.hardware = None
        self.activated: list = []

    def activate_action(self, name, arg):
        self.activated.append(name)


@pytest.fixture
def floor(monkeypatch):
    """The TTS chain's floor only: espeak-ng, nothing above it."""
    monkeypatch.setattr(tts.shutil, "which",
                        lambda name: "/usr/bin/espeak-ng" if name == "espeak-ng" else None)
    monkeypatch.setattr(tts.PiperTTS, "_rhvoice_has_voice", staticmethod(lambda: False))
    monkeypatch.setattr(voices, "piper_binary", lambda: "")
    return _App()


def _walk(node, out=None):
    out = [] if out is None else out
    if isinstance(node, Gtk.Widget):
        out.append(node)
    child = node.get_first_child()
    while child is not None:
        _walk(child, out)
        child = child.get_next_sibling()
    return out


def _labels(page):
    """Every visible label on `page`, joined - so "does it say when" is checkable."""
    return " ".join(
        w.get_label() for w in _walk(page)
        if isinstance(w, Gtk.Label) and w.get_label())


# ---------------------------------------------------------------------------
# the shared helper
# ---------------------------------------------------------------------------


def test_a_banner_button_always_has_a_tooltip_and_a_label():
    """No caller can forget, because forgetting is not expressible.

    `button_tooltip` is optional and the default is the banner's own sentence,
    which is the best available explanation of what the button is for. An empty
    tooltip here would put "Reload" back to announcing itself as just "Reload".
    """
    fired = []
    widget = common.banner("The reading is from an hour ago.", "Reload",
                           lambda: fired.append(1))
    buttons = [w for w in _walk(widget) if isinstance(w, Gtk.Button)]
    assert len(buttons) == 1, f"expected one button, found {len(buttons)}"
    button = buttons[0]
    assert button.get_tooltip_text(), "the button has no tooltip at all"
    assert "an hour ago" in button.get_tooltip_text(), (
        f"the default tooltip does not carry the reason: "
        f"{button.get_tooltip_text()!r}")
    assert button.get_label() == "Reload"

    button.emit("clicked")
    assert fired == [1], "the button did not reach the callback"


def test_an_explicit_tooltip_wins_and_the_button_still_acts():
    """The override exists for a caller with something better to say."""
    fired = []
    widget = common.banner("Read once.", "Reload", lambda: fired.append(1),
                           button_tooltip="Read the engines again from disk")
    button = [w for w in _walk(widget) if isinstance(w, Gtk.Button)][0]
    assert button.get_tooltip_text() == "Read the engines again from disk"
    button.emit("clicked")
    assert fired == [1]


def test_a_banner_with_no_button_offers_nothing_to_press():
    """The no-button path is unchanged - most banners have nothing to press.

    Asserted on *a button carrying the caller's label*, not on "no button
    exists". `Adw.Banner` on libadwaita 1.9.1 contains an internal `Gtk.Button`
    at depth 3 - its own dismiss control - so demanding an empty tree would be
    asserting something about libadwaita's internals rather than about this
    helper. What matters is that no control of *ours* is on screen.
    """
    widget = common.banner("Nothing here was read.")
    mine = [w for w in _walk(widget)
            if isinstance(w, Gtk.Button) and w.get_label()]
    assert mine == [], f"a banner with no action offered {mine}"


# ---------------------------------------------------------------------------
# the two panels
# ---------------------------------------------------------------------------


def test_the_voice_panel_says_when_it_read(floor):
    """The reason is on screen, not only in a tooltip.

    This is the whole point of the change: a stale reading with no stated age is
    indistinguishable from a fresh one.
    """
    page = voice_surface.build(floor)
    text = _labels(page)
    assert "Read once" in text, text
    assert "polled" in text, (
        "the banner does not say that nothing is polled, so a person has no way "
        "to know the reading goes stale on its own")


def test_the_model_panel_says_when_it_read(floor):
    """The same claim, in the panel where it is worst.

    A model is a large download; "not installed" for a model installed two
    minutes ago is a wrong answer that a person would act on.
    """
    page = model_surface.build(floor)
    text = _labels(page)
    assert "Read once" in text, text
    assert "Reload" in text, text


@pytest.mark.parametrize("surface", [voice_surface, model_surface],
                         ids=["voice", "model"])
def test_the_reload_button_is_labelled_for_a_screen_reader(surface, floor):
    """A text button announcing only "Reload" is the failure being fixed."""
    button = surface.build(floor).reload_button()
    assert isinstance(button, Gtk.Button)
    assert button.get_tooltip_text(), "the reload button has no tooltip"
    assert len(button.get_tooltip_text()) > len("Reload"), (
        "the tooltip is just the label, which tells a screen-reader user "
        "nothing about what pressing it does")


def test_pressing_reload_re_reads_rather_than_only_repainting(floor, monkeypatch):
    """The button acts, and "acts" means the readings are asked again.

    Not "the widget tree is still valid" - a panel that rebuilt its rows from
    cached values would pass that. The engine chain is counted across a press,
    so a refresh that does not re-ask is caught.
    """
    asked = []
    real = tts.PiperTTS.engine

    def counted(self_):
        asked.append(1)
        return real(self_)

    monkeypatch.setattr(tts.PiperTTS, "engine", counted)
    page = voice_surface.build(floor)
    before = len(asked)
    assert before, "the panel never asked the chain in the first place"

    page.reload_button().emit("clicked")
    assert len(asked) > before, (
        "Reload rebuilt the rows without asking the engine chain again - the "
        "readings would be identical, which is the opposite of what the button "
        "says it does")


def test_reload_does_not_stack_a_second_banner(floor):
    """Reload replaces the reading; it must not accumulate notices.

    The banner is built once and never rebuilt, so this asserts the thing that
    would actually go wrong if someone moved its construction into `refresh()`.
    """
    page = voice_surface.build(floor)

    def notices(widget):
        return [w for w in _walk(widget)
                if "toolbar-view" in w.get_css_classes()
                or type(w).__name__ == "Banner"]

    assert len(notices(page)) >= 1, "the reload banner is not on screen"
    before = len(notices(page))
    for _ in range(3):
        page.reload_button().emit("clicked")
    assert len(notices(page)) == before, (
        f"three reloads took the notices from {before} to "
        f"{len(notices(page))}")


def test_the_organ_panel_keeps_its_header_refresh():
    """The one item of the consolidation this declines, and why.

    `organs.py`'s refresh is a header-bar icon button, which is where every other
    application keeps one; the two panels above could not do that because
    `common.surface()` builds their header and offers no slot for a button. Moving
    a conventional header control into a banner inside the content would be a
    regression dressed as consistency, so it stays - and this pins that, because a
    future pass through this file would otherwise "finish the job".
    """
    from shani_chronoa.gui.organs import OrganPanel

    panel = OrganPanel()
    buttons = [w for w in _walk(panel)
               if isinstance(w, Gtk.Button)
               and w.get_icon_name() == "view-refresh-symbolic"]
    assert len(buttons) == 1, (
        "the organ panel's refresh is not the header icon button it should be")
    assert buttons[0].get_tooltip_text() == "Refresh"