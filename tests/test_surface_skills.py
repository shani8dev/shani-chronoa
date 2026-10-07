"""The skills surface as a built widget.

The properties pinned here are the ones a reader cannot get wrong without
opening the window: the list is the *live* registry rather than a hand-kept
list, a row's gate is a working switch only where the permission is a real
settings key, a malformed registry entry costs a row instead of the window, and
the filter narrows - including a negative control that breaks the filter on
purpose, so a green narrowing test is known to be capable of failing.

The registry wiring is asserted against the registry, not its source. These two
tests used to `ast`-parse `surfaces/__init__.py` for a literal
`"skills": (skills.TITLE, skills.ICON, skills.build)` mapping, which the
loop-based registry does not have - the tuple in `SURFACE_IDS` names the
surfaces and `all_surfaces()` reads `TITLE`/`ICON`/`build` off each module it
imports - so the parse could only ever report an empty mapping. They now import
the registry and assert the contract that actually decides whether this panel is
ever built: the name is in `SURFACE_IDS`, and `all_surfaces()` hands the sidebar
this module's own three names.
"""

from __future__ import annotations

import importlib
import importlib.util
import os
import pathlib
import sys

import gi
import pytest

gi.require_version("Gtk", "4.0")
gi.require_version("Gdk", "4.0")
from gi.repository import Gdk, Gtk  # noqa: E402

Gtk.init()

import shani_chronoa.gui as _gui  # noqa: E402
from shani_chronoa.gui.surfaces import SURFACE_IDS, all_surfaces  # noqa: E402

SURFACES_DIR = pathlib.Path(_gui.__file__).resolve().parent / "surfaces"
SURFACE_SOURCE = SURFACES_DIR / "skills.py"


def _load_surface():
    """Import `surfaces/skills.py` the ordinary way if the registry allows it,
    and from its path if the registry cannot be imported at all.

    The registry imports fine today (it used to raise `NameError` on a `Tuple`
    its own `typing` import did not bring in). The fallback stays so that a
    regression there is reported as this file's own assertion failure rather
    than as a collection error for the whole module - and it still asserts *why*
    it fell back, so a different failure is not silently absorbed.
    """
    try:
        return importlib.import_module("shani_chronoa.gui.surfaces.skills")
    except NameError as exc:  # a registry that cannot name one of its own types
        assert "Tuple" in str(exc), f"surfaces/__init__.py now fails differently: {exc}"
        name = "chronoa_surface_skills"
        spec = importlib.util.spec_from_file_location(name, SURFACE_SOURCE)
        assert spec is not None and spec.loader is not None
        module = importlib.util.module_from_spec(spec)
        sys.modules[name] = module
        spec.loader.exec_module(module)
        return module


skills = _load_surface()


class _StubApp:
    """The least an app can be for this surface: nothing at all."""

    def __init__(self, config=None):
        if config is not None:
            self.config = config


def _visible(surface):
    return [row for row in surface.rows() if row.widget.get_visible()]


def _fake_registry(monkeypatch, entries):
    """Point the surface at a registry of `entries` instead of the real one."""
    monkeypatch.setattr(skills, "discover_skills", lambda: (entries, {}))


# -- imports ------------------------------------------------------------
def test_module_exports_title_icon_and_build():
    assert isinstance(skills.TITLE, str) and skills.TITLE
    assert isinstance(skills.ICON, str) and skills.ICON
    assert callable(skills.build)


def test_the_icon_name_is_one_the_theme_actually_has():
    """An icon name nothing ships renders as a blank space in the sidebar, and
    a widget that constructed says nothing about that.

    The live theme is the real answer, so it is asked first; on a machine with
    no display the installed icon directories are asked instead. Neither
    answering is a failure, not a skip - the alternative is a green test that
    has not looked at anything.
    """
    display = Gdk.Display.get_default()
    if display is not None:
        assert Gtk.IconTheme.get_for_display(display).has_icon(skills.ICON), (
            f"{skills.ICON} is not in the icon theme this display uses")
        return
    roots = [pathlib.Path(p) for p in
             (os.environ.get("XDG_DATA_DIRS") or "/usr/local/share:/usr/share").split(":")]
    found = [path for root in roots
             for path in root.glob(f"icons/*/*/actions/{skills.ICON}.*")]
    assert found, f"no display and no installed icon theme carries {skills.ICON}"


