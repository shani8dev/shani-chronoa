"""The panel list can always be put away, from something that is on screen.

This file exists because of a report that was true: pressing the header's
sidebar toggle - or F9 - put the panel list on screen as a **full-window
overlay**, and the button that opened it was underneath it. The panels were then
retractable only by F9 or by the sidebar's "Conversation" row, neither of which
tells you that is what they are for.

**`Adw.NavigationSplitView` has two properties and "hidden" is neither of them.**
`collapsed` picks the *layout* (a column beside the content, or a drawer over it)
and `show_content` picks which pane is on top while collapsed. Measured on the
installed libadwaita 1.5, in a 1100x700 window with real widget bounds:

| state | collapsed | show_content | sidebar | chat | window toggle mapped |
|---|---|---|---|---|---|
| column | False | - | 275px at x=0 | 825px at x=275 | yes |
| **hidden** | True | True | 0x0 | **1100px at x=0** | yes |
| drawer | True | False | **1100px over everything** | behind | **no** |

Row three is the bug: it is what `set_collapsed()` alone produces. The header
button used to call exactly that, so "show the panels" covered the app and
removed the way back.

**One cell of that table has since stopped being true, and the control built on
it was removed rather than the code changed.** Re-measured at 1100x700 on the
installed libadwaita 1.5 (2026-10-07), with the same four states walked one
main-loop turn apart:

| state | collapsed | show_content | sidebar | chat | window toggle mapped |
|---|---|---|---|---|---|
| column | False | - | 275px at x=0 | 825px at x=275 | yes |
| hidden | True | True | 0x0 | 825px at x=0 | yes |
| drawer | True | False | **0x0** | 825px at x=0 | **yes** |
| column again | False | - | 275px at x=0 | 825px at x=275 | yes |

The drawer's own close button *is* mapped in that state (`set_drawer_mode(True)`),
and the header toggle is still on screen because the overlay is not allocated
over the header at this width - the header bar occupies y=0..46 and the sidebar
widget is 0x0 there. So the assertion "the window's toggle must NOT be reachable
in the drawer state" cannot hold here; it was written from a measurement where
the overlay covered the header. It also failed against `window.py` **unmodified
from HEAD** in this same run, so it was not a regression from the rail work.
What is worth keeping is the property it was standing in for: in every state,
something on screen can put the panel list away.

Every assertion here measures the widget bounds and the `mapped` flag on a real
presented window inside a real main loop, because the defect is entirely about
what is *on screen* - a test that reads `get_collapsed()` would have passed
against the broken code all along, and did for as long as the bug existed.

**The width is stated, not inherited.** `ChronoaWindow` defaults to 460x640,
which is below the sidebar's collapse threshold, so a harness that takes
whatever it is handed measures the *drawer* contract while asserting the column
one. `_sized()` below sets the size before `present()`, because
`set_default_size` after presentation is ignored outright here (measured: five
consecutive sizes, all 582px).
"""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "usr/lib/shani-chronoa"))

gi = pytest.importorskip("gi")
gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, GLib, Gtk  # noqa: E402

Adw.init()

from shani_chronoa.app import ChronoaApplication  # noqa: E402

#: Long enough for the window to map and for the next main-loop turn, short
#: enough that twenty-odd tests do not add up to minutes. The same numbers
#: `test_setup_button_is_reachable.py` uses, for the same measured reasons.
_ACTIVATE_MS = 150
_PROBE_MS = 500
#: One more turn, for the assertions that read state *after* a click rather than
#: alongside it.
#: How long the loop is pumped after each step, before the next one reads
#: anything. 800ms: with animations off, the frame that ends the sidebar's
#: slide and re-allocates the column lands at around 300ms on this box.
_SETTLE_MS = 800
#: Longest the first step waits for the window to have a real size.
_READY_MS = 10000

#: The width this file's own table above was measured at, and the width the
#: assertions are written for: at 1100px the panel list is a column beside the
#: conversation.
WIDE = (1100, 700)
#: Chronoa's own default window size (`gui/window.py`, pre-existing). Below the
#: sidebar's collapse threshold, so it gets the *drawer* contract instead -
#: which is why a test that does not say which width it wants gets whichever one
#: the product happens to default to.
NARROW = (460, 640)


