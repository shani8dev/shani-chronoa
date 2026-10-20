"""The Triggers panel names its switch, and offers to turn it on.

**10c** found three defects on this page, and a photograph of the real app
showed all three at once:

> From /tmp/opencode/trig2/live-window-state/da/shani-chronoa/triggers/rules.json,
> /tmp/opencode/trig2/live-window-state/da/shani-chronoa/triggers/event_rules.json
>
> Rules need the tool-activity switch before any of them can act.  [Manage triggers]
>
> 'trigger-control-enabled' is off: no armed rule can act, including one armed
> before it was turned off. Nothing on this surface fires anything.

Two absolute paths across the top of the page, **the raw key** in the
sentence, and **two off-messages saying the same thing** - one of them
offering a route to Settings rather than the switch itself.

So: the gate is a `consent_banner` like every other gate in the app (it
names the switch and offers **Turn on**), the dim line adds the one fact
the banner cannot - that the switch is not the only gate - and the paths
are shortened to `~/...` with the whole path on hover.
"""

from __future__ import annotations

import pathlib
import sys

import pytest

gi = pytest.importorskip("gi")
gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gtk  # noqa: E402

Adw.init()

sys.modules.setdefault("Gtk", Gtk)

import builtins  # noqa: E402
import typing  # noqa: E402

_REGISTRY_MISSING_TUPLE = not hasattr(builtins, "Tuple")
if _REGISTRY_MISSING_TUPLE:
    builtins.Tuple = typing.Tuple
try:
    from shani_chronoa.gui.surfaces import triggers  # noqa: E402
finally:
    if _REGISTRY_MISSING_TUPLE:
        del builtins.Tuple

from shani_chronoa.triggers import EventRuleStore, RuleStore  # noqa: E402

TRIGGER_CONTROL_KEY = "trigger-control-enabled"


class _StubConfig:
    def __init__(self, on):
        self._on = on

    def get_bool(self, key, default=False):
        return self._on


class _StubApp:
    def __init__(self, trigger_rule_store=None, event_rule_store=None, config=None):
        self.trigger_rule_store = trigger_rule_store
        self.event_rule_store = event_rule_store
        self.config = config


def _texts(widget):
    """Every label's rendered text, in tree order."""
    out = []
    child = widget.get_first_child()
    while child is not None:
        if isinstance(child, Gtk.Label):
            out.append(child.get_text())
        out.extend(_texts(child))
        child = child.get_next_sibling()
    return out


def _buttons(widget):
    out = []
    child = widget.get_first_child()
    while child is not None:
        if isinstance(child, Gtk.Button):
            out.append(child.get_label())
        out.extend(_buttons(child))
        child = child.get_next_sibling()
    return out


def _panel(tmp_path, on=False, home=None):
    """The panel, with its rule files where the test says they are."""
    base = home if home is not None else tmp_path
    app = _StubApp(
        RuleStore(base / "rules.json"),
        EventRuleStore(base / "event_rules.json"),
        config=_StubConfig(on),
    )
    return triggers.build(app)


class TestTheGateNamesItsSwitch:
    def test_the_banner_names_the_switch_not_its_key(self, tmp_path):
        """**A sentence that names a setting by a word no screen shows**
        sends the person looking for a switch that is called something else."""
        said = " ".join(_texts(_panel(tmp_path)))
        assert TRIGGER_CONTROL_KEY not in said, (
            f"the panel still names the raw key: {said!r}")

    def test_the_banner_names_the_switch_as_settings_spells_it(self, tmp_path):
        from shani_chronoa import capabilities
        name = capabilities.gate_label(TRIGGER_CONTROL_KEY)
        assert name, "the switch has no name in the table the rows are built from"
        said = " ".join(_texts(_panel(tmp_path)))
        assert name in said, (
            f"the panel does not name the switch {name!r}: {said!r}")

    def test_the_note_names_the_switch_when_it_is_on(self, tmp_path):
        """**The other branch of the same sentence.** The note says the
        switch's name when the switch is on, and the key must not appear
        there either.

        The first version of this file checked only the *off* branch - and a
        mutation that put the key back passed it, because the off branch
        does not name the switch at all. **A test aimed at a branch the
        fixture never reaches is a test that cannot fail.**
        """
        said = " ".join(_texts(_panel(tmp_path, on=True)))
        assert TRIGGER_CONTROL_KEY not in said, (
            f"the panel names the raw key when the switch is on: {said!r}")
        from shani_chronoa import capabilities
        name = capabilities.gate_label(TRIGGER_CONTROL_KEY)
        assert name in said, (
            f"the panel does not name the switch when it is on: {said!r}")

    def test_the_off_banner_offers_to_turn_it_on(self, tmp_path):
        """**A gate that says what is wrong and sends you away is the
        dead-end class** - the other nine panels offer the switch itself."""
        assert "Turn on" in _buttons(_panel(tmp_path)), _buttons(_panel(tmp_path))


class TestTheTwoOffMessages:
    def test_the_page_does_not_say_off_twice(self, tmp_path):
        """**Two off-messages, in two different words**, was what the page
        showed: the banner and the dim line below it.

        The dim line now adds the one fact the banner cannot - that the
        switch is not the only gate - rather than repeating it.
        """
        said = " ".join(_texts(_panel(tmp_path))).lower()
        assert said.count("is off") <= 1, (
            f"the page says the switch is off more than once: {said!r}")

    def test_the_dim_line_still_says_the_switch_is_not_the_only_gate(self, tmp_path):
        said = " ".join(_texts(_panel(tmp_path)))
        assert "gates permitted" in said, said


class TestThePaths:
    def test_the_paths_are_shortened(self, tmp_path, monkeypatch):
        """**`~/...`, with the whole path on hover** - the files live under
        the person's own data directory, so the long form is the same path
        on every machine and says nothing they need."""
        home = tmp_path / "home"
        home.mkdir(exist_ok=True)
        monkeypatch.setenv("HOME", str(home))
        said = " ".join(_texts(_panel(tmp_path, home=home)))
        assert str(home) not in said, (
            f"the panel still shows the absolute path: {said!r}")
        assert "~/" in said, (
            f"the panel does not shorten the path at all: {said!r}")

    def test_the_whole_path_is_still_there_on_hover(self, tmp_path, monkeypatch):
        """**Shortened, not dropped.** The path is what a person needs when
        they want to open the file."""
        home = tmp_path / "home"
        home.mkdir(exist_ok=True)
        monkeypatch.setenv("HOME", str(home))
        surface = _panel(tmp_path, home=home)
        tips = []
        stack = [surface]
        while stack:
            node = stack.pop()
            tip = node.get_tooltip_text()
            if tip:
                tips.append(tip)
            child = node.get_first_child()
            while child is not None:
                stack.append(child)
                child = child.get_next_sibling()
        joined = " ".join(tips)
        assert str(home) in joined, (
            f"the whole path is nowhere in the tooltips: {joined!r}")