# -- registry wiring ----------------------------------------------------
def test_the_registry_registers_this_surface():
    """The sidebar offers exactly the names in `SURFACE_IDS`, and this is one of
    them. A surface missing from that tuple is a module nothing builds - built,
    unit-tested and never wired in, which is the dead-code class this repo has
    paid for twice."""
    assert "skills" in SURFACE_IDS, (
        f"the sidebar never offers this surface: {sorted(SURFACE_IDS)}")
    assert "skills" in all_surfaces(), (
        "SURFACE_IDS names this surface but all_surfaces() did not hand it over, "
        "so importing it failed at runtime")


def test_the_registry_hands_the_sidebar_this_module_title_icon_and_build():
    """`all_surfaces()` reads `TITLE`, `ICON` and `build` off the module it
    imports for each name, so what it hands the sidebar has to be this module's
    own three names - not a copy, and not a stand-in."""
    assert all_surfaces()["skills"] == (skills.TITLE, skills.ICON, skills.build)
    # The attributes the registry reads are the ones this module defines, and
    # the module a registry entry names is the file the entry sits in.
    assert skills.TITLE == "Skills"
    assert callable(skills.build)
    assert skills.__name__.endswith("skills")


# -- build --------------------------------------------------------------
def test_build_returns_a_widget_for_a_stub_app():
    surface = skills.build(_StubApp())
    assert isinstance(surface, Gtk.Widget)


def test_an_app_with_no_config_still_gets_the_whole_list(chronoa_config):
    """No settings object is a real state (a broken read, a test stub); the
    list must not depend on it, and nothing may offer a control that cannot
    work."""
    with_config = skills.build(_StubApp(chronoa_config))
    without = skills.build(_StubApp())

    assert len(without.rows()) == len(with_config.rows()) > 60
    assert without.switch_count() == 0, "a switch was offered with nothing to write"
    assert with_config.switch_count() > 0


# -- the whitelist ------------------------------------------------------
def test_more_than_sixty_skill_rows_from_the_live_registry():
    surface = skills.build(_StubApp())
    rows = surface.rows()

    assert len(rows) > 60, f"only {len(rows)} rows - the whitelist is not being read"
    assert surface.registry_error is None
    # Titled for a person (`capabilities.tool_title`), with the id kept in the
    # subtitle; a skill with no entry there is titled with its name.
    from shani_chronoa import capabilities
    assert all(row.name and row.title.get_text() == capabilities.tool_title(row.name)
               for row in rows)
    assert any(row.title.get_text() != row.name for row in rows), "no row got a human title"
    assert any(row.description for row in rows)
    assert all(row.switch is None or row.switch.get_tooltip_text() for row in rows)


def test_row_count_matches_the_registry_the_model_is_handed():
    from shani_chronoa.skills import discover_skills

    tools, _handlers = discover_skills()
    assert len(skills.build(_StubApp()).rows()) == len(tools)


def test_a_malformed_registry_entry_is_skipped_not_raised(monkeypatch):
    _fake_registry(monkeypatch, [
        {},                                    # not a dict
        {"function": None},                    # no function member
        {"type": "function", "function": {}},  # no name
        {"type": "tool", "function": {"name": "wrong_type", "description": "d"}},
        {"type": "function", "function": {"name": "good_one",
                                          "description": "A skill that is fine."}},
        "a bare string",
        None,
    ])
    surface = skills.build(_StubApp())
    assert [row.name for row in surface.rows()] == ["good_one"]
    assert surface.registry_error is None


def test_a_registry_that_cannot_be_read_says_so_instead_of_looking_empty(monkeypatch):
    def _boom():
        raise RuntimeError("every built-in module failed to import")

    monkeypatch.setattr(skills, "discover_skills", _boom)
    surface = skills.build(_StubApp())
    assert surface.rows() == []
    assert "RuntimeError" in surface.registry_error