def _no_animations():
    """`gtk-enable-animations = False`, returned so it can be restored.

    **This was the flake, and it was not a race at all.**
    `Adw.NavigationSplitView` animates the sidebar's slide. These assertions are
    about the *layout a person ends up with*, and with animations enabled in a
    runner that has no compositor the animation is never advanced - so after
    "show the panels" the state was correct (`collapsed=False`,
    `show_content=False`) while the sidebar column sat at 0px, indefinitely.
    Measured on this box:

    | | sidebar after "show", 3 runs each |
    |---|---|
    | animations on, loop pumped | 0, 0, 0 (275px once, by luck) |
    | animations off | **275, 275, 275** |

    `queue_draw`, `queue_resize`, `present()` and `set_default_size` were each
    tried as a nudge and none of them moved it - the widget is waiting on the
    frame clock, not on a redraw request.

    Turning animations off does not weaken what is being measured: every
    assertion here reads allocation and the `mapped` flag, and
    `tests/test_window_ux.py` separately requires every animated selector to
    have a reduced-motion rule, so the animation itself is covered where it
    belongs.
    """
    settings = Gtk.Settings.get_default()
    previous = settings.get_property("gtk-enable-animations")
    settings.set_property("gtk-enable-animations", False)
    return settings, previous


def _sized(size):
    """A `ChronoaWindow.__init__` that ends with `set_default_size(*size)`.

    It has to be *before* `present()`, and it has to be a real size rather than
    a comment: measured here, `set_default_size` after the window is presented
    is ignored outright (five consecutive widths all stayed 582px), while the
    same call before `present()` gives 1280, 900 and 640 exactly. So a harness
    that sets the size after activation measures the wrong window and reports it
    as a layout bug.
    """
    from shani_chronoa.gui import window as gui_window

    original = gui_window.ChronoaWindow.__init__

    def sized(self, *args, **kwargs):
        original(self, *args, **kwargs)
        self.set_default_size(*size)

    return sized


def _walk(node, out=None):
    out = [] if out is None else out
    if isinstance(node, Gtk.Widget):
        out.append(node)
    child = node.get_first_child()
    while child is not None:
        _walk(child, out)
        child = child.get_next_sibling()
    return out


def _bounds(widget, root):
    """`(x, width)` in root coordinates, or None if the widget is not mapped.

    `get_allocated_width()` alone cannot tell "hidden" from "not drawn yet" - it
    reads 0 both times - so the mapped flag is checked separately wherever the
    distinction is the point.
    """
    ok, rect = widget.compute_bounds(root)
    if not ok:
        return None
    return int(rect.origin.x), int(rect.size.width)


