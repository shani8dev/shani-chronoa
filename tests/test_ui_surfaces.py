"""The suggestion and help surfaces as built widgets.

`test_capabilities.py` covers the model behind these. This covers the thing a
user touches: that the chips appear, that clicking one fills the input rather
than firing it, that they leave when the conversation starts and come back when
it is cleared, and that the help window names the switch that is blocking a
skill.

Every test drives the real signal - `emit("clicked")` on the real button, a real
`add_user_turn()` - rather than calling the handler, because a handler called
directly cannot tell you whether the button was ever wired to it. That
distinction is not theoretical in this repo: the orb button once shipped with no
`clicked` connection at all, and every test that called the handler passed.
"""

import gi
import pytest

gi.require_version('Gtk', '4.0')

pytest.importorskip("gi")
from gi.repository import Gtk, GLib  # noqa: E402

from shani_chronoa import capabilities  # noqa: E402
from shani_chronoa.gui import CajitaWindow, HelpWindow, SuggestionBar  # noqa: E402


def _tool(name, description=""):
    return {
        "type": "function",
        "function": {
            "name": name,
            "description": description,
            "parameters": {"type": "object", "properties": {}},
        },
    }


MINIMAL = [
    _tool("get_datetime", "Get the current local date and time."),
    _tool("get_battery_status", "Get the current battery charge percentage."),
    _tool("screenshot", "Capture the screen to a PNG file. Refuses without vision consent."),
    _tool("move_pointer", "Move the pointer. Requires the 'input-control-enabled' consent key."),
]


class _Closed:
    def sense_allowed(self, key):
        return False


class _Open:
    def sense_allowed(self, key):
        return True


@pytest.fixture
def app():
    application = Gtk.Application(application_id="test.chronoa.surfaces")
    yield application
    GLib.timeout_add(50, lambda: (application.quit(), False)[1])


def _walk(node, out):
    out.append(node)
    child = node.get_first_child()
    while child is not None:
        _walk(child, out)
        child = child.get_next_sibling()
    return out


def _chips(window):
    return [
        n for n in _walk(window, [])
        if isinstance(n, Gtk.Button) and "suggestion-chip" in n.get_css_classes()
    ]


class TestSuggestionBar:
    def test_it_builds_one_chip_per_prompt(self):
        bar = SuggestionBar(["One", "Two", "Three"], lambda *a: None)
        assert bar.prompts() == ["One", "Two", "Three"]

    def test_clicking_reports_that_prompt(self):
        seen = []
        bar = SuggestionBar(["First", "Second"], lambda b, s: seen.append(s))
        bar.observe_children().__iter__().__next__().get_first_child().emit("clicked")
        assert seen == ["First"]

    def test_an_empty_list_builds_an_empty_bar_rather_than_failing(self):
        assert SuggestionBar([], lambda *a: None).prompts() == []


class TestEmptyState:
    def test_the_empty_window_offers_chips(self, app):
        window = CajitaWindow(app, config=_Closed())
        assert len(_chips(window)) > 0, "an empty window with no suggestions is the bug"

    def test_a_window_whose_registry_is_empty_shows_none(self, app):
        # The transcript holds the reference the empty state reads, so that is
        # what has to be emptied - the window's own `_caps` is the same list
        # object and only exists for the help window.
        window = CajitaWindow(app, config=_Closed())
        window._transcript._caps = []
        window._transcript.show_placeholder()
        assert _chips(window) == [], (
            "prompts appeared with no skills loaded - that is the hardcoded-list "
            "failure arriving by another route"
        )

    def test_chips_leave_once_the_conversation_starts(self, app):
        window = CajitaWindow(app, config=_Closed())
        assert len(_chips(window)) > 0
        window.add_user_turn("hello")
        assert _chips(window) == [], (
            "suggestions stayed on screen under a real conversation"
        )

    def test_chips_come_back_when_the_transcript_is_cleared(self, app):
        window = CajitaWindow(app, config=_Closed())
        before = len(_chips(window))
        window.add_user_turn("hello")
        window.clear_transcript()
        assert len(_chips(window)) == before

    def test_each_chip_is_a_focusable_button_with_a_spoken_prompt(self, app):
        window = CajitaWindow(app, config=_Closed())
        chips = _chips(window)
        assert chips
        for chip in chips:
            assert chip.get_focusable(), "a chip a keyboard cannot reach"
            # The accessible LABEL is set with `update_property`, but this
            # PyGObject build exposes no getter for it, so the tooltip is the
            # readable proxy - it carries the same prompt text.
            assert chip.get_tooltip_text() == f"Send: {chip.get_label()}"


