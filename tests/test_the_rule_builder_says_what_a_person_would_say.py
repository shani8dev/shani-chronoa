"""The rule builder says what a person would say, not what a file holds.

**The arguments were JSON in the middle of a form.** Most rules need none
at all, and the field sat where it read like a required step - a person
writing "when the battery is low, notify me" had to know what a JSON
object was.

So the JSON is **behind an "Advanced" toggle**, with a line saying what
it is - the same shape the Diagnostics panel uses for what is missing.

**And a rule that passes no arguments said so.** `json.dumps({})` is
`"{}"`, and `"{}"` is a true string - so every rule with no arguments at
all drew a line reading *"arguments: {}"*, which is noise and JSON on a
row a person reads. The row now says nothing about arguments when there
are none, and the ones that *are* there are the skill's own schema, so
they belong behind a **Details** button - the pattern the Diagnostics
panel already uses, and one that does not depend on a hover, which never
arrives on a touch screen.
"""

from __future__ import annotations

import os
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

from shani_chronoa.gui.surfaces import triggers  # noqa: E402


class _App:
    application_id = "test.chronoa.rulebuilder"

    def activate(self):
        pass


def _page():
    """The built page, and the surface's logic behind it."""
    page = triggers.build(_App())
    return page, page.surface


def _walk(widget):
    queue, found = deque([widget]), []
    while queue:
        node = queue.popleft()
        found.append(node)
        if hasattr(node, "get_first_child"):
            child = node.get_first_child()
            while child is not None:
                queue.append(child)
                child = child.get_next_sibling()
    return found


def _texts(widget):
    """Every label's rendered text, in tree order."""
    return [(node.get_label() or "").strip()
            for node in _walk(widget) if isinstance(node, Gtk.Label)]


class _Rule:
    """A rule, as the store holds it."""

    def __init__(self, **over):
        self.name = "low-battery"
        self.sense = "power"
        self.match_mode = "substring"
        self.substring = "Discharging"
        self.keywords = []
        self.actuator = "notify"
        self.arguments = {}
        self.cooldown_seconds = 300
        self.debounce_seconds = 0
        self.retry_policy = ""
        self.enabled = True
        self.allow_destructive = False
        self.ask_first = False
        self.event_type = ""
        self.source = ""
        self.last_fired_at = None
        self.created_at = 0
        for key, value in over.items():
            setattr(self, key, value)


class TestTheBuilderDoesNotAskForJson:
    def test_the_json_field_is_not_labelled_as_json_in_the_form(self):
        """**A person writing "when the battery is low, notify me"** must
        not have to know what a JSON object is."""
        page, _ = _page()
        said = " ".join(_texts(page))
        assert "with arguments (JSON)" not in said, (
            "the arguments field is still labelled as JSON in the form")
        assert "with arguments" in said, (
            "the arguments field is not in the form at all")

    def test_the_json_is_behind_an_advanced_toggle(self):
        """**Most rules need none of it**, so it is behind a toggle that
        says what it is."""
        page, _ = _page()
        labels = [(w.get_label() or "") for w in _walk(page)
                  if isinstance(w, Gtk.ToggleButton)]
        assert "Advanced" in labels, labels

    def test_the_json_is_reachable_once_the_toggle_is_open(self):
        """**Hidden is not gone**: the field is still there for a person
        who needs it, and what it holds still reaches the rule."""
        _, view = _page()
        view._form_args.set_text('{"summary": "on battery"}')
        sent = view.form_arguments()
        assert sent["arguments"] == {"summary": "on battery"}, sent

    def test_the_field_still_refuses_a_malformed_object(self):
        """**Hiding the field must not hide the check**: a broken object
        is still refused, in the same words."""
        _, view = _page()
        view._form_args.set_text("{oops")
        assert "not valid JSON" in view.form_arguments()


class TestTheRowsDoNotPrintJson:
    def test_a_rule_with_no_arguments_says_nothing_about_them(self):
        """**`json.dumps({})` is `"{}"`, and `"{}"` is a true string** - so
        every rule with no arguments drew a line reading "arguments: {}"."""
        assert triggers._arguments_text(_Rule()) == "", (
            "a rule with no arguments still renders them")

    def test_a_rule_with_arguments_still_shows_them_to_the_reader(self):
        """**The ones that are there are the skill's own schema**, so they
        are not nothing."""
        said = triggers._arguments_text(_Rule(arguments={"summary": "hi"}))
        assert "summary" in said, said

    def test_the_tooltip_does_not_say_the_empty_case_either(self):
        """**The tooltip carried the same line**, so it said "arguments: {}"
        as well - and a tooltip is the one place a reader never looks."""
        said = triggers._tooltip(_Rule())
        assert "arguments: {}" not in said, said

    def test_the_row_body_has_no_json(self, tmp_path):
        """**A row a person reads is not the place for a JSON object** -
        and this rule *has* arguments, so the row is really there to be
        checked rather than absent."""
        rule = _Rule(arguments={"summary": "on battery"})
        page = triggers.build(_AppWithRule(tmp_path, rule))
        said = " ".join(_texts(page))
        assert "arguments: {" not in said, (
            f"a rule row is printing JSON: {said[:200]}")
        assert "on battery" in said, (
            "the rule's arguments are not readable anywhere on the page")

    def test_the_arguments_are_reachable_behind_a_details_button(self, tmp_path):
        """**Behind a Details button, not only in a tooltip** - a tooltip
        never arrives on a touch screen."""
        rule = _Rule(arguments={"summary": "on battery"})
        page = triggers.build(_AppWithRule(tmp_path, rule))
        buttons = [w for w in _walk(page)
                   if isinstance(w, Gtk.MenuButton)]
        assert buttons, "the rule row has no Details button"
        found = False
        for button in buttons:
            popover = button.get_popover()
            if popover is None:
                continue
            for node in _walk(popover):
                if isinstance(node, Gtk.Label) and "on battery" in (node.get_label() or ""):
                    found = True
        assert found, "the arguments are in no popover on the page"


class _AppWithRule(_App):
    """The app, carrying one armed rule in a store of its own.

    **A store at an explicit path**, never the default one: a
    `RuleStore()` with no arguments resolves the real per-user rules file,
    so a test that "forgot" would arm a rule in a real home.
    """

    def __init__(self, tmp_path, rule):
        from shani_chronoa.triggers.rules import RuleStore, TriggerRule

        store = RuleStore(tmp_path / "rules.json")
        store.add(TriggerRule(
            name=rule.name,
            sense=rule.sense,
            match_mode=rule.match_mode,
            substring=rule.substring,
            actuator=rule.actuator,
            arguments=rule.arguments,
            cooldown_seconds=rule.cooldown_seconds,
            enabled=rule.enabled,
        ))
        self.trigger_rule_store = store
        self.event_rule_store = None
        self.config = None