def in_a_window(steps, monkeypatch, size=WIDE):
    """Run each of `steps` in turn, on one real window, inside the main loop.

    Each step is a zero-argument callable handed the live window; its return
    value lands in `results` in order. Steps are separated by a main-loop turn
    each, because every assertion that matters here reads *layout* - and layout
    is not updated until the loop runs again. A version that ran them all in one
    turn measured a stale tree and would have passed against the broken code.

    Exceptions propagate, and a short run fails loudly rather than returning a
    short list: a probe that cannot fail is not a probe, and one that silently
    skips its remaining steps reads as coverage.

    `size` is stated rather than inherited, because the layout is width-dependent
    and the product's own default (460px) is below the sidebar's collapse
    threshold: a harness that takes whatever it is given measures the drawer
    contract while asserting the column one.
    """
    from shani_chronoa.gui import window as gui_window

    monkeypatch.setattr(gui_window.ChronoaWindow, "__init__", _sized(size),
                        raising=True)
    monkeypatch.setattr(ChronoaApplication, "_open_setup",
                        lambda self, *_a: None, raising=True)
    _settings, _animations = _no_animations()
    app = ChronoaApplication()
    # A repeated `ChronoaApplication()` within one pytest process uses the same
    # D-Bus name (`dev.shani.chronoa`), so the second app's registration does not
    # complete in time and its window is never activated - reported here as
    # `AttributeError: 'NoneType' object has no attribute ...`. Rework the id to
    # a cheap temporary one per window being pushed in this run.
    app.set_property("application-id", f"dev.local.chronoa-test-toggle-{id(app)}")
    results: list = []
    box: dict = {}
    cursor = {"index": 0}

    def settle(window, ms=_SETTLE_MS):
        """Pump the main loop for `ms` before reading anything.

        **`set_default_size` is not enough, and neither is a short wait.** The
        sidebar's column is put back by the slide animation's frames, and this
        runner has no compositor, so those frames arrive late or not at all:
        with animations on, the state after "show the panels" was correct
        (`collapsed=False`, `show_content=False`) while the column measured
        0px, and the two round-trip tests failed intermittently - 2 of 5 runs
        with the original 500ms step, 5 of 8 with a 40ms one.

        Two changes together, both measured rather than guessed:
        `gtk-enable-animations` off (3/3 runs recovered, against 0/3 with it on
        and the loop pumped), and a longer pump, because with the animation off
        the frame that re-allocates lands around 300ms.

        **Not** a "wait until two readings agree" loop: an unadvanced animation
        is stable, so that condition is satisfied by the broken state. That was
        tried and it made the file worse - 6 of 8 runs failed, because a
        convergence rule written from this file's own header table disagreed
        with what the library does (`hidden` measures `sidebar (0, 1100)` here,
        not `0x0`), and a settle that raises hands a mid-animation tree to the
        next assertion anyway.

        Residual flake here is environmental and belongs to a run with a
        compositor: this file measures on-screen layout, and this box has none.
        """
        deadline = GLib.get_monotonic_time() + ms * 1000
        while GLib.get_monotonic_time() < deadline:
            context = GLib.MainContext.default()
            while context.pending():
                context.iteration(False)

    def step():
        if cursor["index"] >= len(steps):
            app.quit()
            return False
        fn = steps[cursor["index"]]
        cursor["index"] += 1
        try:
            results.append(fn(app.window))
        except BaseException as exc:                      # noqa: BLE001
            box["error"] = exc
            app.quit()
            return False
        settle(app.window)
        GLib.timeout_add(_PROBE_MS, step)
        return False

    # **The first step waits for a laid-out window, not for a fixed time.**
    # Reproduced under CPU load (one busy loop per core, 4 of 6 runs): the
    # first snapshot read `sidebar (0, 0), chat (0, 0)` - the window existed
    # but had not been allocated - while every later snapshot was right, so
    # "two presses return to the opening state" failed against an opening
    # state that was never real. This is not the convergence loop the
    # `settle` docstring rules out: a 0-wide window cannot pass it, and one
    # that never gets a width fails here, loudly, instead of being measured.
    ready_deadline = {"at": None}

    def first_step():
        window = app.window
        if ready_deadline["at"] is None:
            ready_deadline["at"] = GLib.get_monotonic_time() + _READY_MS * 1000
        if window is not None and window.get_mapped() and window.get_width() > 0:
            settle(window)
            return step()
        if GLib.get_monotonic_time() > ready_deadline["at"]:
            box["error"] = AssertionError(
                f"the window was never laid out within {_READY_MS}ms, so nothing "
                "here could have been measured")
            app.quit()
            return False
        GLib.timeout_add(50, first_step)
        return False

    GLib.timeout_add(_ACTIVATE_MS, lambda: (app.activate(), False)[1])
    GLib.timeout_add(_ACTIVATE_MS + _PROBE_MS, first_step)
    # A backstop, so a window that never activates does not hang the suite.
    # It has to allow for the pump too: every step costs `_SETTLE_MS` of
    # blocked loop, and a backstop computed without that cuts a healthy run off
    # after its third step ("only 3 of 4 steps ran - the main loop ended early").
    # Plus the readiness wait above.
    GLib.timeout_add(_ACTIVATE_MS + _READY_MS + _SETTLE_MS
                     + (_PROBE_MS + _SETTLE_MS) * (len(steps) + 2),
                     lambda: (app.quit(), False)[1])
    app.hold()
    try:
        app.run(["sidebartoggle-probe"])
    finally:
        app.release()
        _settings.set_property("gtk-enable-animations", _animations)
    if "error" in box:
        raise box["error"]
    assert len(results) == len(steps), (
        f"only {len(results)} of {len(steps)} steps ran - the main loop ended "
        "early, so the remaining assertions would have been skipped")
    return results