def test_a_broken_user_drop_in_module_costs_a_row_not_the_window(temp_user_skills_dir):
    """The real case, not a stubbed registry: a module in
    `~/.config/shani-chronoa/skills/` whose `SKILLS` is not a list. It is
    skipped with a log line, the well-formed drop-in beside it still shows, and
    the surface builds - which is what "a bad user module can never crash the
    app" means for this window."""
    (temp_user_skills_dir / "broken_drop_in.py").write_text(
        'SKILLS = "not a list"\n', encoding="utf-8")
    (temp_user_skills_dir / "good_drop_in.py").write_text(
        "from shani_chronoa.skills import Skill\n"
        "SCHEMA = {'type': 'function', 'function': {'name': 'user_drop_in',\n"
        "          'description': 'A skill the user dropped in.'}}\n"
        "def _run(arguments):\n"
        "    return 'ran'\n"
        "SKILLS = [Skill(name='user_drop_in', schema=SCHEMA, run=_run)]\n",
        encoding="utf-8")

    from shani_chronoa.skills import discover_skills

    tools, _handlers = discover_skills()
    surface = skills.build(_StubApp())

    names = [row.name for row in surface.rows()]
    assert "user_drop_in" in names, "the well-formed drop-in skill was lost"
    assert len(surface.rows()) == len(tools)
    assert surface.registry_error is None
    assert surface.switch_count() == 0


# -- escaping -----------------------------------------------------------
def test_a_skill_name_is_shown_literally_not_as_markup(monkeypatch):
    _fake_registry(monkeypatch, [{"type": "function", "function": {
        "name": "<b>evil</b>",
        "description": "5 < 6 & 7 > 2",
    }}])
    row = skills.build(_StubApp()).rows()[0]
    # get_text() is the *rendered* text, so this only passes if the name
    # survived escaping rather than being read as bold markup.
    assert row.title.get_text() == "<b>evil</b>"
    assert row.description == "5 < 6 & 7 > 2"


# -- gates --------------------------------------------------------------
def test_only_gated_skills_get_a_switch(chronoa_config):
    surface = skills.build(_StubApp(chronoa_config))
    gated = [row for row in surface.rows() if row.gate_key]
    ungated = [row for row in surface.rows() if not row.gate_key]

    assert gated, "no gated skill found; the gate lookup is not working"
    assert ungated, "no ungated skill found; something is inventing gates"
    assert all(row.switch is not None for row in gated)
    # "always available" is stated as text, and no switch is offered for it.
    assert all(row.switch is None for row in ungated)


def test_a_gate_whose_key_this_build_lacks_is_not_a_working_switch(chronoa_config):
    """A key the running schema does not declare cannot be granted, so the
    switch is insensitive rather than a control that silently does nothing -
    the settings window's own case, in its own words."""

    class _OtherSchema:
        _valid_keys = frozenset({"some-other-key"})

        def get_bool(self, key, default=False):
            return default

        def set(self, key, value):
            raise AssertionError(f"wrote {key}, which this schema does not declare")

    gated = [row for row in skills.build(_StubApp(_OtherSchema())).rows() if row.gate_key]
    assert gated
    assert all(row.switch is not None and not row.switch.get_sensitive()
               for row in gated)
    # And the real schema, which does declare them, offers working ones.
    assert skills.build(_StubApp(chronoa_config)).switch_count() == len(gated)


def test_toggling_a_gate_writes_the_consent_key_and_moves_its_siblings(chronoa_config):
    from shani_chronoa.config import ChronoaConfig

    surface = skills.build(_StubApp(chronoa_config))
    row = next(r for r in surface.rows() if r.gate_key == "input-control-enabled")
    assert row.switch is not None and row.switch.get_sensitive() is True

    row.switch.set_active(True)

    # A fresh config instance sees the write: a switch that only moves itself
    # is decoration.
    assert ChronoaConfig().get_bool("input-control-enabled", False) is True
    siblings = [r for r in surface.rows() if r.gate_key == "input-control-enabled"]
    assert len(siblings) > 1, "expected more than one skill behind this gate"
    assert all(r.switch.get_active() for r in siblings)
    # Building again from the same settings shows the gate as allowed, so the
    # state is persisted rather than only held in the widget.
    assert skills.build(_StubApp(ChronoaConfig())).switch_count() == surface.switch_count()


