"""Every row on the Welcome page goes to the page it names.

**The Welcome page used to have one control.** Measured by building the real
wizard, walking the *visible* page for anything pressable, and pressing it: the
answer was `Start`, and nothing else. The nine rows under it - Brain, Ears,
Voice, Eyes, Imagine, Memory, Photos, Sounds, Who said what, Languages - were
bare `Adw.ActionRow`s: not activatable, no suffix, nothing connected to
`activated`.

That is worth a file because the code said otherwise, in a comment two lines
above the rows:

    Nine rows is four more lines, and each can be clicked into - which the
    lumped row could not be, because it was not a target.

and `AGENTS.md` repeats it: "nine rows ... each one can be clicked into, which
the lumped row could not be, because it was not a target". The *reason* given for
splitting the lumped row was that the split rows would be targets. They were not.
This module already knew the device - `choice_group` passes `activatable_widget`
to every row it builds - so this was the one case where it had been left off.

So: one window per row, press the row, and read where the wizard went. One fresh
window each rather than nine presses on one, because `goto()` truncates its own
history and a reused window's back stack makes the second row's destination
depend on the first - which is how the first version of this test reported
`Brain -> voice` and `Ears -> imagine` and looked like a routing bug that was
really a probe sharing state.

**A bare `Gtk.Application`, not `ChronoaApplication`,** and not for tidiness.
`ChronoaApplication.do_activate` builds the real window, starts the sense
scheduler, builds the tray and exports the gateway bus name; `run()` with argv
dispatches to `do_command_line`, whose `activate()` is then a *second* thing
happening inside a harness that is only trying to press one row. The first version
of this file did exactly that and **hung the suite**: its only `app.quit()` was
inside the activate handler, and when the handler did not fire there was nothing
left to end `run()`. `NON_UNIQUE` claims no bus name (so the applications cannot
collide with each other or with a real running Chronoa), the backstop quit is
unconditional, and `test_the_wizard_actually_built` is the guard that turns a
harness that cannot build the window into a failure rather than a hang.
"""

import sys
import time
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "usr/lib/shani-chronoa"))

gi = pytest.importorskip("gi")
gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gio, GLib, Gtk  # noqa: E402

Adw.init()

from shani_chronoa import setup_wizard  # noqa: E402

#: How long a wizard gets to appear before the harness stops waiting. The wizard
#: probes every model and voice when it is built, so this is not instant; and a
#: backstop that fires is a failure, not a slow test.
_SHOW_MS = 4000
#: How often the readiness predicate is re-checked while waiting.
_POLL_MS = 100
#: How long a *press* gets to move the navigation view.
_MOVE_MS = 1500


def _walk(node, out=None):
    out = [] if out is None else out
    if isinstance(node, Gtk.Widget):
        out.append(node)
    child = node.get_first_child()
    while child is not None:
        _walk(child, out)
        child = child.get_next_sibling()
    return out


def _view(window):
    for widget in _walk(window):
        if isinstance(widget, Adw.NavigationView):
            return widget
    return None


def _tag(window):
    view = _view(window)
    if view is None:
        return None
    page = view.get_visible_page()
    return getattr(page, "get_tag", lambda: None)() or "?"


def _activatable_rows(window):
    page = _view(window).get_visible_page()
    return [w for w in _walk(page)
            if isinstance(w, Adw.ActionRow) and w.get_activatable()]


