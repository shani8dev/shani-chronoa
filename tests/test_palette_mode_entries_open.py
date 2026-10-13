"""The palette's mode entries opened nothing.

`Ctrl+K` reached 50 entries, the palette itself was parented and scrollable,
and **every mode entry in it was inert**. Two independent faults, stacked, and
neither visible to the test that named the actions:

1. **The lookup ran on the wrong object.** `_activate_mode` read its chips with
   `getattr(self, ...)` - on the window. All seven are assigned inside
   `_ModeStrip.__init__`, so every lookup answered `None` and
   `if chip is not None` never fired. The three chips that *are* on the strip,
   and so could have opened perfectly well, did nothing either.

2. **Five chips are not in the window at all.** `5ef5c62` moved the model switch
   off the strip to make room and its parent moved plan, ambient, hands-free and
   reply style with it; nothing re-parented any of them. A `Gtk.Popover` whose
   parent is outside every toplevel cannot make a surface to draw into, which
   GTK says itself: *"Widgets must be inside a toplevel container before
   realizing them"*, followed by `gdk_surface_new_popup: GDK_IS_SURFACE(parent)
   failed`.

**The fix moves the popover, not the chip** - the chip stays off the strip,
which measures 97% full and is why it moved - and **the move is
`unparent()` then `set_parent()`**, because `set_parent` alone refuses with a
critical on stderr and no exception: a one-line version of this fix looks
applied and changes nothing.

**The assertion is the property, not the branch.** Every test says "after the
entry is chosen, the popover's parent can reach the window", which holds whether
the chip is on the strip or not. A test asserting only the off-strip chips
would be satisfied by a fix that broke the three on-strip ones.
"""

import sys
from pathlib import Path

import pytest

gi = pytest.importorskip("gi")
gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, GLib, Gtk  # noqa: E402

Adw.init()

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "usr/lib/shani-chronoa"))

from shani_chronoa.gui.window import ChronoaWindow  # noqa: E402


#: The seven entries `_MODE_TABLES` builds, and the chip each names. These are
#: the names on the app, so a rename cannot quietly shorten the list while the
#: tests go on passing over a smaller one.
MODE_CHIPS = (
    ("answer-rules", "_rules_chip"),
    ("toggle-plan-mode", "_plan_chip"),
    ("answer-egress", "_answer_chip"),
    ("answer-ambient", "_ambient_chip"),
    ("answer-presence", "_presence_chip"),
    ("hands-free", "_hands_chip"),
    ("reply-style", "_style_chip"),
)


def _inside(window, widget):
    """Walk parents to the window. A hidden ancestor still counts - `Gtk.Popover`
    needs its parent *in* a toplevel, not *shown*."""
    node = widget
    while node is not None:
        if node is window:
            return True
        node = node.get_parent()
    return False


def _chip():
    """A chip shaped like the real ones: a `MenuButton` owning a `Popover`."""
    button = Gtk.MenuButton()
    popover = Gtk.Popover()
    popover.set_child(Gtk.ListBox())
    button.set_popover(popover)
    return button


class _StandIn:
    """A window with just the two members `_open_chip_dropdown` reads.

    **The helpers are driven unbound against this rather than through a real
    `ChronoaWindow`.** The claim being tested is about the widget tree - which
    object a popover's parent can reach - and a real window brings a D-Bus
    name, twenty panels of idle seeding and a teardown that segfaults on a
    loaded box. None of that can change the answer, and all of it can stop the
    test running. `TestAgainstTheRealWindow` below is where the product claim is
    made, once, on a real window.
    """

    def __init__(self):
        self.window = Gtk.Window()
        self.box = Gtk.Box()
        self.window.set_child(self.box)
        self._palette = Gtk.Box()
        self.box.append(self._palette)

    def _inside_window(self, widget):
        """**The window, not this wrapper.**

        `_inside_window` walks parents looking for the object it is bound to,
        so binding it to a wrapper around a `Gtk.Window` makes it walk off the
        top of the tree and answer False for *every* widget - which sends all of
        them to the palette branch and leaves the on-strip case untested while
        every test still passes a plausible-looking assertion.
        """
        return _inside(self.window, widget)

    _open_chip_dropdown = ChronoaWindow._open_chip_dropdown