# -- filtering ----------------------------------------------------------
def test_the_search_entry_filters_by_name_and_by_description():
    surface = skills.build(_StubApp())
    total = len(surface.rows())

    surface.search.set_text("battery")
    by_name = _visible(surface)
    assert by_name, "filtering by a real skill name left nothing"
    assert 0 < len(by_name) < total
    assert any("battery" in row.name.lower() for row in by_name), (
        "the name that was searched for matched no skill name")

    # A phrase lifted out of a real description rather than written here, so the
    # assertion cannot rot into "the filter matched nothing" the day the prose
    # is reworded.
    phrase = " ".join(max((row.description for row in surface.rows()),
                          key=str.split).split()[2:5])
    surface.search.set_text(phrase)
    by_description = _visible(surface)
    assert by_description, f"filtering by {phrase!r}, a real description, left nothing"
    assert len(by_description) < total
    assert all(phrase.lower() in row.haystack.lower() for row in by_description)

    surface.search.set_text("")
    assert surface.visible_row_count() == total


def test_a_filter_that_matches_nothing_says_so():
    surface = skills.build(_StubApp())
    surface.search.set_text("zzz-no-such-skill")
    assert surface.visible_row_count() == 0
    assert surface.no_matches.get_visible() is True
    surface.search.set_text("")
    assert surface.no_matches.get_visible() is False


def test_negative_control_an_always_match_filter_breaks_the_narrowing_check(monkeypatch):
    """The narrowing assertion above has to be able to fail.

    With `_matches` forced to always True, "battery" leaves every row visible,
    so the real assertion (`0 < visible < total`) fails - proven here rather
    than assumed, then the real filter restored and checked again.
    """
    surface = skills.build(_StubApp())
    total = len(surface.rows())

    surface.search.set_text("battery")
    narrowed = surface.visible_row_count()
    assert 0 < narrowed < total

    with pytest.MonkeyPatch.context() as ctx:
        ctx.setattr(skills, "_matches", lambda haystack, query: True)
        surface.search.set_text("battery")
        assert surface.visible_row_count() == total, (
            "an always-match filter still narrowed the list, so the narrowing "
            "assertion cannot fail")
        with pytest.raises(AssertionError):
            assert surface.visible_row_count() == narrowed

    surface.apply_filter(surface.search.get_text())
    assert surface.visible_row_count() == narrowed


# -- accessibility ------------------------------------------------------
def test_every_interactive_control_has_a_tooltip_that_names_what_it_does(chronoa_config):
    """A permission control with no accessible name is announced as "switch",
    and there are dozens of them.

    The accessible LABEL itself is set with `update_property`, which this
    PyGObject build exposes no getter for (the same constraint
    `tests/test_window_ux.py` records), so the tooltip is the readable proxy -
    and each one is built from the same `spoken` string the label is, which is
    what this asserts: the announced name of every switch, recomputed here from
    the row's own skill name and gate, is in what the widget shows.
    """
    from shani_chronoa import capabilities

    surface = skills.build(_StubApp(chronoa_config))

    assert "Filter skills by name or description" in surface.search.get_tooltip_text()

    switches = [row for row in surface.rows() if row.switch is not None]
    assert switches
    for row in switches:
        spoken = f"Allow {row.name}: {capabilities.GATE_NAMES.get(row.gate_key, row.gate_key)}"
        tooltip = row.switch.get_tooltip_text()
        assert tooltip, f"the gate switch for {row.name} has no tooltip"
        assert spoken in tooltip, f"{row.name}: the tooltip does not name the control"