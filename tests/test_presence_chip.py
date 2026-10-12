"""Whether Chronoa is here at all, on the strip rather than only in Settings.

`presence.Presence` has three states and the third is the interesting one:
**SLEEPING is a decision, not an observation.** `detect()` answers "is the model
resident" and so can only ever return ACTIVE or DROWSY - which is why
`reconcile()` exists, and why the Settings row is built from the reconciled value
rather than from `detect()`.

That row was the only place the state was visible, on a screen you open
deliberately. Every other mode is on the strip, and presence is the largest one
the window can answer: the rest govern how Chronoa behaves *for the person*, this
governs whether it is *there*.

**The word is the reconciled state, and the control is the disagreement.** The
strip has now been hit three times by the same failure - a chip reporting a state
the machine is not in - so this asserts the chip against `reconcile()` over every
reachable pair rather than against a table of expected words, which would only
prove the table was copied correctly.
"""

import sys

import pytest

gi = pytest.importorskip("gi")
gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gio, Gtk  # noqa: E402

Adw.init()

sys.path.insert(0, "usr/lib/shani-chronoa")
from shani_chronoa import local_llm, presence  # noqa: E402
from shani_chronoa.gui.window import _ModeStrip  # noqa: E402

WORDS = {"active": "Here", "drowsy": "Drowsy", "sleeping": "Asleep"}


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
    """A strip owner carrying only what `_read_presence` reads."""

    def __init__(self, chosen):
        self.config = _Config()
        self._presence = chosen


@pytest.fixture(scope="module")
def gapp():
    app = Gtk.Application(application_id="test.chronoa.presencestrip",
                          flags=Gio.ApplicationFlags.NON_UNIQUE)
    yield app
    del app


def _word(strip):
    box = strip._presence_chip.get_child()
    return box.get_first_child().get_next_sibling().get_text()


@pytest.mark.parametrize("chosen", [None, presence.Presence.ACTIVE,
                                   presence.Presence.DROWSY,
                                   presence.Presence.SLEEPING])
@pytest.mark.parametrize("is_up", [True, False])
def test_the_chip_shows_the_reconciled_state(gapp, monkeypatch, chosen, is_up):
    """All six reachable pairs, against `reconcile()` rather than a table.

    `local_llm.is_up` is patched because it is the *only* thing the reader asks
    the machine, and a test that cannot make the machine answer is asserting
    nothing about the reconciliation. The first version of this probe fed a fake
    `is_up` into its own expectation while the chip correctly asked the real
    service - so it reported two mismatches that were the probe's bug, not the
    code's.
    """
    monkeypatch.setattr(local_llm, "is_up", lambda: is_up)
    strip = _ModeStrip(_App(chosen), _Config())
    try:
        expected = presence.reconcile(
            chosen if chosen is not None else presence.detect(lambda: is_up),
            lambda: is_up)
        assert _word(strip) == WORDS[expected.value], (
            f"chosen={chosen} is_up={is_up}: the chip says {_word(strip)!r} and "
            f"reconcile says {expected.value!r}")
    finally:
        strip.unparent()


def test_asleep_is_not_something_the_machine_can_take_back(gapp, monkeypatch):
    """The chip keeps "Asleep" even once the model is resident again.

    **`reconcile()` never withdraws SLEEPING**, which is the whole reason it
    exists: `detect()` cannot see the decision, so a machine that is up would
    otherwise report a state the person never chose. This is the specific
    behaviour a stale-reader regression would destroy silently - the chip would
    go back to looking correct.
    """
    monkeypatch.setattr(local_llm, "is_up", lambda: True)
    strip = _ModeStrip(_App(presence.Presence.SLEEPING), _Config())
    try:
        assert _word(strip) == "Asleep", (
            "a machine that happens to be up overrode a sleeping choice - the "
            "chip is reporting what it can observe rather than what was chosen")
    finally:
        strip.unparent()


def test_every_state_is_reachable_from_the_menu(gapp, monkeypatch):
    """Three items, and their targets are the three states.

    The strip is the place a mode is *picked*, so a state the menu omits is a
    state that cannot be chosen from here - which is how the Settings row's
    predecessor offered "Asleep" and could not reach it.
    """
    monkeypatch.setattr(local_llm, "is_up", lambda: True)
    strip = _ModeStrip(_App(None), _Config())
    try:
        menu = strip._presence_chip.get_menu_model()
        assert menu is not None, "the presence dropdown has no menu to open"
        targets = [menu.get_item_attribute_value(i, "target").get_string()
                   for i in range(menu.get_n_items())]
        assert targets == ["active", "drowsy", "sleeping"]
        labels = [menu.get_item_attribute_value(i, "label").get_string()
                  for i in range(menu.get_n_items())]
        assert all(len(t) <= 30 for t in labels), (
            f"a menu item can neither wrap nor ellipsise: {labels}")
    finally:
        strip.unparent()


def test_this_files_control_can_fail(monkeypatch):
    """**The control: the comparison above can tell two states apart.**

    Every test here compares the chip's word against `reconcile()`. If that
    comparison could not distinguish "Asleep" from "Here", all four would pass
    with a chip stuck on any single value. So it is driven over pairs that must
    differ.
    """
    def shown(reconciled_value):
        return WORDS[reconciled_value]

    assert shown("sleeping") != shown("active")
    assert shown("drowsy") != shown("sleeping")
    assert shown("active") == "Here"      # and not silently the same word
    assert len({shown(v) for v in WORDS}) == 3, (
        "the three states do not produce three words, so no comparison against "
        "them can distinguish a chip that never changes")