class TestTheMechanism:
    """The popover lands somewhere a popover can be drawn."""

    def test_a_chip_outside_the_window_ends_up_inside_it(self):
        """The shipped defect: the popover stayed on a chip no toplevel
        contains, so `popup()` asked GTK for a surface it cannot have."""
        s = _StandIn()
        chip = _chip()                       # never added to s.box
        assert not _inside(s.window, chip), "the fixture is not an orphan"

        assert s._open_chip_dropdown(chip) is True
        parent = chip.get_popover().get_parent()
        assert _inside(s.window, parent), (
            f"the popover is still anchored to {type(parent).__name__}, "
            "which is not in the window")

    def test_the_anchor_is_the_palette_and_not_something_else(self):
        """Inside the window is necessary and not sufficient - a popover
        re-parented to some other detached box satisfies the first and still
        cannot be shown. This says *which* widget it lands on."""
        s = _StandIn()
        chip = _chip()
        s._open_chip_dropdown(chip)
        assert chip.get_popover().get_parent() is s._palette

    def test_a_chip_inside_the_window_keeps_its_own_anchor(self):
        """**The half that could have been deleted.**

        Anchoring everything to the palette would make the test above pass -
        every popover inside the window - while moving a chip's own dropdown
        away from the chip it belongs to. So the on-strip case is asserted
        separately, and it is the case that actually occurs for three chips.
        """
        s = _StandIn()
        chip = _chip()
        s.box.append(chip)
        assert _inside(s.window, chip)

        assert s._open_chip_dropdown(chip) is True
        assert chip.get_popover().get_parent() is chip

    def test_opening_twice_does_not_stack_or_drift(self):
        """The palette can be reopened and re-chosen, so this runs more than
        once per chip in a session."""
        s = _StandIn()
        chip = _chip()
        s._open_chip_dropdown(chip)
        first = chip.get_popover().get_parent()
        s._open_chip_dropdown(chip)
        assert chip.get_popover().get_parent() is first is s._palette

    def test_a_chip_that_later_lands_in_the_window_gets_its_anchor_back(self):
        """The re-anchoring is not one-way. A chip re-parented onto the strip -
        which is the fix somebody would reach for, and the reason the strip is
        97% full - must not be left with a popover stranded over the palette."""
        s = _StandIn()
        chip = _chip()
        s._open_chip_dropdown(chip)
        assert chip.get_popover().get_parent() is s._palette

        s.box.append(chip)
        s._open_chip_dropdown(chip)
        assert chip.get_popover().get_parent() is chip

    def test_a_chip_with_no_popover_is_refused_rather_than_raising(self):
        """A `MenuButton` with no popover is not a thing the product builds, and
        `_open_chip_dropdown` says so rather than raising through a palette
        callback - where an exception aborts the main loop instead of
        propagating to the test."""
        s = _StandIn()
        assert s._open_chip_dropdown(Gtk.Button()) is False

    def test_no_window_at_all_is_refused_rather_than_raising(self):
        """The anchor has to come from somewhere, and a window whose palette has
        not been built yet has nowhere to put it."""
        s = _StandIn()
        s._palette = None
        assert s._open_chip_dropdown(_chip()) is False


class TestTheLookup:

    def test_the_lookup_names_the_strip_not_the_window(self):
        """The shipped lookup, restored verbatim.

        `getattr(self, "_rules_chip", None)` on the window answers `None` for
        all seven names, so the entry does nothing - and the name resolves
        cleanly, so nothing raises and nothing is logged.
        """
        s = _StandIn()
        window, strip = s.window, Gtk.Box()
        s.box.append(strip)
        s._mode_strip = strip
        for _, attr in MODE_CHIPS:
            setattr(strip, attr, _chip())
        # this is what `_activate_mode` used to do
        assert getattr(window, "_rules_chip", None) is None
        # and this is what it does
        assert getattr(strip, "_rules_chip", None) is not None


class TestAgainstTheRealWindow:
    """One real window, every entry, asserted where the chips really are."""

    def test_every_palette_mode_entry_opens_its_dropdown_inside_the_window(self):
        from shani_chronoa.app import ChronoaApplication

        app = ChronoaApplication()
        app.hold()
        out = {}

        def step():
            try:
                window = ChronoaWindow(app)
                app.window = window
                window.present()
                strip = window._mode_strip

                # where each chip actually is, before anything is opened
                on_window = {a: getattr(window, a, None) is not None
                             for _, a in MODE_CHIPS}
                in_tree = {a: _inside(window, getattr(strip, a, None))
                           for _, a in MODE_CHIPS}
                orphan = [a for _, a in MODE_CHIPS if not in_tree[a]]

                results = {}
                for action, attr in MODE_CHIPS:
                    window._activate_mode(action)
                    chip = getattr(strip, attr)
                    results[attr] = _inside(
                        window, chip.get_popover().get_parent())

                # the model entry builds its list by hand, and called
                # `_model_chip.popup()` directly - the same broken call on the
                # same unparented chip, and the one a fix walking only
                # `_MODE_TABLES` would miss
                entries = [e for e in window._palette_mode_entries()
                           if e.title == "Which model answers"]
                if entries:
                    entries[0].run()
                    results["_model_chip"] = _inside(
                        window, window._mode_strip._model_chip.get_popover()
                        .get_parent())
                out.update(on_window=on_window, in_tree=in_tree,
                           orphan=orphan, results=results,
                           model_entry=len(entries))
            except Exception as exc:                       # noqa: BLE001
                out["error"] = f"{type(exc).__name__}: {exc}"
            app.quit()
            return False

        GLib.timeout_add(500, step)
        GLib.timeout_add(60000, lambda: (app.quit(), False)[1])
        app.run(["mode-entries"])

        assert "error" not in out, out.get("error")
        assert not any(out["on_window"].values()), (
            "a mode chip is now also on the window - `_activate_mode` must not "
            "look for it there, or the two can drift apart again")
        assert all(out["results"].values()), (
            "entries whose dropdown is not anchored inside the window: "
            f"{[k for k, v in out['results'].items() if not v]}")
        assert out["model_entry"] == 1, "the model entry is gone from the palette"
        assert out["results"].get("_model_chip") is True, (
            "the model dropdown is still anchored outside the window")
        assert out["orphan"], (
            "every chip is now in the window, so the re-anchoring branch is "
            "dead - worth knowing rather than guessing at")
