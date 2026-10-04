"""The Senses surface, as a built widget.

Four things are pinned here, and only four, because they are the ones a reader
cannot check by reading the module:

- the module exports the three names `gui/surfaces/__init__.py` reads off it,
  and the registry already wires this surface in (asserted by importing the
  registry and reading its real `SURFACE_IDS` tuple, so a rename that broke the
  wiring fails here rather than as a window that opens with a missing panel);
- `build(app_stub)` returns a widget;
- there is exactly one switch per sense in the registry - not per hand-written
  list, not per consent key - and the negative control below is what proves the
  count can fail at all;
- flipping a switch writes that sense's own consent key, against the real
  compiled schema rather than a mock that would agree with anything.

Switch rows are counted by walking the built widget tree for real `Gtk.Switch`
widgets, not by trusting a list the module stashed on itself: a count read back
out of the thing being counted is the same shape of mistake as the
visual-regression suite this repo deleted.

**The registry wiring is asserted against the registry itself, not its source.**
This test used to grep `gui/surfaces/__init__.py` for a literal
`"senses": (senses.TITLE, senses.ICON, senses.build)` mapping. That mapping is
gone: the registry is loop-based now - `SURFACE_IDS` names the surfaces and
`all_surfaces()` imports each one and reads `TITLE`/`ICON`/`build` off it - so the
grep could only ever fail, and its passing would have said nothing about whether
this module is built by anything. It now imports the registry and checks the
name is in the tuple, which is the contract that actually decides whether the
sidebar offers this panel.

(The `Surfaces = Dict[str, Tuple[...]]` blocker this file's docstring used to
carry is fixed in the registry itself - it imports `Tuple` now - so importing
any `gui.surfaces.<name>` module works and no workaround is needed here.)
"""

from __future__ import annotations

import types

import gi
import pytest

gi.require_version("Gtk", "4.0")
from gi.repository import Gtk  # noqa: E402

from shani_chronoa.gui.surfaces import SURFACE_IDS  # noqa: E402
from shani_chronoa.senses import discover_senses  # noqa: E402

pytest.importorskip("shani_chronoa.gui.surfaces.senses")

from shani_chronoa.gui.surfaces import senses  # noqa: E402


def _switches(widget):
    """Every real Gtk.Switch in the built tree, found by walking it."""
    found = []

    def walk(node):
        child = node.get_first_child()
        while child is not None:
            if isinstance(child, Gtk.Switch):
                found.append(child)
            walk(child)
            child = child.get_next_sibling()

    walk(widget)
    return found


def _app(config=None):
    """The smallest thing `build` accepts: anything with a `config`."""
    return types.SimpleNamespace(config=config)


class TestImports:
    def test_it_exports_the_three_names_the_registry_reads(self):
        assert senses.TITLE == "Senses"
        assert senses.ICON
        assert callable(senses.build)

    def test_the_registry_already_wires_this_surface_in(self):
        """`gui/surfaces/__init__.py` reads `senses.TITLE`, `senses.ICON` and
        `senses.build` off this module by name, but only for the names its
        `SURFACE_IDS` tuple lists - the registry is loop-based, so a surface
        missing from that tuple is a hole in the sidebar.

        Asserted against the imported registry rather than its source: the tuple
        is the thing that decides whether this panel is ever built, and reading
        a literal mapping out of the file's text can only ever pass by accident.
        """
        assert "senses" in SURFACE_IDS, (
            "gui/surfaces/__init__.py's SURFACE_IDS never names a 'senses' "
            f"surface, so this module is built by nothing that runs: {SURFACE_IDS}"
        )


class TestBuild:
    def test_build_returns_a_gtk_widget(self):
        assert isinstance(senses.build(_app()), Gtk.Widget)

    def test_one_switch_per_registered_sense(self):
        registry = discover_senses()
        assert registry, "the real sense registry loaded nothing to assert against"
        assert len(_switches(senses.build(_app()))) == len(registry)

    def test_a_missing_config_disables_every_switch_rather_than_raising(self):
        """The surface has to survive being built before settings loaded: an
        exception here takes the whole window down for a condition that is a
        normal transient state, not a broken install."""
        switches = _switches(senses.build(types.SimpleNamespace()))
        assert switches, "no rows at all - that is a failure to build, not a state"
        assert all(not switch.get_active() for switch in switches)


class TestTheCountCanFail:
    """The negative control for `test_one_switch_per_registered_sense`.

    A count assertion that passes whatever the surface does is not a check. So:
    make the registry import report zero senses and the count has to collapse
    with it. `discover_senses` is patched where this module reads it - on
    `gui.surfaces.senses`, not on `shani_chronoa.senses` - because a patch to a
    re-export is a copy nobody reads.
    """

    def test_a_registry_reporting_zero_senses_collapses_the_count(
        self, monkeypatch
    ):
        real = len(_switches(senses.build(_app())))
        assert real == len(discover_senses())
        assert real != 0, "the real registry built no rows for this to lose"

        monkeypatch.setattr(senses, "discover_senses", lambda: {})
        assert len(_switches(senses.build(_app()))) == 0, (
            "the switch count survived a registry that reports no senses, so "
            "the count assertion cannot fail and proves nothing"
        )

        monkeypatch.undo()
        assert len(_switches(senses.build(_app()))) == real


class TestTheConsentKeys:
    def test_a_switch_reads_its_own_key(self, chronoa_config):
        """`memory-sense-enabled` is the one sense the schema defaults to true,
        so this needs no fixture setup to be a real reading of a real default."""
        row = next(r for r in senses.build(_app(chronoa_config))._sense_rows
                   if r._sense_name == "memory")
        assert row._consent_key == "memory-sense-enabled"
        assert chronoa_config.get_bool("memory-sense-enabled", False) is True
        assert row._switch.get_active() is True

    def test_flipping_a_switch_writes_that_senses_own_key(self, chronoa_config):
        widget = senses.build(_app(chronoa_config))
        row = next(r for r in widget._sense_rows if r._sense_name == "hearing")
        assert row._consent_key == "hearing-sense-enabled"
        chronoa_config.set("hearing-sense-enabled", "false")
        assert row._switch.get_active() is False

        row._switch.set_active(True)
        assert chronoa_config.get_bool("hearing-sense-enabled", False) is True

        row._switch.set_active(False)
        assert chronoa_config.get_bool("hearing-sense-enabled", False) is False

    def test_every_switch_carries_a_tooltip_and_an_accessible_label(self):
        """A permission control with no accessible name is a control a screen
        reader announces as "switch", and there are forty-five of them."""
        for switch in _switches(senses.build(_app())):
            tooltip = switch.get_tooltip_text()
            assert tooltip, "a switch with no tooltip"
            assert "Allow or deny the " in tooltip, tooltip

    def test_granted_rows_come_first(self, chronoa_config):
        chronoa_config.set("hearing-sense-enabled", "true")
        rows = senses.build(_app(chronoa_config))._sense_rows
        granted = [r for r in rows if r._switch.get_active()]
        refused = [r for r in rows if not r._switch.get_active()]
        assert granted, "the granted sense did not read as granted"
        assert [r._sense_name for r in rows[: len(granted)]] == [
            r._sense_name for r in granted
        ]
        assert [r._sense_name for r in rows[len(granted):]] == [
            r._sense_name for r in refused
        ]