# ---------------------------------------------------------------------------
# the four states
# ---------------------------------------------------------------------------


def _snapshot(w):
    """Everything the four states are read from, as plain values."""
    return {
        "collapsed": bool(w._split.get_collapsed()),
        "show_content": bool(w._split.get_show_content()),
        "sidebar": _bounds(w._split.get_sidebar(), w),
        "chat": _bounds(w._content_view, w),
        "toggle_mapped": bool(w._sidebar_toggle.get_mapped()),
        "drawer_bar_visible": bool(w._sidebar_page._drawer_bar.get_visible()),
        "drawer_close_mapped": bool(w._sidebar_page._drawer_close.get_mapped()),
    }


def test_the_panels_open_beside_the_chat_and_come_back(monkeypatch):
    """The whole round trip on a wide window: hide, show, hide, show.

    Both directions, because a fix that only handles one of them is a fix for the
    other bug. The chat must end up at x=0 and full width when the panels are
    hidden - that is what "hidden" means, and reading `get_collapsed()` alone
    would not tell the drawer from the hidden state.
    """
    def hide(w):
        w.toggle_sidebar()
        return None

    def after_hide(w):
        snap = _snapshot(w)
        assert snap["sidebar"] in (None, (0, 0)) or snap["sidebar"][1] == 0, (
            f"the panels are still {snap['sidebar'][1]}px wide after hiding them")
        assert snap["chat"][0] == 0, (
            f"the chat did not move to the edge: it is at x={snap['chat'][0]}, so "
            "something is still occupying the sidebar's column")
        assert snap["toggle_mapped"], (
            "the window's own toggle is not on screen with the panels hidden - "
            "so there is no way to bring them back from the header")
        return None

    def show(w):
        w.toggle_sidebar()
        return None

    def after_show(w):
        snap = _snapshot(w)
        assert snap["sidebar"] is not None and snap["sidebar"][1] > 0, (
            "the panels did not come back")
        assert snap["chat"][0] == snap["sidebar"][1], (
            "the chat is not sitting beside the panel list")
        assert snap["toggle_mapped"]
        return None

    results = in_a_window([hide, after_hide, show, after_show], monkeypatch)
    assert len(results) == 4


def test_two_presses_return_exactly_where_it_started(monkeypatch):
    """The toggle is a toggle: an even number of presses is a no-op overall.

    Asserted on the snapshot rather than on `get_collapsed()`, because the bug
    this file is about was a state that no single property described.
    """
    def flip(w):
        w.toggle_sidebar()
        return None

    def open_state(w):
        return _snapshot(w)

    def hide(w):
        w.toggle_sidebar()
        return None

    def hidden(w):
        return _snapshot(w)

    def show(w):
        w.toggle_sidebar()
        return None

    def back_to_open(w):
        return _snapshot(w)

    results = in_a_window(
        [open_state, hide, hidden, show, back_to_open], monkeypatch)
    opening, while_hidden, restored = results[0], results[2], results[4]
    assert while_hidden != opening, (
        "one press did not change anything, so the toggle does nothing at all")
    assert restored == opening, (
        "two presses did not return to the opening state - the second press did "
        "not undo the first. Restored "
        f"{ {k: restored[k] for k in restored if restored[k] != opening[k]} } "
        f"against { {k: opening[k] for k in opening if opening[k] != restored[k]} }")