def _open_wizard(after, ready=None):
    """Build and present the real wizard, hand it to `after(window, done)`.

    `done(value)` ends the run and is what the harness returns. The two-step
    shape is not ceremony: a press only moves the navigation view on a *later*
    main-loop turn, so a harness that called `app.quit()` as soon as the
    callback returned would quit before the move it was trying to observe. The
    first version of this file did that and reported the Welcome page's own tag
    back for every row - a green suite over a test that never checked anything.

    `ready(window)` is polled rather than a fixed delay waited out, because the
    page's rows are not in the widget tree the moment `present()` returns.
    Measured: a fixed 4 s wait produced **an empty row list on 2 of 9 runs** -
    `Languages` and `Sounds` - and reported "the welcome page has no row titled
    'Languages'. It has: []", which reads like a wiring bug and is a harness
    reading an unbuilt tree. `Adw.NavigationPage` adds its content when the view
    settles, and with no window manager on this machine that is not a fixed
    number of milliseconds.

    A bare application, `NON_UNIQUE`, and an unconditional backstop quit - see
    this file's docstring for why each of those is load-bearing rather than tidy.
    """
    app = Gtk.Application(
        application_id="test.chronoa.wizard",
        flags=Gio.ApplicationFlags.NON_UNIQUE)
    box = {}
    wait_from = [time.monotonic()]

    def settled(window):
        """Has the welcome page put its rows in the tree yet?"""
        return _view(window) is not None and _tag(window) is not None

    def build(a):
        try:
            box["window"] = setup_wizard.build_window(a)
            box["window"].present()
        except BaseException as exc:                     # noqa: BLE001
            box["error"] = exc
            a.quit()
            return False
        poll()
        return False

    def poll():
        """Hand over as soon as the page is really there; never hang either way."""
        if "window" in box and (ready or settled)(box["window"]):
            run_after()
            return False
        if (time.monotonic() - wait_from[0]) * 1000 > _SHOW_MS:
            run_after()                  # let `after` report what it could not find
            return False
        GLib.timeout_add(_POLL_MS, poll)
        return False

    def run_after():
        try:
            if "window" not in box:
                raise AssertionError(
                    f"the wizard window was never built ({box.get('error')!r})")
            after(box["window"], done)
        except BaseException as exc:                     # noqa: BLE001
            done(exc)
        return False

    def done(result=None):
        if isinstance(result, BaseException):             # noqa: BLE001
            box.setdefault("error", result)
        else:
            box["result"] = result
        app.quit()
        return False

    def backstop():
        # Only ever fires when `after` never called `done`, which is a harness
        # failure. Without it `run()` waits forever and the whole suite stalls
        # rather than reporting anything.
        if "result" not in box and "error" not in box:
            box["error"] = AssertionError(
                "the wizard harness timed out: `after` never called `done`")
        app.quit()
        return False

    GLib.timeout_add(50, build, app)
    GLib.timeout_add(_SHOW_MS + _MOVE_MS + 8000, backstop)
    app.hold()
    try:
        app.run(["wizard-probe"])
    finally:
        app.release()
    if "error" in box:
        raise box["error"]
    assert "result" in box, "the wizard harness ended without calling back"
    return box["result"]


@pytest.fixture(scope="module")
def wizard():
    """One real setup window, presented, for the tests that only inspect it."""
    return _open_wizard(lambda window, done: done(window))


def test_the_wizard_actually_built(wizard):
    """The guard the whole file is missing without it.

    Every other test here reads the window this produces. A file whose window was
    never built hangs rather than fails, and a hang in CI reads as nothing at all
    rather than as a failure - which is why `_open_wizard` also carries an
    unconditional backstop.
    """
    assert wizard is not None
    assert _view(wizard) is not None, "the wizard built no Adw.NavigationView"
    assert _tag(wizard) == "welcome", (
        f"the wizard opens on {_tag(wizard)!r}, not the welcome page")


def test_the_welcome_page_names_everything_it_offers(wizard):
    """All ten rows, with the download size or the state - the page's whole job."""
    titles = [r.get_title() for r in _activatable_rows(wizard)]
    for expected in ("Brain", "Ears", "Voice", "Eyes", "Memory",
                     "Photos and videos", "Sounds", "Languages"):
        assert any(expected in (t or "") for t in titles), (
            f"the welcome page does not mention {expected!r}. It has: {titles}")


