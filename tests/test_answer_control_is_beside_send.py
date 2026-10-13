"""One answer control beside Send: which model, how it works, how it replies.

**Why the control exists, measured rather than argued.** At the size this window
opens at (460x640) the strip was 480px of a 496px chat column - five chips, no
room for a sixth - so `_model_chip` and `_style_chip` were *constructed and
never appended*, and "which model answers" was reachable only through the
palette. A control whose only path is a keyboard shortcut nobody has been told
about is a control that is not there.

The composer row, meanwhile, was 496px holding two 34px buttons around 412px of
typing room. That is where the width was, so that is where the control went.

**The shape is both other products' own.** ChatGPT's documentation says *"In the
model menu... Use the Thinking slider to choose your effort"* - model and effort
are one control. Claude titles the equivalent page *"Change the model, effort,
and thinking settings"*. So the three sections below are one question, not three
chips asking it separately.

**And the test this file exists for is `test_each_row_carries_its_own_target`.**
The first version of the reply-style loop bound `_key` and then interpolated a
bare `key`, so all three rows inherited whatever `key` the *model* loop above had
left behind and every row read `reply-style:qwen3-8b`. Nothing caught it:
`test_mode_strip` stayed green throughout, because no test presses a menu row.
The section rendered, three rows appeared, the words were right, and the control
would have done nothing. That is the dead-control class this repository records
repeatedly, and the only thing that found it was reading the built menu's
targets rather than its appearance.
"""
import pytest

gi = pytest.importorskip("gi")
gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gio, GLib, Gtk  # noqa: E402

from shani_chronoa.gui.window import _ModeStrip  # noqa: E402
from shani_chronoa import local_llm  # noqa: E402


@pytest.fixture(scope="module")
def gapp():
    """One registered application carrying the actions the menu rows name.

    `register()` is what makes `activate_action` work at all; without it GIO
    drops the activation. Mirrors `test_mode_strip`'s fixture rather than
    inventing a second one.
    """
    app = Gtk.Application(application_id="test.chronoa.answer")
    app.register(None)
    for name in ("answer-model", "toggle-plan-mode", "reply-style"):
        action = Gio.SimpleAction.new(name, GLib.VariantType.new("s"))
        action.connect("activate", lambda *_a: None)
        app.add_action(action)
    yield app
    app.quit()


@pytest.fixture
def state(gapp, tmp_path, monkeypatch):
    class _Config:
        def get(self, key, default=None):
            return default

        def get_bool(self, key, default=False):
            return default

        def get_string(self, key, default=""):
            return default

    app = gapp
    app.config = _Config()
    # A known machine: two installed tiers, so "which model" has a real choice
    # and the two rows can be told apart.
    monkeypatch.setattr(local_llm, "installed", lambda: ["qwen3-1.7b", "qwen3-8b"])
    monkeypatch.setattr(local_llm, "active", lambda: "qwen3-1.7b")
    yield app
    del app.config


@pytest.fixture
def strip(state):
    s = _ModeStrip(state, state.config)
    yield s
    s.unparent()


def _sections(button):
    """`[(heading, [(label, action, target), ...]), ...]` read off the built menu.

    `Gio.Menu` exposes `get_item_attribute_value`, **not**
    `get_item_attribute` - three names were guessed wrong before that one, and
    the mistake is recorded here so the next reader does not repeat it.
    """
    model = button.get_menu_model()
    out = []
    for i in range(model.get_n_items()):
        sub = model.get_item_link(i, "section")
        heading = model.get_item_attribute_value(i, "label", None)
        rows = []
        for j in range(sub.get_n_items()):
            rows.append((
                sub.get_item_attribute_value(j, "label", None).get_string(),
                sub.get_item_attribute_value(j, "action", None).get_string(),
                (lambda t: t.get_string() if t else None)(
                    sub.get_item_attribute_value(j, "target", None)),
            ))
        out.append((heading.get_string() if heading else "", rows))
    return out


def _section(menu, heading):
    for name, rows in menu:
        if name == heading:
            return rows
    raise AssertionError(f"no {heading!r} section in {[n for n, _ in menu]}")