def test_the_panel_list_never_covers_the_way_out_of_it(monkeypatch):
    """The reported bug, stated as an invariant over every reachable state.

    Drives the split view through all four combinations of `collapsed` and
    `show_content` - which is what the toggle and the breakpoint can produce
    between them - and demands that at every one of them either the window's
    toggle is on screen or the panel list carries its own close button that is.

    This is the check that had been missing. The old code satisfied
    `get_collapsed()` at every state, because `collapsed` was the only property
    anyone looked at.
    """
    def initial(w):
        return _snapshot(w)

    def collapse_true_content_true(w):
        w._split.set_collapsed(True)
        w._split.set_show_content(True)
        w._sync_sidebar_toggle()
        return None

    def collapse_true_content_false(w):
        w._split.set_collapsed(True)
        w._split.set_show_content(False)
        w._sync_sidebar_toggle()
        return None

    def collapse_false(w):
        w._split.set_collapsed(False)
        w._sync_sidebar_toggle()
        return None

    def check(w):
        snap = _snapshot(w)
        assert snap["toggle_mapped"] or snap["drawer_close_mapped"], (
            f"neither the header toggle nor the panel list's own close button is "
            f"on screen (collapsed={snap['collapsed']}, "
            f"show_content={snap['show_content']}) - the panel list can be put "
            f"on screen and then nothing on screen can take it off again")
        return snap

    states = in_a_window(
        [initial, check,
         collapse_true_content_true, check,
         collapse_true_content_false, check,
         collapse_false, check],
        monkeypatch)

    # `check` is every second step: 2, 4, 6, 8.
    covered = states[1::2]
    assert len(covered) == 4, f"only {len(covered)} states were checked"
    # And the drawer state really is the one that needs the sidebar's own button,
    # or this test would pass against a build that had none.
    drawer = [s for s in covered
              if s["collapsed"] and not s["show_content"]]
    assert drawer, "the drawer state was never reached, so nothing was proved"
    assert drawer[0]["drawer_close_mapped"], (
        "the panel list is an overlay and its own close button is not on screen")
    # **Not** `assert not drawer[0]["toggle_mapped"]`. That was here to prove
    # the drawer's own button is the only way out of the drawer; measured, it
    # is not the only way - at 1100px the overlay is not allocated over the
    # header, so the window's own toggle is still on screen. The header
    # table records the numbers, and the assertion failed against `window.py`
    # unmodified from HEAD in this same run.


def test_the_drawers_close_button_puts_the_panels_away(monkeypatch):
    """The button in the drawer is a control, so it is pressed.

    Following `test_setup_button_is_reachable.py`: asserting that a widget exists
    with the right icon proves a string was set, not that anything happens. The
    press is `emit("clicked")` on the real button inside the real loop.
    """
    def open_as_drawer(w):
        w._split.set_collapsed(True)
        w._split.set_show_content(False)
        w._sync_sidebar_toggle()
        return None

    def press(w):
        before = _snapshot(w)
        assert before["drawer_close_mapped"], (
            "the panel list's close button is not on screen, so there is nothing "
            "to press")
        w._sidebar_page._drawer_close.emit("clicked")
        return None

    def after(w):
        snap = _snapshot(w)
        assert snap["drawer_close_mapped"] is False, (
            "pressing the close button left the panel list covering the content")
        assert snap["toggle_mapped"], (
            "closing the drawer left the window's toggle unreachable")
        return snap

    results = in_a_window([open_as_drawer, press, after], monkeypatch)
    assert results[-1]["collapsed"] is True and results[-1]["show_content"] is True, (
        "the close button hid the panels into the drawer state instead of taking "
        "them off the screen")


def test_the_drawers_close_button_does_not_change_the_page_you_are_on(monkeypatch):
    """Closing a drawer is not "go back to the conversation".

    The drawer can be sitting over a panel that is already open, so a close
    control wired to the same handler as the "Conversation" row would yank the
    panel away as well - closing a menu should not navigate. Asserted on the
    visible page, not on a private stack length.
    """
    def open_a_panel(w):
        w._show_surface("senses")
        return None

    def open_as_drawer(w):
        w._split.set_collapsed(True)
        w._split.set_show_content(False)
        w._sync_sidebar_toggle()
        return None

    def record_page(w):
        return w._content_view.get_visible_page()

    def press(w):
        w._sidebar_page._drawer_close.emit("clicked")
        return None

    def still_there(w):
        return w._content_view.get_visible_page()

    results = in_a_window(
        [open_a_panel, open_as_drawer, record_page, press, still_there],
        monkeypatch)
    before, after = results[2], results[4]
    assert before is not None, "no panel was open, so this test proved nothing"
    assert before is after, (
        "closing the panel list also popped the panel off the content stack")


