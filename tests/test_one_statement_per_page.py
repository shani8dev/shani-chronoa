"""One statement per page: a banner that names a switch is the page's one
statement about it.

**A page that says "off" twice in two different words is harder to read
than a page that says it once.** The reader has to work out which of the
two sentences is the current one, and the two are worded differently
because they were written by different code.

Measured on this tree, with every consent key off - the state a fresh
install is in - **two panels said the same thing twice**: the Phone
panel's status row named the switch its banner had already named, and
the Phone link panel's empty state pointed back at a banner that was
already on screen saying it.

The property is checked across **every** panel, because the failure is a
shape rather than a mistake in one file: a panel that grows a second
statement about a switch it already banners will not be caught by a
per-panel test.
"""

from __future__ import annotations

import os
import re
import sys
from collections import deque

import pytest

_REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(_REPO, "usr", "lib", "shani-chronoa"))

gi = pytest.importorskip("gi")
gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
Gtk = pytest.importorskip("gi.repository.Gtk", reason="GTK 4 is not installed")
Adw = pytest.importorskip("gi.repository.Adw", reason="libadwaita is not installed")

Adw.init()

from shani_chronoa.capabilities import GATE_NAMES  # noqa: E402
from shani_chronoa.gui import surfaces  # noqa: E402

#: Every switch name Settings shows. The table the Settings rows are built
#: from, so the check asks about the words a person actually reads.
SWITCH_NAMES = tuple(sorted(set(GATE_NAMES.values())))


class _Config:
    """A config where every switch is off - the state a fresh install is in.

    **Not a real `ChronoaConfig`**: one of those reads the running schema,
    and a machine whose compiled schema predates a key answers its Python
    default instead, which is a different population on every machine. The
    gate is about the *shape* of a page, and the shape is the same whether
    a switch is off or unreadable.
    """

    def get_bool(self, key, default=False):
        return False

    def get(self, key, default=None):
        return default

    def get_string(self, key, default=""):
        return default

    def set(self, key, value):
        return None

    def sense_allowed(self, key):
        return False


class _App:
    application_id = "test.chronoa.onestatement"

    def __init__(self):
        self.config = _Config()
        self.window = None

    def activate(self):
        pass


def _is_banner(node):
    """Is this the notice a panel puts above its content?

    **Two shapes, and the button one is not an `Adw.Banner`.** A banner that
    carries a button of ours is a plain `Gtk.Box` with the `surface-banner`
    class, because `Adw.Banner` has no signal to connect a button to - so
    looking for `Adw.Banner` alone misses every consent banner, which is
    every banner that offers a "Turn on".
    """
    if isinstance(node, Adw.Banner):
        return True
    try:
        return bool(node.has_css_class("surface-banner"))
    except Exception:  # noqa: BLE001 - not every widget has a style context
        return False


def _walk(widget):
    """Every widget on screen, and every label's text.

    **Effective visibility, not `get_visible()`**: that is per-widget, so a
    label inside a hidden banner still answers True - and counting it is how
    a "clean" result turns out to have measured a page nobody sees.

    **A banner's own text is not "elsewhere"**: the check is about a second
    statement about the same switch, and the banner's own sentence is the
    first one.
    """
    if not widget.get_visible():
        return
    queue, labels, banners = deque([(widget, False)]), [], []
    while queue:
        node, inside = queue.popleft()
        if _is_banner(node) and not inside:
            inside = True
            banners.append(node)
        if isinstance(node, Gtk.Label) and not inside:
            text = (node.get_label() or "").strip()
            if text:
                labels.append(text)
        if hasattr(node, "get_first_child"):
            child = node.get_first_child()
            while child is not None:
                queue.append((child, inside))
                child = child.get_next_sibling()
    return labels, banners


def _flat(text):
    """A text with its line breaks turned into spaces.

    **A label's text is wrapped**, so a phrase a person reads as one line
    is two lines in the widget - and a substring check over the raw text
    misses it. That is how the My files panel's empty state slipped past
    the first version of this file: the switch's name was split across a
    line break, so "the name is in this label" was false.
    """
    return " ".join(str(text or "").split())


