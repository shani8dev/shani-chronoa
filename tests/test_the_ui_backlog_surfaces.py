"""The user-visible UI backlog: Today, Git, IA hubs and their shared contracts.

These are behaviour tests, not source-string checks. Each new surface is built
through its own `build()`, and the assertions ask the widget what it shows -
the store reads, the buttons, the consent prompts, the tabs. A source check
would pass with a surface that built nothing, which is the failure this repo
keeps recording.
"""

from __future__ import annotations

import importlib

import pytest

import gi  # noqa: E402

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Gtk  # noqa: E402

from shani_chronoa.gui import surfaces  # noqa: E402


class _App:
    """Only what a surface reads while being built; no config, no services."""

    def __init__(self):
        self.config = None
        self.window = None


def _buttons(widget):
    out = []

    def walk(w):
        if isinstance(w, Gtk.Button):
            out.append(w)
        child = w.get_first_child()
        while child is not None:
            following = child.get_next_sibling()
            walk(child)
            child = following

    walk(widget)
    return out


def _labels(widget):
    out = []

    def walk(w):
        text = (w.get_label() or "") if isinstance(w, Gtk.Label) else ""
        if text:
            out.append(text)
        child = w.get_first_child()
        while child is not None:
            following = child.get_next_sibling()
            walk(child)
            child = following

    walk(widget)
    return out


@pytest.fixture(scope="module")
def built():
    return {
        name: importlib.import_module(f"shani_chronoa.gui.surfaces.{name}")
        for name in (
            "today",
            "git_panel",
            "models_hub",
            "automation_hub",
            "changes_hub",
            "health_hub",
            "privacy_hub",
        )
    }


def test_new_surfaces_are_registered(built):
    for name in built:
        assert name in surfaces.SURFACE_IDS, name
        assert surfaces.sections()[name] in surfaces.SECTION_ORDER


def test_every_new_surface_builds_and_says_its_health(built):
    for name, module in built.items():
        page = module.build(_App())
        assert callable(page.status), name
        assert page.status() in surfaces.common.STATUS_CLASSES, name


def test_today_lists_five_groups_and_asks_for_writes(built):
    from shani_chronoa.gui.surfaces import today

    page = today.build(_App())
    labels = _labels(page)
    for title, _description, _prompt in today._GROUPS:
        assert title in labels, f"{title} is not on the Today page"
    # Every group offers a composer-filling control, not a direct write to a
    # chat-only store. Timer rows (Cancel/+5) appear only when a timer is
    # pending; those buttons are checked in the timer store's own tests.
    tips = [b.get_tooltip_text() or "" for b in _buttons(page)]
    fills = [t for t in tips if "Put “" in t]
    assert len(fills) == len(today._GROUPS), (
        f"only {len(fills)} of {len(today._GROUPS)} groups offer a composer-filling "
        "control; a write here would bypass the chat gate"
    )
    assert any("Try it" in (b.get_label() or "") for b in _buttons(page))


def test_git_panel_names_both_gates_and_both_actions(built):
    from shani_chronoa.gui.surfaces import git_panel

    page = git_panel.build(_App())
    labels = _labels(page)
    assert "Git" in labels
    assert any("Commit" in t for t in labels)
    assert any("Push" in t for t in labels)
    # The two writes are named as skills in the composer prompts, not performed.
    prompts = [b.get_tooltip_text() or "" for b in _buttons(page)]
    assert any("git_write" in p for p in prompts)
    assert any("git_push" in p for p in prompts)


@pytest.mark.parametrize(
    "name,members,first",
    [
        ("models_hub", ("model", "models"), "model"),
        ("automation_hub", ("daemon", "routines", "triggers", "goals"), "daemon"),
        ("changes_hub", ("diff", "editor", "artifact_store"), "diff"),
        (
            "health_hub",
            ("diagnostics", "desktop", "machine", "processes"),
            "diagnostics",
        ),
        ("privacy_hub", ("privacy", "senses"), "privacy"),
    ],
)
def test_hub_embeds_each_member_once(name, members, first, built):
    from gi.repository import Adw

    page = built[name].build(_App())
    stacks = []

    def walk(w):
        if isinstance(w, Adw.ViewStack):
            stacks.append(w)
        child = w.get_first_child()
        while child is not None:
            following = child.get_next_sibling()
            walk(child)
            child = following

    walk(page)
    assert stacks, f"{name} has no tab stack"
    for member in members:
        assert stacks[0].get_child_by_name(member) is not None, (
            f"{name} does not embed {member}"
        )
    assert stacks[0].get_visible_child_name() == first