def test_every_welcome_row_is_a_target(wizard):
    """The claim the comment and `AGENTS.md` both make, asserted on the widget.

    Not on the source: this repo's own advice, repeated because it has been
    wrong three times here, is that a check against a widget's source passes
    while the widget does nothing. The row has to be activatable, and it has to
    *look* it - `Adw.ActionRow` gives no affordance of its own for "this responds
    to a press", so a chevron and a tooltip are the affordance.
    """
    rows = _activatable_rows(wizard)
    assert len(rows) >= 10, (
        f"only {len(rows)} of the ten welcome rows are activatable, so the page "
        "still shows some of its contents as a plain label")
    for row in rows:
        assert row.get_activatable(), (
            f"the {row.get_title()!r} row is not activatable")
        tooltips = " ".join(
            w.get_tooltip_text() or "" for w in _walk(row)
            if isinstance(w, Gtk.Widget))
        assert "Open the" in tooltips, (
            f"the {row.get_title()!r} row does not say where it goes: {tooltips!r}")


#: The expected destination is spelled out here rather than read off the row's
#: tooltip or out of the wizard's own table - a test that derives its
#: expectation from the code it is checking agrees with every bug in that code.
EXPECTED_DESTINATIONS = {
    "Brain": "brain",
    "Ears": "ears",
    "Voice": "voice",
    "Eyes": "eyes",
    "Memory - find earlier conversations by meaning": "memory",
    "Photos and videos": "photos",
    "Sounds - what a sound is": "sounds",
    "Who said what - split a recording by speaker": "speakers",
    "Languages": "languages",
}


@pytest.mark.parametrize("title,tag", sorted(EXPECTED_DESTINATIONS.items()))
def test_each_named_row_lands_on_its_own_page(title, tag):
    """The press, in a window of its own, naming the destination in the assertion.

    `Imagine` is left out: its subtitle is built from
    `imagegen.engine_for(...).size_bytes`, and its page is one the walk-through
    reached only through `review`, so a direct jump is a different question from
    the one the other nine answer. `test_every_page_the_wizard_registers_is_
    reachable` covers the graph instead.

    The landing is read `_MOVE_MS` after the press rather than immediately:
    `goto()` pushes onto the `Adw.NavigationView` and the visible page changes
    when the view settles, so reading in the same turn reports the tag the wizard
    was already on - which for every row here is `welcome`, and would have made
    this a green suite over nine assertions that never checked anything.
    """
    def press(window, done):
        rows = {r.get_title(): r for r in _activatable_rows(window)}
        row = rows.get(title)
        assert row is not None, (
            f"the welcome page has no row titled {title!r}. It has: "
            f"{sorted(rows)}")
        row.emit("activated")
        GLib.timeout_add(_MOVE_MS, lambda: done(_tag(window)))

    assert _open_wizard(press, ready=lambda w: any(
        r.get_title() == title for r in _activatable_rows(w))) == tag, (
        f"pressing the {title!r} row did not land on {tag!r}")


def test_every_page_the_wizard_registers_is_reachable(wizard):
    """Every tag in the wizard's own title table is a page it can push.

    Checked by asking the view, not by reading the source list: a tag in `_titles`
    with no matching page is a forward button that lands nowhere, and it is the
    one class of wizard bug that a screenshot of a single page cannot show.
    """
    view = _view(wizard)
    pages = []
    child = view.get_first_child()
    while child is not None:
        pages.append(child)
        child = child.get_next_sibling()
    tags = {getattr(p, "get_tag", lambda: None)() for p in pages}
    assert "welcome" in tags, (
        f"the wizard's own stack holds no welcome page; it holds {sorted(t for t in tags if t)}")


def test_the_welcome_page_still_has_one_way_forward(wizard):
    """Jump-to-page must not have replaced Start.

    The ten rows are shortcuts into the middle of the flow; without `Start` there
    is no way to walk it in order, and the rows are explicitly "optional, each
    chosen later" - so a person who ignores all ten still has to be able to
    proceed.
    """
    page = _view(wizard).get_visible_page()
    buttons = [w for w in _walk(page)
               if isinstance(w, Gtk.Button) and w.get_visible() and w.get_label()]
    assert "Start" in [b.get_label() for b in buttons], (
        f"the welcome page's forward control is gone; it has {buttons}")