def _banner_text(node):
    """What a banner says."""
    if isinstance(node, Adw.Banner):
        return node.get_title() or ""
    texts = []
    stack = deque([node])
    while stack:
        item = stack.popleft()
        if isinstance(item, Gtk.Label):
            text = (item.get_label() or "").strip()
            if text:
                texts.append(text)
        if hasattr(item, "get_first_child"):
            child = item.get_first_child()
            while child is not None:
                stack.append(child)
                child = child.get_next_sibling()
    return " ".join(texts)


#: A sentence, loosely: a run of text ending in a stop. **Longer than a
#: dozen characters**, because a fragment that short is a label rather than
#: a statement, and the same label on two rows of a list is not a page
#: saying the same thing twice.
_SENTENCE = re.compile(r"[^.;:!?\n]+[.;:!?]?")


def _sentences(text):
    """The sentences of a text, **lowercased and without their stops**.

    **The case has to go.** The Export panel said its reason three times -
    in a banner, in a status row's subtitle and in a group's description -
    and the first version of this check missed it because the banner began
    the sentence with a capital and the other two did not. Two spellings of
    one sentence are one sentence.
    """
    out = []
    for part in _SENTENCE.findall(text.replace("\n", " ")):
        part = part.strip().rstrip(".;:!?").strip().lower()
        if len(part) > 12:
            out.append(part)
    return out


def _panel_texts(sid):
    """The on-screen labels and banners of one built panel."""
    registry = surfaces.all_surfaces()
    assert sid in registry, f"the panel {sid!r} is not in the registry"
    _title, _icon, build = registry[sid]
    page = build(_App())
    return _walk(page)


def _all_text(sid):
    """Every visible label on the page, banner text included."""
    registry = surfaces.all_surfaces()
    _title, _icon, build = registry[sid]
    page = build(_App())
    if not page.get_visible():
        return []
    found, queue = [], deque([page])
    while queue:
        node = queue.popleft()
        if isinstance(node, Gtk.Label):
            text = (node.get_label() or "").strip()
            if text:
                found.append(text)
        if hasattr(node, "get_first_child"):
            child = node.get_first_child()
            while child is not None:
                queue.append(child)
                child = child.get_next_sibling()
    return found


class TestOneStatementPerPage:
    @pytest.mark.parametrize("sid", list(surfaces.SURFACE_IDS))
    def test_no_panel_repeats_a_switch_its_banner_already_named(self, sid):
        """**The banner is the page's one statement about that switch** -
        it names it, says what it means, and offers to turn it on. Nothing
        else on the page may say it again."""
        labels, banners = _panel_texts(sid)
        for banner in banners:
            title = _flat(_banner_text(banner))
            for name in SWITCH_NAMES:
                if name not in title:
                    continue
                elsewhere = [text for text in labels if name in _flat(text)]
                assert not elsewhere, (
                    f"{sid}: the banner says {name!r} and so does "
                    f"{elsewhere[0][:120]!r} - one statement per page")

    @pytest.mark.parametrize("sid", list(surfaces.SURFACE_IDS))
    def test_no_panel_repeats_a_sentence_its_banner_already_said(self, sid):
        """**A banner's sentence said again in a status row is the same
        defect in different words** - and a switch's name is not the only
        thing a banner says."""
        labels, banners = _panel_texts(sid)
        for banner in banners:
            for sentence in _sentences(_flat(_banner_text(banner))):
                elsewhere = [text for text in labels if sentence in _flat(text).lower()]
                assert not elsewhere, (
                    f"{sid}: the banner says {sentence[:80]!r} and so does "
                    f"{elsewhere[0][:120]!r} - one statement per page")

    def test_the_phone_panel_does_not_say_it_twice(self):
        """**The Phone panel's status row named the switch its banner had
        already named**, and the row's own detail repeated the banner's
        sentence word for word."""
        labels = _all_text("phone")
        name = GATE_NAMES.get("phone-control-enabled")
        assert name
        assert sum(1 for text in labels if name in text) == 1, labels

    def test_the_phone_link_panel_does_not_say_it_twice(self):
        """**The Phone link panel's empty state pointed back at the banner**
        - "Turn on ... above first" - while the banner was on screen
        saying the same thing with a button to press."""
        from shani_chronoa.gui.surfaces import phone_link
        labels = _all_text("phone_link")
        name = GATE_NAMES.get(phone_link.CONSENT_KEY)
        assert name
        assert sum(1 for text in labels if name in text) == 1, labels