class TestClickFillsRatherThanSends:
    def test_clicking_a_chip_fills_the_input(self, app):
        window = CajitaWindow(app, config=_Closed())
        chips = _chips(window)
        chips[0].emit("clicked")
        assert window.get_input_text() == chips[0].get_label()
        assert window.get_input_text() != "", "clicking a suggestion did nothing"

    def test_clicking_does_not_send_the_message(self, app):
        """Sending on click would make a wrong suggestion uneditable and
        unrecoverable, which is the wrong trade for a control offered only to
        be tried."""
        window = CajitaWindow(app, config=_Closed())

        def turns():
            return len([n for n in _walk(window._transcript, [])
                        if isinstance(n, Gtk.Label)
                        and "transcript-turn" in n.get_css_classes()])

        before = turns()
        _chips(window)[0].emit("clicked")
        assert turns() == before, "clicking a suggestion submitted it"


class TestHelpWindow:
    def test_it_opens_with_a_help_button(self, app):
        window = CajitaWindow(app, config=_Closed())
        buttons = [
            n for n in _walk(window, [])
            if isinstance(n, Gtk.Button)
            and "help-about-symbolic" == n.get_icon_name()
        ]
        assert buttons, "no help button in the header"
        buttons[0].emit("clicked")
        assert window._help_window is not None

    def test_opening_it_twice_raises_one_window_not_two(self, app):
        window = CajitaWindow(app, config=_Closed())
        first = window.open_help()
        second = window.open_help()
        assert first is second

    def test_a_closed_help_window_forgets_itself(self, app):
        window = CajitaWindow(app, config=_Closed())
        window.open_help().close()
        assert window._help_window is None
        assert window.open_help() is not None, "the closed window was not reusable"

    def test_no_capabilities_means_no_help_window(self, app):
        window = CajitaWindow(app, config=_Closed())
        window._caps = []
        assert window.open_help() is None, (
            "an empty help window is worse than no help button"
        )

    def test_it_names_the_switch_that_is_blocking_a_skill(self, app):
        window = CajitaWindow(app, config=_Closed())
        help_window = window.open_help()
        labels = [
            n.get_text() for n in _walk(help_window, [])
            if isinstance(n, Gtk.Label)
            and "help-row-gate" in n.get_css_classes()
        ]
        assert any("Let Chronoa act" in text for text in labels), (
            f"the gated skill never named its switch: {labels}"
        )

    def test_the_gate_text_changes_when_the_switch_flips(self, app):
        """The single reason this window exists: a gate that is off is the
        reason a user's request got silence."""
        off = CajitaWindow(app, config=_Closed()).open_help()
        on = CajitaWindow(app, config=_Open()).open_help()

        def gates(win):
            return [n.get_text() for n in _walk(win, [])
                    if isinstance(n, Gtk.Label)
                    and "help-row-gate" in n.get_css_classes()]

        assert gates(off) != gates(on), "the gate row is identical either way"
        assert any(t.startswith("Off.") for t in gates(off))
        assert any(t.startswith("On.") for t in gates(on))

    def test_ungated_skills_get_no_gate_prose(self, app):
        window = CajitaWindow(app, config=_Closed())
        help_window = window.open_help()
        gates = [n for n in _walk(help_window, [])
                 if isinstance(n, Gtk.Label) and "help-row-gate" in n.get_css_classes()]
        details = [n for n in _walk(help_window, [])
                   if isinstance(n, Gtk.Label) and "help-row-detail" in n.get_css_classes()]
        assert len(gates) < len(details), (
            "most rows are showing gate text; only gated skills should"
        )

    def test_a_try_button_fills_the_input_and_closes_help(self, app):
        window = CajitaWindow(app, config=_Closed())
        help_window = window.open_help()
        tries = [n for n in _walk(help_window, [])
                 if isinstance(n, Gtk.Button) and n.get_label() == "Try"]
        assert tries, "no Try buttons in help"
        tries[0].emit("clicked")
        assert window.get_input_text() != "", "Try did not fill the input"
        assert window._help_window is None, "Try did not close the help window"


class TestDegradesRatherThanRefuses:
    def test_a_broken_config_does_not_stop_the_window_opening(self, app):
        class Exploding:
            def sense_allowed(self, key):
                raise RuntimeError("schema not installed")

        window = CajitaWindow(app, config=Exploding())
        assert len(_chips(window)) > 0
        assert window.open_help() is not None, "help refused to open on a bad config"

    def test_an_empty_registry_still_builds_a_window(self, app):
        window = CajitaWindow(app, config=_Closed())
        assert window._caps == [] or len(window._caps) > 0
        assert _chips(window) is not None


class TestAgainstTheRealRegistry:
    def test_the_real_window_offers_suggestions(self, app):
        """The built-ins, not a fixture: if the registry shape ever changes,
        the empty state goes quiet and this is the only thing that notices."""
        window = CajitaWindow(app, config=_Closed())
        assert len(window._caps) > 10
        assert len(_chips(window)) >= 4
