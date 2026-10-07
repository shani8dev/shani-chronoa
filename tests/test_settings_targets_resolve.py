"""A gear beside the rows Settings governs, and it opens the section it names.

The panels are read-only reports; the settings window holds the switches. That
split is right, and it made every "turn this on in Settings" a sentence naming a
window with no way to reach it — so the fix is a gear on the sidebar row that
says the thing is off, putting the route on the same screen as the complaint.

**Only where Settings genuinely governs the panel.** A gear that opens a section
of unrelated switches is the same dead end wearing a button, and a worse one
because it *does* open something. So the negative half is asserted too, from the
same table the sidebar reads.

Every target is resolved against the real `pages` registry rather than against a
list in this file, because a gear wired to an id nothing registers looks
identical to a working one from here: `pages.show` logs and returns False.
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
from shani_chronoa.gui import surfaces  # noqa: E402
from shani_chronoa.gui.sidebar import SidebarPage  # noqa: E402


def _walk(node, out=None):
    out = [] if out is None else out
    if isinstance(node, Gtk.Widget):
        out.append(node)
    child = node.get_first_child()
    while child is not None:
        _walk(child, out)
        child = child.get_next_sibling()
    return out


@pytest.fixture
def sidebar():
    """A real `SidebarPage` built from the real registry."""
    page = SidebarPage(None, None, None)
    yield page
    page.unparent()


@pytest.fixture
def shown(monkeypatch):
    """Record every `pages.show` rather than opening a window."""
    reached: list = []
    monkeypatch.setattr(pages, "show",
                        lambda target, application=None, config=None:
                        reached.append(target) or True)
    return reached


def test_every_settings_target_resolves_to_a_page_that_exists():
    """The table cannot name a section the settings window does not have.

    Resolved through `pages`, with the settings window's declaration imported, so
    a renamed or dropped section fails here rather than producing a gear that goes
    nowhere. A gear wired to a dead id is indistinguishable from a working one at
    the only place it can be observed cheaply - the press - and that press is only
    observable through the registry, so this uses it.
    """
    from shani_chronoa.settings_window import window as settings_window

    # Importing the window needs an application; registering the ids does not.
    settings_window.SettingsWindow.__init__            # touch, do not construct
    declared = set(_declared_settings_pages())
    for name, section in surfaces.SETTINGS_TARGETS.items():
        assert section in declared, (
            f"{name} names settings:{section}, which the settings window does "
            f"not declare. It has: {sorted(declared)}")


def _declared_settings_pages():
    """The ids the settings window registers, read from its own source.

    **The module, and the table, not the class body.** The declaration moved out
    of `SettingsWindow` into `_declare_families()` (still called at the bottom of
    the module, so still at import time) and the call now passes
    `list(_SECTION_FAMILIES)` rather than an inline list literal. Reading the
    class found no `register` call at all and yielded **nothing**, so this
    generator was empty and every assertion built on it passed for the wrong
    reason - the twin of the same defect in
    `tests/test_dead_ends_have_buttons.py`, which is fixed there with the
    measurement. This resolves the module-level table and asserts the registry
    call passes it, so an empty yield cannot look like a pass.
    """
    import ast
    import inspect

    from shani_chronoa.settings_window import window as settings_window

    tree = ast.parse(inspect.getsource(settings_window))
    families = None
    for node in tree.body:
        if isinstance(node, ast.Assign):
            names = [t.id for t in node.targets if isinstance(t, ast.Name)]
            if "_SECTION_FAMILIES" in names:
                families = node.value
    assert families is not None, (
        "the settings module no longer declares _SECTION_FAMILIES, so this "
        "helper would silently yield nothing")
    found = set()
    for element in getattr(families, "elts", []):
        if isinstance(element, ast.Tuple) and element.elts:
            first = element.elts[0]
            if isinstance(first, ast.Constant):
                found.add(str(first.value))
    registered = any(
        isinstance(node, ast.Call) and getattr(node.func, "attr", "") == "register"
        and any("_SECTION_FAMILIES" in ast.dump(arg) for arg in node.args)
        for node in ast.walk(tree))
    assert registered, (
        "nothing registers _SECTION_FAMILIES with the page registry any more, "
        "so every settings target below is dead however full the table is")
    yield from sorted(found)


def test_every_panel_named_in_the_table_is_a_real_panel():
    """A typo in the table is a gear on nothing, or a missing gear."""
    known = set(surfaces.SURFACE_IDS)
    for name in surfaces.SETTINGS_TARGETS:
        assert name in known, (
            f"{name!r} has a settings target but is not a panel; the sidebar "
            f"would never build a row for it. Panels: {sorted(known)}")


def test_the_gears_are_on_the_rows_that_need_them(sidebar):
    """Exactly the mapped panels carry one, and no other does."""
    gears = set(sidebar._gears)
    expected = set(surfaces.SETTINGS_TARGETS) & set(sidebar._surface_rows)
    assert gears == expected, (
        f"gears on {sorted(gears)}, expected {sorted(expected)}. A row with a gear "
        "for a panel Settings does not govern sends someone to unrelated "
        "switches; a mapped panel without one leaves the dead end in place.")


#: The panels Settings does **not** govern, stated here rather than read from
#: `surfaces.SETTINGS_TARGETS`.
#:
#: **This list is deliberately independent of the table the sidebar reads.**
#: The check above compares the built sidebar against `SETTINGS_TARGETS` - the very
#: table that built it - so adding an entry there adds the gear *and* the
#: expectation together and the test stays green. Measured: adding `diagnostics`
#: and `inventory` to the table left all seven tests passing.
#:
#: So the claim being made here is a judgement, and only a stated judgement can
#: assert it:
#:
#: - `diagnostics`, `inventory`, `export`, `workbench`, `conversations`,
#:   `senses`-alike reports that only *read* the machine. Settings holds switches
#:   for permissions, voices, models and the trigger gate; it has nothing to
#:   offer someone looking at why a subsystem is not working.
#: - `machine` - its readings come from the kernel, not from a setting.
#: - `desktop` - the rows are what the desktop answered, and its own settings own
#:   the switches behind them, not this app's.
#: - `learning` - the model is fitted from the log; there is no switch.
#: - `senses` is *not* in this list despite being a pure report, because the
#:   eighteen consent switches it reports on live in Settings > Senses.
NO_GEAR = (
    "conversations", "desktop", "diagnostics", "export", "inventory",
    "learning", "machine", "workbench",
)


@pytest.mark.parametrize("name", NO_GEAR)
def test_a_panel_settings_does_not_govern_has_no_gear(name, sidebar):
    """One gear per row is a lie when it opens an unrelated section.

    The failure this prevents is specific and was nearly shipped: a gear on
    `Diagnostics` opens Settings > System, which changes auto-start and debug
    logging and has nothing to do with a probe that could not answer. It opens
    *something*, so nothing looks broken - which is what makes it worse than the
    dead end it replaced.
    """
    assert name in sidebar._surface_rows, (
        f"{name!r} is not a panel, so this says nothing about it")
    assert name not in sidebar._gears, (
        f"{name!r} carries a settings gear, but Settings does not govern it. "
        "Either remove it from SETTINGS_TARGETS or add the switch it would "
        "open - do not leave a gear that opens something unrelated.")


def test_every_gear_is_labelled_and_names_its_section(sidebar):
    """An icon button with no label is announced as nothing."""
    for name, gear in sidebar._gears.items():
        tooltip = gear.get_tooltip_text()
        assert tooltip and tooltip.startswith("Open Settings on"), (
            f"{name}: the gear's tooltip does not say where it goes: {tooltip!r}")


def test_pressing_a_gear_opens_the_section_it_names(sidebar, shown):
    """The press, not the tooltip.

    A gear whose handler is not connected is the orb again. So each gear is
    pressed and the registry is watched.
    """
    assert sidebar._gears, "no gears were built, so there is nothing to press"
    for name, gear in sidebar._gears.items():
        before = len(shown)
        gear.emit("clicked")
        assert shown[before:], (
            f"{name}: pressing its gear reached nothing, so the button is present, "
            "named and does nothing")
    for name in sidebar._gears:
        gear = sidebar._gears[name]
        assert isinstance(gear, Gtk.Button)


def test_a_gear_reaches_the_same_route_a_panel_button_does(monkeypatch):
    """One route, not two.

    The sidebar's gear and a panel's own "Open Privacy settings" both go through
    `common.open_page`. Two implementations of "open Privacy" is the arrangement
    that lets one of them stop working while the other keeps going.
    """
    import inspect

    from shani_chronoa.gui import sidebar as sidebar_module

    source = inspect.getsource(sidebar_module)
    assert "common.open_page(" in source, (
        "the sidebar's gear does not route through common.open_page")
    tree = __import__("ast").parse(source)
    direct = [
        node for node in __import__("ast").walk(tree)
        if isinstance(node, __import__("ast").Call)
        and isinstance(node.func, __import__("ast").Attribute)
        and node.func.attr == "show"
        and getattr(node.func.value, "id", "") == "pages"
    ]
    assert not direct, (
        "the sidebar calls pages.show itself; every route goes through "
        "common.open_page so there is one implementation")


def test_a_gear_without_an_application_does_not_raise(sidebar):
    """`SidebarPage(None, None, None)` is how the tests build it, and it presses.

    A gear that raised inside its handler on an app-less window would take the
    window down, and a panel's own buttons have the same rule -
    `test_a_target_that_does_not_exist_is_reported_not_raised` covers the helper.
    """
    for gear in sidebar._gears.values():
        gear.emit("clicked")