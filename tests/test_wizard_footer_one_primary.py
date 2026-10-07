"""Every setup-wizard page has one primary action, and its buttons say where they go.

**Six of the seven optional-extra pages showed four stacked buttons, two of them
primary** - "Next: Photos and videos / Skip the rest / Back to the list /
Finish" - because `extras_page()` added a Next/Skip pair and each page then
added `goto_back_to_list()` underneath. Found by rendering the wizard at 1280px
and looking, not by any test: every existing check asked whether a button was
*reachable*, none asked how many there were. The Languages page had two buttons
both labelled "Close" that went to two different pages.

The routing under the duplicates was the worse half. "Skip the rest" and
Languages' "Next: Done" went to Done, past the Review page that is the only
place anything is downloaded, so an extra picked on the way was never fetched;
"Finish" went to the Review rather than to the finish. So this file asserts the
shape (one primary, no repeated label) and presses the one button that matters
on each extra to see where it actually lands.
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

_EXTRAS = ("eyes", "imagine", "memory", "photos", "sounds", "speakers", "languages")


def _walk(node, out=None):
    out = [] if out is None else out
    out.append(node)
    child = node.get_first_child()
    while child is not None:
        _walk(child, out)
        child = child.get_next_sibling()
    return out


def _build():
    return setup_wizard.build_window(Gtk.Application(
        application_id="test.wizard.oneprimary",
        flags=Gio.ApplicationFlags.NON_UNIQUE))


def _pages(window):
    return {p.get_tag(): p for p in _walk(window) if isinstance(p, Adw.NavigationPage)}


def _footer_buttons(page):
    """The labelled buttons pinned under the page's scroller - its action row."""
    scroller = next(n for n in _walk(page) if isinstance(n, Gtk.ScrolledWindow))
    inside = {id(n) for n in _walk(scroller)}
    return [b for b in _walk(page)
            if isinstance(b, Gtk.Button) and b.get_label() and id(b) not in inside]


@pytest.fixture(scope="module")
def wizard():
    return _build()


def test_no_page_has_two_primary_actions(wizard):
    offenders = {}
    for tag, page in sorted(_pages(wizard).items()):
        primary = [b.get_label() for b in _footer_buttons(page)
                   if b.has_css_class("suggested-action")]
        if len(primary) > 1:
            offenders[tag] = primary
    assert not offenders, f"pages with more than one primary button: {offenders}"


def test_no_page_repeats_a_button_label(wizard):
    offenders = {}
    for tag, page in sorted(_pages(wizard).items()):
        labels = [b.get_label() for b in _footer_buttons(page)]
        if len(labels) != len(set(labels)):
            offenders[tag] = labels
    assert not offenders, f"pages whose action row repeats a label: {offenders}"


def test_the_check_can_see_a_second_primary(wizard):
    """Control: a footer given a second primary button must be flagged."""
    page = _pages(wizard)["memory"]
    footer = _footer_buttons(page)[0].get_parent()
    extra = Gtk.Button(label="Injected", css_classes=["suggested-action"])
    footer.append(extra)
    try:
        primary = [b for b in _footer_buttons(page) if b.has_css_class("suggested-action")]
        assert len(primary) == 2, "the walker did not see the injected button"
    finally:
        footer.remove(extra)


@pytest.mark.parametrize("tag", _EXTRAS)
def test_each_extras_primary_action_returns_to_the_review(tag):
    """Pressed on a fresh window each, so one press's history cannot steer the next."""
    window = _build()
    view = next(n for n in _walk(window) if isinstance(n, Adw.NavigationView))
    primary = [b for b in _footer_buttons(_pages(window)[tag])
               if b.has_css_class("suggested-action")]
    assert len(primary) == 1, [b.get_label() for b in primary]
    primary[0].emit("clicked")
    assert view.get_visible_page().get_tag() == "review", (
        f"{tag}'s primary action {primary[0].get_label()!r} went to "
        f"{view.get_visible_page().get_tag()!r}, not to the review - anything "
        "picked there would never be downloaded")