def test_the_button_follows_a_change_nothing_in_the_window_made(monkeypatch):
    """The notification is the guard, so it is exercised by writing past it.

    `notify::show-content` is wired for the case where something that is not
    this window changes which pane is on top - a future layout change, or a
    breakpoint that grows one. A guard with no test is a line that looks like it
    does something, so this drives the property *directly*, deliberately skipping
    `_sync_sidebar_toggle`, and demands that the button and the drawer's close bar
    both follow on their own.

    Measured before this existed: removing the `notify::show-content`
    connection left all seven tests here green.
    """
    def column(w):
        w._split.set_collapsed(False)
        return _snapshot(w)

    def become_a_drawer(w):
        # No `_sync_sidebar_toggle`: the point is that the notification does it.
        w._split.set_collapsed(True)
        w._split.set_show_content(False)
        return None

    def after_drawer(w):
        return _snapshot(w)

    def become_hidden(w):
        w._split.set_show_content(True)
        return None

    def after_hidden(w):
        return _snapshot(w)

    results = in_a_window(
        [column, become_a_drawer, after_drawer, become_hidden, after_hidden],
        monkeypatch)
    drawer, hidden = results[2], results[4]

    assert drawer["drawer_bar_visible"] is True, (
        "the panel list became an overlay over the content and nothing revealed "
        "its close bar - so the only way out of it is a control underneath it")
    assert drawer["toggle_mapped"] is False, (
        "sanity: this test needs the overlay to actually cover the header, or "
        "the close bar is being asserted in a state that does not need it")

    assert hidden["drawer_bar_visible"] is False, (
        "the overlay is gone and the close bar is still on screen")
    assert hidden["toggle_mapped"] is True, (
        "the panel list is hidden and the window's own toggle is still not on "
        "screen, so there is no way to bring the panels back from the header")


def test_the_sidebar_has_no_close_button_when_it_is_a_column(monkeypatch):
    """The drawer bar exists only in the drawer state.

    The opposite failure is the duplicate title bar and duplicate back arrow this
    window already shipped once: a second close control beside a reachable
    header toggle is two controls that mean the same thing and can disagree. So
    the bar must be gone in the column layout, where the header's own toggle is
    on screen.
    """
    def column(w):
        w._split.set_collapsed(False)
        w._sync_sidebar_toggle()
        return _snapshot(w)

    results = in_a_window([column], monkeypatch)
    snap = results[0]
    assert snap["toggle_mapped"], (
        "this test needs the header toggle to be reachable, or the drawer bar "
        "being absent proves nothing")
    assert snap["drawer_bar_visible"] is False, (
        "the panel list shows a close bar while it is a column beside the "
        "content, where the header's own toggle is already on screen")


def test_the_window_button_always_agrees_with_what_is_on_screen(monkeypatch):
    """The button's pressed state is derived, never remembered.

    A toggle whose state is a lie is worse than no toggle, and this one was
    pressed while the panels were off screen. Driven through the split view
    directly - the way the breakpoint and an edge-drag do it - rather than
    through the button, so it tests following rather than setting.
    """
    def start(w):
        return _snapshot(w)

    def hide_via_the_view(w):
        w._set_panels_visible(False)
        return None

    def after_hide(w):
        return _snapshot(w)

    def show_as_drawer(w):
        w._split.set_collapsed(True)
        w._split.set_show_content(False)
        w._sync_sidebar_toggle()
        return None

    def after_drawer(w):
        return _snapshot(w)

    results = in_a_window(
        [start, hide_via_the_view, after_hide, show_as_drawer, after_drawer],
        monkeypatch)
    opening, hidden, drawer = results[0], results[2], results[4]

    assert opening["toggle_mapped"], (
        "sanity: the window's toggle must be on screen in the opening state, or "
        "the two states below are not being compared")
    assert not opening["drawer_bar_visible"], (
        "the panel list is a column on a wide window and showed a drawer bar")
    assert hidden["chat"][0] == 0, (
        f"the chat did not take the whole width when the panels were hidden - "
        f"it is at x={hidden['chat'][0]}")
    assert not hidden["drawer_bar_visible"], (
        "the panel list is hidden and still showed a drawer bar")
    assert drawer["drawer_bar_visible"], (
        "the panel list is an overlay over the content and did not reveal its "
        "own close bar - so the only way out is a control underneath it")