# --- the move ---------------------------------------------------------------

def test_the_answer_control_rides_with_send_not_the_strip(strip):
    """It is a child of the composer row, not of the strip above it."""
    button = strip._answer_button
    assert button is not None, "the consolidated control was never built"
    assert button.get_parent() is not strip, (
        "the answer control is still a child of the strip, so it is still on "
        "the row that measured 480px of a 496px column")


def test_the_strip_no_longer_holds_the_plan_chip(strip):
    """Five chips became four, and the freed width is the point."""
    children = []
    child = strip.get_first_child()
    while child is not None:
        children.append(child)
        child = child.get_next_sibling()
    assert strip._plan_chip not in children, (
        "plan mode is still on the strip, so the strip is still full")
    assert len(children) == 4, (
        f"expected capability, posture, presence and egress; got {len(children)}")


def test_the_plan_chip_is_still_built_and_still_drawn(strip):
    """It moved, it was not deleted - `test_mode_strip` asserts its css class."""
    assert strip._plan_chip is not None
    assert hasattr(strip, "_plan_label")


# --- the three settings -----------------------------------------------------

def test_the_menu_holds_all_three_settings_as_sections(strip):
    menu = _sections(strip._answer_button)
    assert [name for name, _ in menu] == [
        "Which model", "How it works it out", "How it replies"], menu


def test_each_row_carries_its_own_target(strip):
    """**The witness for the loop-variable bug.**

    Every reply-style row must name the style it offers. When the loop bound
    `_key` and interpolated a bare `key`, all three inherited the model loop's
    last key and read `reply-style:qwen3-8b` - a style that does not exist, on
    every row, with the section still rendering three correct-looking words.
    """
    rows = _section(_sections(strip._answer_button), "How it replies")
    targets = [target for _label, _action, target in rows]
    assert targets == ["ordinary", "brief", "explanatory"], (
        f"reply-style rows carry {targets}, so pressing one would ask for a "
        f"style that is not in _STYLES")


def test_the_two_model_rows_target_different_models(strip):
    """Two rows that fire the same action are only interchangeable if both
    targets are None - which is right for plan (a toggle) and wrong for a model."""
    rows = _section(_sections(strip._answer_button), "Which model")
    targets = [target for _label, _action, target in rows]
    assert targets == ["qwen3-1.7b", "qwen3-8b"], (
        f"model rows carry {targets} - two rows that would do the same thing")


def test_the_plan_rows_fire_the_toggle_with_no_target(strip):
    """Both rows activate the same action: it is a toggle, not a choice."""
    rows = _section(_sections(strip._answer_button), "How it works it out")
    assert [action for _l, action, _t in rows] == [
        "app.toggle-plan-mode", "app.toggle-plan-mode"]
    assert all(target is None for _l, _a, target in rows)


def test_the_button_words_the_model(strip):
    """The model had no home on screen at all; this is where it goes."""
    assert strip._answer_label.get_text() == "1.7B", (
        "the control does not name the model, which was the whole reason for "
        "moving it")


def test_the_tooltip_names_all_three_settings(strip):
    tooltip = strip._answer_button.get_tooltip_text()
    assert "1.7B" in tooltip, tooltip
    assert "Whether it plans first" in tooltip, tooltip
    assert "Reply style" in tooltip, tooltip


# --- the properties the strip already held ----------------------------------

def test_no_menu_row_is_a_sentence(strip):
    """The layout contract fails any menu label over 30 characters.

    A `Gtk.PopoverMenu` renders `label` and nothing beside it, so a sentence
    there is a sentence that cannot fit - measured at 1,227px on libadwaita.
    """
    for _heading, rows in _sections(strip._answer_button):
        for label, _action, _target in rows:
            assert len(label) <= 30, f"{label!r} is {len(label)} characters"


def test_the_section_headings_are_short_too(strip):
    for name, _rows in _sections(strip._answer_button):
        assert len(name) <= 30, f"{name!r} is {len(name)} characters"