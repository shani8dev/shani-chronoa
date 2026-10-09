"""The Help window can be searched, and a search hides what it does not match.

The list is over a hundred capabilities in one scroll, and finding "timer" meant
scrolling past calendar, date arithmetic and notifications to get to it. The
window also used to draw its own title and close button under the title bar's
own, so the same heading appeared twice; that space now holds the search.
"""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "usr/lib/shani-chronoa"))

gi = pytest.importorskip("gi")
gi.require_version("Gtk", "4.0")
from gi.repository import Gtk  # noqa: E402

from shani_chronoa.capabilities import Capability  # noqa: E402
from shani_chronoa.gui.widgets import HelpWindow  # noqa: E402


class _Config:
    def get_bool(self, _key, default=False):
        return False


_CAPS = [
    Capability(tool="timer", group="Time and reminders", label="Timer",
               description="Set a countdown timer.", example="set a timer"),
    Capability(tool="get_datetime", group="Time and reminders", label="Date and time",
               description="The current date and time here."),
    Capability(tool="notify", group="Everyday tools", label="Send a notification",
               description="Send a desktop notification.",
               consent_key="notification-enabled"),
]


def _visible_labels(window, css="help-row-label"):
    out = []

    def walk(node, shown):
        shown = shown and node.get_visible()
        if isinstance(node, Gtk.Label) and css in node.get_css_classes() and shown:
            out.append(node.get_label())
        child = node.get_first_child()
        while child is not None:
            walk(child, shown)
            child = child.get_next_sibling()

    walk(window.get_child(), True)
    return out


@pytest.fixture()
def window():
    return HelpWindow(_CAPS, _Config())


def test_everything_is_listed_before_a_search(window):
    assert sorted(_visible_labels(window)) == ["Date and time", "Send a notification", "Timer"]


def _tasks_matching(word):
    from shani_chronoa import capabilities
    return sum(1 for t in capabilities.EVERYDAY_TASKS
               if word in " ".join((t.title, t.how, t.prompt)).lower())


def test_a_search_keeps_only_matching_rows(window):
    # The search covers the "Get things done" cards too (one mentions a timer).
    assert window.filter("timer") == 1 + _tasks_matching("timer")
    assert _visible_labels(window) == ["Timer"]


def test_a_search_matches_the_switch_that_gates_a_row(window):
    """Someone looking for why notifications do nothing searches for the switch."""
    assert window.filter("spoken replies") == 1
    assert _visible_labels(window) == ["Send a notification"]


def test_no_match_hides_every_row_and_says_so(window):
    """Control: a needle nothing contains must leave nothing visible."""
    assert window.filter("zzzz-no-such-thing") == 0
    assert _visible_labels(window) == []
    assert _visible_labels(window, "help-group-heading") == [], "a heading over nothing"
    assert window._empty.get_visible()


def test_clearing_the_search_restores_the_list(window):
    from shani_chronoa import capabilities
    window.filter("timer")
    assert window.filter("") == 3 + len(capabilities.EVERYDAY_TASKS)
    assert len(_visible_labels(window)) == 3
    assert not window._empty.get_visible()


def test_the_window_does_not_draw_a_second_close_button(window):
    closes = []

    def walk(node):
        if isinstance(node, Gtk.Button) and node.get_icon_name() == "window-close-symbolic":
            closes.append(node)
        child = node.get_first_child()
        while child is not None:
            walk(child)
            child = child.get_next_sibling()

    walk(window.get_child())
    assert not closes, "the content draws its own close button under the title bar's"
