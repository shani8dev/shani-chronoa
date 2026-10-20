"""No jargon on the main window.

**The work order's list**, and the words a person has to look up before they
can read the control. A chip is one word with no room for an explanation, so
a word that needs looking up is a word that does not work there.

The mode strip's presence chip used to say **"Drowsy"** - one word,
unexplained, beside "Here" and "Asleep" - while its own tooltip had said
"Loaded, but idle" all along. The tooltip was right and the chip was not.

**Two instruments, because the words live in two places.** The presence
chip is *built, not appended* - it moved to the palette, where the model
picker lives - so a check that reads the strip's own labels never sees it.
The first version of this file did exactly that and stayed green with
"Drowsy" back on the chip: a check that cannot find the thing it names is
not a check.
"""

from __future__ import annotations

import sys

import pytest

gi = pytest.importorskip("gi")
gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gio, Gtk  # noqa: E402

Adw.init()

sys.path.insert(0, "usr/lib/shani-chronoa")
from shani_chronoa.gui.window import ChronoaWindow, _ModeStrip  # noqa: E402

#: The words the work order names, and the ones this repository has found in
#: the same place since.
JARGON = (
    "organ",
    "posture",
    "egress",
    "presence",
    "drowsy",
    "barge-in",
)

#: The tables the strip and the palette draw their chip words from. The
#: words *are* the claim, so the claim is checked where it is written.
#: (owner, table name). The strip carries most of them; the model picker's
#: presence words live on the window, because the picker is a window's child.
WORD_TABLES = (
    (_ModeStrip, "_PRESENCE"),
    (_ModeStrip, "_EGRESS"),
    (_ModeStrip, "_PLAN"),
    (_ModeStrip, "_STYLES"),
    (_ModeStrip, "_AUTO"),
    (_ModeStrip, "_HANDS"),
    (ChronoaWindow, "_PRESENCE_WORDS"),
)


class _Config:
    privacy_mode = False
    answer_mode = "local-cloud"
    wake_word_enabled = False
    barge_in_vad_enabled = False
    reply_style = "ordinary"
    model = "small"

    def get_bool(self, key, default=False):
        return False


class _App:
    def __init__(self, presence=None):
        self._presence = presence

    @property
    def config(self):
        return _Config()


@pytest.fixture(scope="module")
def gapp():
    app = Gtk.Application(application_id="test.chronoa.jargon",
                          flags=Gio.ApplicationFlags.NON_UNIQUE)
    yield app
    del app


def _words_of(table) -> list:
    """The chip words in one of the strip's tables."""
    return [row[1] for row in table if len(row) > 1 and isinstance(row[1], str)]


def test_no_chip_table_says_a_word_a_person_has_to_look_up():
    """**The words are the claim**, so the claim is checked where it is
    written - and every table is checked, because one of them carrying
    jargon while the others do not is exactly what a single-table check
    misses.
    """
    found = {}
    for owner, name in WORD_TABLES:
        table = getattr(owner, name, None)
        assert table is not None, f"{name} is not on {owner.__name__}"
        words = _words_of(table)
        assert words, f"{name} has no chip words"
        bad = [w for w in words if any(j in w.lower() for j in JARGON)]
        if bad:
            found[name] = bad
    assert not found, (
        f"the mode strip says {found} - a word a person has to look up")


def test_the_drawn_strip_says_no_jargon(gapp, monkeypatch):
    """**And the strip as drawn**, so a word that arrives by another route
    is caught too."""
    from shani_chronoa import local_llm
    monkeypatch.setattr(local_llm, "is_up", lambda: False)
    strip = _ModeStrip(_App(), _Config())
    try:
        strip.refresh()
        said = " ".join(_labels(strip)).lower()
        bad = [j for j in JARGON if j in said]
        assert not bad, (
            f"the strip says {bad} - a word a person has to look up: {said!r}")
    finally:
        strip.unparent()


def test_the_check_is_looking_at_the_chips_at_all(gapp, monkeypatch):
    """**A check that cannot find anything is not a check.**

    The strip's own chip words are on it, so their presence proves the
    labels being searched are the chips' own.
    """
    from shani_chronoa import local_llm
    monkeypatch.setattr(local_llm, "is_up", lambda: False)
    strip = _ModeStrip(_App(), _Config())
    try:
        strip.refresh()
        said = " ".join(_labels(strip)).lower()
        assert "ask first" in said, (
            f"the strip's own chip words are not in its labels: {said!r}")
    finally:
        strip.unparent()


def _labels(widget):
    """Every label the strip draws, in order."""
    out = []
    stack = [widget]
    while stack:
        node = stack.pop()
        if isinstance(node, Gtk.Label) and node.get_text().strip():
            out.append(node.get_text())
        child = node.get_first_child()
        while child is not None:
            stack.append(child)
            child = child.get_next_sibling()
    return out
