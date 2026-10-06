"""The setup button is clicked, not merely found in the tree.

`app.setup` was registered in `application.py` and named in the shortcuts
window, with no control anywhere in the UI that reached it. Five panels
dead-ended into "turn it on in Settings" or "the setup wizard's page installs
it", and a first-run user had no route to either.

**Everything here presses the button.** Asserting the class list contains
`"setup"` proves a string was set, not that the wizard opens - and the orb is
this repo's own documented example of that gap: a `Gtk.Button` with no
`set_action_name` and no `clicked` handler, which read as complete and did
nothing. So the press is `emit("clicked")` on the real button, found by walking
the real window, inside a real main loop, on the real `ChronoaApplication`, and
the assertion is on `_open_setup` being called.

Three things about the press, all measured here rather than assumed, and each
one a way this file would otherwise have passed without pressing anything:

- **`Gtk.Widget.activate()` does not press a button.** On this GTK (4.14) it
  returns `True` and dispatches nothing. Verified on a bare `Gtk.Button` with no
  Chronoa in the process at all: `activate()` -> no call, `emit("activate")` ->
  no call, `emit("clicked")` -> the action runs. A test written with
  `activate()` would have asserted on a `True` and proved nothing at all.
  `test_activate_is_not_a_press_on_this_gtk` keeps that claim honest.
- **The press has to happen inside the main loop.** Reading `sensitive` or
  pressing after `app.run()` returned measures a window that has already been
  torn down, where *every* `app.*` button in the product - `open-settings`,
  `toggle-listening`, all of them - reports `sensitive=False`. A false reading
  there is the harness, not the product, and it is why the first version of
  this file would have failed on a button that works.
- **`NON_UNIQUE` is required, and the failure it prevents is a skip.** A
  `Gtk.Application` claims a D-Bus name from its id. When a second run of this
  suite already owns that name - which is what overlapping runs produce, and
  this repo's AGENTS.md warns about - `run()` forwards the activation to the
  owner and returns immediately, so the window is never built and every test
  here would have skipped. Measured with three concurrent suites live: the
  default construction produced no window, `NON_UNIQUE` produced one.

`test_the_window_was_built` is the guard for all of it: a file that skips
because a stranger took its bus name reads as coverage of a window nobody built.
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


#: The action the button must reach. Named here as well as in `application.py`
#: so a rename on either side fails a test instead of producing a dead button.
SETUP_ACTION = "app.setup"

#: How long the loop is given to activate, then to run the caller's function.
#: Short enough that twenty-odd tests do not add up to minutes, long enough
#: that the window is mapped before it is asked anything.
_ACTIVATE_MS = 150
_PROBE_MS = 500


def _walk(node, out=None):
    out = [] if out is None else out
    if isinstance(node, Gtk.Widget):
        out.append(node)
    child = node.get_first_child()
    while child is not None:
        _walk(child, out)
        child = child.get_next_sibling()
    return out


def _buttons(window):
    return [n for n in _walk(window) if isinstance(n, Gtk.Button)]


def in_a_window(fn, monkeypatch=None):
    """Build the real window, run `fn(window)` inside the main loop, return it.

    `fn` is handed the live window and its return value comes back out, so every
    assertion in this file is made against a mapped, sensitive, real window.
    Anything raised by `fn` propagates: a test that cannot fail is the failure
    this repo keeps documenting, and swallowing the exception into a result dict
    would be that again one level down.

    `monkeypatch` is applied to the *class* before the application is built, so
    a stub on `_open_setup` is in place before the action that calls it is
    wired - the alternative is a wizard window opening during the suite.
    """
    if monkeypatch is not None:
        monkeypatch.setattr(ChronoaApplication, "_open_setup",
                            lambda self, *_a: None, raising=True)

    # A **real** `ChronoaApplication`, constructed normally.
    #
    # The first version built one with `__new__` and called
    # `Gtk.Application.__init__` by hand, to avoid the real `do_activate` and the
    # component startup behind it. That is the stand-in-fake mistake this repo
    # documents: `do_activate` then ran against an object with no
    # `sense_scheduler`, no `wakeword` and no `_model_override`, and raised
    # `AttributeError` three times over - so the window this file exists to test
    # was never built, and three tests failed on a harness defect while eight
    # passed. The startup is a few hundred milliseconds of probing for binaries
    # that are not installed, which is also the honest thing to measure against:
    # a window built by the product's own path, on a machine with no model.
    app = ChronoaApplication()
    box = {}
    GLib.timeout_add(_ACTIVATE_MS, lambda: (app.activate(), False)[1])
    GLib.timeout_add(_ACTIVATE_MS + _PROBE_MS, lambda: _probe(app, fn, box))
    GLib.timeout_add(_ACTIVATE_MS + _PROBE_MS + 400,
                     lambda: (app.quit(), False)[1])
    app.hold()
    try:
        app.run(["setupbutton-probe"])
    finally:
        app.release()
    if "error" in box:
        raise box["error"]
    if "value" not in box:
        pytest.fail("the main loop ended before the window was probed")
    return box["value"]


def a_activate(app):
    app.activate()


def _probe(app, fn, box):
    try:
        box["value"] = fn(app.window)
    except BaseException as exc:                   # noqa: BLE001
        box["error"] = exc
    return False


# ---------------------------------------------------------------------------
# the window and the button
# ---------------------------------------------------------------------------


def test_the_window_was_built(monkeypatch):
    """The guard the whole file is missing without it."""
    window = in_a_window(lambda w: w, monkeypatch)
    assert window is not None, (
        "the main window did not build, so every assertion here would be about "
        "nothing - with default application flags and a taken D-Bus name, "
        "run() forwards the activation and returns")


def _setup_button(window):
    for node in _buttons(window):
        if node.get_action_name() == SETUP_ACTION:
            return node
    return None


def test_the_setup_button_is_on_screen_and_live(monkeypatch):
    """Sensitive, mapped, parented and labelled - all read while it is live.

    An insensitive button is a control that looks like one, which is the exact
    failure this file exists to rule out.
    """
    def check(window):
        button = _setup_button(window)
        if button is None:
            return None
        return {
            "sensitive": button.get_sensitive(),
            "tooltip": button.get_tooltip_text(),
            "parented": button.get_parent() is not None,
            "mapped": button.get_mapped(),
        }

    found = in_a_window(check, monkeypatch)
    assert found is not None, (
        f"no button carries {SETUP_ACTION}, so the wizard cannot be opened "
        "from the UI at all")
    assert found["parented"], "the setup button has no parent, so it is never drawn"
    assert found["mapped"], "the setup button is not mapped, so it is not on screen"
    assert found["sensitive"], (
        "the setup button is insensitive: a control that looks like one")
    assert found["tooltip"], "an unlabelled icon button is unusable"


@pytest.mark.parametrize("action", [
    "app.setup", "app.quick-ask", "app.open-settings", "app.toggle-listening",
])
def test_every_header_action_has_a_button(monkeypatch, action):
    """The general form of the bug: an action nothing in the window reaches.

    One assertion per named action means the next action added without a control
    is caught here rather than by a person hunting for it.
    """
    names = in_a_window(
        lambda w: {n.get_action_name() for n in _buttons(w)}, monkeypatch)
    assert action in names, (
        f"{action} is registered but no widget reaches it; buttons found: "
        f"{sorted(n for n in names if n)}")


# ---------------------------------------------------------------------------
# the press - the coverage that was missing
# ---------------------------------------------------------------------------


def test_pressing_the_setup_button_opens_the_wizard(monkeypatch):
    """The assertion this file exists for.

    `clicked` is what a pointer press and release dispatches, and on this GTK it
    is the only one of the three candidate signals that runs an action wired
    through `set_action_name` - the measurement is in the module docstring and
    `test_activate_is_not_a_press_on_this_gtk` keeps it honest.
    """
    opened = []
    monkeypatch.setattr(ChronoaApplication, "_open_setup",
                        lambda self, *_a: opened.append(1), raising=True)

    def press(window):
        button = _setup_button(window)
        assert button is not None, "there is no setup button to press"
        before = len(opened)
        button.emit("clicked")
        return len(opened) - before

    fired = in_a_window(press)
    assert fired == 1, (
        "the setup button was pressed and the wizard did not open - the button "
        "is on screen and live but wired to nothing")


def _first_run_cta(window):
    """The in-context "Set Chronoa up" button in the composer column.

    Found by its `pill` class, which the header's flat button does not carry, so
    the two setup controls cannot be confused for one another here.
    """
    for node in _buttons(window):
        if "pill" in node.get_css_classes():
            return node
    return None


def test_the_first_run_cta_reaches_the_wizard_and_the_header_button_stays(monkeypatch):
    """The button on the screen that says "no model" opens the wizard too.

    `set_can_answer` puts a second setup control on the composer column, because
    the whole point of it is that "no model" used to be stated in a tooltip on a
    label with nothing to click. It activates `app.setup` through a handler
    rather than `set_action_name`, so the header's button stays the only widget
    that *declares* the action - which is what keeps `_setup_button()` above
    unambiguous - and both must still reach the same `_open_setup`.

    Both halves are asserted: that the CTA fires, and that the header button is
    still found and still mapped. A CTA that reached nothing and a header button
    that had been hidden would each pass one of those and are the two ways this
    has actually gone wrong.

    `in_a_window` is called **without** `monkeypatch`, for the reason
    `test_pressing_the_setup_button_opens_the_wizard` above does the same: passing
    it makes `in_a_window` install its own no-op `_open_setup`, replacing the stub
    that records the press. With that mistake this test measured `fired == 0`
    against a button that works, and would have failed for the harness.
    """
    opened = []
    monkeypatch.setattr(ChronoaApplication, "_open_setup",
                        lambda self, *_a: opened.append(1), raising=True)

    def check(window):
        cta = _first_run_cta(window)
        assert cta is not None, (
            "no setup control in the composer column, so 'no model' is still "
            "stated with nothing to click beside it")
        assert not cta.get_visible(), (
            "the CTA is on screen on a machine that can answer - it is meant to "
            "appear only while there is nothing to answer with")
        window.set_can_answer(False, "no model")
        assert cta.get_visible(), "set_can_answer(False) did not reveal the CTA"
        before = len(opened)
        cta.emit("clicked")
        cta_fired = len(opened) - before

        header = _setup_button(window)
        assert header is not None, "the header setup button disappeared"
        assert header.get_mapped(), (
            "the header setup button is hidden while a model is missing; it is "
            "also the route to the wizard on a working machine")
        return cta_fired

    assert in_a_window(check) == 1, (
        "the in-context setup button was pressed and the wizard did not open")


def test_only_the_wizard_builder_is_replaced(monkeypatch):
    """Says which link the press test substitutes, so it cannot drift.

    The application, the action, the action name, the button and the signal are
    all the product's own code. Only `_open_setup` - the method that builds a
    second window - is stubbed, and the press above is what proves the chain up
    to it is real.
    """
    import inspect
    source = inspect.getsource(ChronoaApplication._open_setup)
    assert "setup_wizard" in source, (
        "this no longer opens the setup wizard, so the press test would be "
        "asserting against a method that does something else")
    assert hasattr(ChronoaApplication, "_open_setup")


# ---------------------------------------------------------------------------
# negative controls - without these the file could pass while broken
# ---------------------------------------------------------------------------


def test_a_button_with_no_action_does_not_open_the_wizard(monkeypatch):
    """The control for the press, in the same shape as a real press.

    A second, live, sensitive button with no action behind it, pressed the same
    way in the same window, must do nothing while the real one opens the
    wizard. Without this, "the press opened the wizard" could be the press
    rather than the wiring.

    The first version of this control did `button.set_action_name(None)` on the
    *real* button instead. That is not the same control: removing the action
    name also makes the button insensitive, because GTK derives a button's
    sensitivity from whether its action is enabled - so the "unwired" control it
    pressed was a dead one, and the assertion that it was sensitive failed. A
    control that is not the shape of the thing it controls proves less than it
    looks like it does.
    """
    opened = []
    monkeypatch.setattr(ChronoaApplication, "_open_setup",
                        lambda self, *_a: opened.append(1), raising=True)

    def press_unwired(window):
        # The real button, and a twin pressed the same way.
        real = _setup_button(window)
        assert real is not None
        twin = Gtk.Button(label="not wired to anything")
        twin.set_sensitive(True)
        before = len(opened)
        real.emit("clicked")
        after_real = len(opened) - before
        before = len(opened)
        twin.emit("clicked")
        return {
            "real_opened": after_real,
            "twin_opened": len(opened) - before,
            "twin_sensitive": twin.get_sensitive(),
        }

    result = in_a_window(press_unwired)
    assert result["twin_sensitive"], "the twin is not a live control"
    assert result["real_opened"] == 1, (
        "the real button did not open the wizard, so this file's positive test "
        "is not measuring what it claims")
    assert result["twin_opened"] == 0, (
        "a button with no action opened the wizard, so the press is not caused "
        "by the wiring")


def test_looking_for_an_action_nobody_has_finds_nothing(monkeypatch):
    """The search itself, proved able to fail.

    `_walk` plus the action-name filter is how every test above finds its
    button. If asking for an action nothing carries came back with something,
    the `is not None` in each of them would be a tautology.
    """
    def hunt(window):
        found = [n for n in _buttons(window)
                 if n.get_action_name() == "app.no-such-action"]
        return len(found), _setup_button(window) is not None

    missing, present = in_a_window(hunt, monkeypatch)
    assert missing == 0, "the search matched an action that does not exist"
    assert present, "and it failed to match the one that does"


def test_activate_is_not_a_press_on_this_gtk(monkeypatch):
    """Why this file uses `clicked`, asserted rather than asserted-in-prose.

    If a future GTK made `Gtk.Widget.activate()` dispatch the action, the
    `clicked` tests would keep passing and this file's stated reason for using
    `clicked` would be out of date - so the claim has a test that fails when it
    stops being true. It is also the check that would catch someone "fixing" the
    press tests to use `activate()` and silently asserting on a `True`.
    """
    opened = []
    monkeypatch.setattr(ChronoaApplication, "_open_setup",
                        lambda self, *_a: opened.append(1), raising=True)

    def probe(window):
        button = _setup_button(window)
        assert button is not None
        before = len(opened)
        returned = button.activate()
        return returned, len(opened) - before

    returned, fired = in_a_window(probe)
    assert fired == 0, (
        "Gtk.Widget.activate() now dispatches the action on this GTK - the "
        "clicked-based tests still hold, but the reason this file gives for "
        "using clicked is now wrong and has to be rewritten")
    assert returned is True, (
        "activate() no longer even returns True, so it is not a usable "
        "substitute for a press on any GTK")
