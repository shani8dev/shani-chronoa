"""Fired rules, on screen.

`AmbientScheduler.event_results()` had **no caller anywhere in `usr/`**. An event
rule could fire a skill, change a setting or run a command with no sign of it in
the window: the automation was real and invisible. These tests are the section
that reads it.
"""

import sys
from collections import deque
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "usr/lib/shani-chronoa"))

import gi  # noqa: E402

gi.require_version("Gtk", "4.0")
from gi.repository import Gtk  # noqa: E402

from shani_chronoa.gui import rail as rail_module  # noqa: E402


class _Fired:
    """Shaped like the records a fire produces, by attribute rather than type -
    the shapes are another subsystem's, so the reader must not depend on them."""

    def __init__(self, **fields):
        for key, value in fields.items():
            setattr(self, key, value)


def _rail(events):
    return rail_module.NowRail(get_events=lambda: list(events))


def _labels(rail):
    out, queue = [], deque([rail])
    while queue:
        node = queue.popleft()
        if isinstance(node, Gtk.Label):
            out.append(node.get_label())
        if hasattr(node, "get_first_child"):
            child = node.get_first_child()
            while child is not None:
                queue.append(child)
                child = child.get_next_sibling()
    return out


def test_a_fired_rule_is_named_in_the_rail():
    rail = _rail([_Fired(rule_name="quiet hours", action="lowered the volume")])
    labels = _labels(rail)
    assert "Triggers" in labels, f"no Triggers section: {labels}"
    assert "quiet hours" in labels, f"the rule that fired is not shown: {labels}"
    assert any("volume" in text for text in labels), labels


def test_the_newest_firing_is_first():
    """The thing that just happened is the one being looked for."""
    rail = _rail([_Fired(rule_name="first rule"),
                  _Fired(rule_name="second rule")])
    labels = _labels(rail)
    assert labels.index("second rule") < labels.index("first rule"), \
        f"the rail is chronological: {labels}"


def test_a_refused_rule_is_shown_as_refused_not_hidden():
    """A rule that asked and was turned down is an action someone declined.

    A rail listing only successes makes a declined action look like nothing
    happened, which is the opposite of what happened.
    """
    rail = _rail([_Fired(rule_name="install a package", refused=True,
                         detail="you said no")])
    labels = _labels(rail)
    line = next((t for t in labels if "install a package" in t), None)
    assert line is not None, f"the declined rule is not shown at all: {labels}"
    # **On the same line as the rule.** The first version of this assertion
    # filtered out every label containing "install" - which is the one label
    # that matters - and then looked for the refusal wording somewhere else,
    # so it failed on a rail that was displaying the refusal perfectly well.
    assert "you said no" in line or "turned down" in line, (
        f"a refusal reads as a plain success: {line!r}")


def test_no_firings_says_so():
    labels = _labels(_rail([]))
    assert any("No rule has fired" in text for text in labels), \
        f"an empty Triggers section is indistinguishable from a broken one: {labels}"


def test_an_unreadable_source_does_not_take_the_rail_down():
    """The rail refreshes every two seconds, so this is where an exception
    would be both easy to cause and expensive to notice."""

    def _boom():
        raise RuntimeError("the scheduler is gone")

    rail = rail_module.NowRail(get_events=_boom)
    labels = _labels(rail)
    assert any("Could not read" in text for text in labels), \
        f"a broken source was not reported: {labels}"


def test_an_unfamiliar_record_renders_rather_than_crashing():
    """These shapes belong to another subsystem; none of them are the reader's
    to guarantee."""
    labels = _labels(_rail([object()]))
    assert labels, "an unfamiliar record took the whole rail down"


def test_a_record_with_nothing_in_it_is_skipped_quietly():
    rail = _rail([_Fired()])
    assert _labels(rail), "an empty record broke the section"


def test_the_window_without_a_scheduler_offers_no_events():
    """Before the application attaches one there is nothing to report, and the
    section must not imply that no rule has fired."""
    import gi
    gi.require_version("Gtk", "4.0")
    from shani_chronoa.gui.window import ChronoaWindow
    app = Gtk.Application(application_id="test.rail.triggers")
    window = ChronoaWindow(app)
    assert window._rail_events() == ()


def test_a_section_that_throws_says_so_rather_than_looking_empty():
    """**A section raising on every refresh must not be indistinguishable from
    one with nothing to report.**

    Found the hard way: `self._events.link(label, detail, icon, self.on_open,
    surface="triggers")` passed `surface` twice against a signature of
    `(text, detail, surface, on_open)`, so it raised `TypeError` every two
    seconds. `refresh()`'s per-section guard caught it and logged it, and the
    section rendered empty - which looks exactly like "no rules have fired".

    The log line was correct and useless: nobody reads the log on a two-second
    cadence, and the person looking at the rail sees an empty section.
    """
    rail = rail_module.NowRail(get_events=lambda: [])

    def _explode():
        raise TypeError("link() takes 4 positional arguments but 5 were given")

    rail._fill_events = _explode
    rail.refresh()

    labels = _labels(rail)
    assert any("Could not read this section" in text for text in labels), (
        f"a throwing section rendered as an empty one: {labels}")
    assert not any("No rule has fired" in text for text in labels), (
        "a throwing section claimed nothing had fired, which is a lie")
