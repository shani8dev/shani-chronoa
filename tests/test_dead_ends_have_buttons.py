"""Every "you need to turn something on" has a button, and every button acts.

The most-cited dead end in this app's history: five panels said where a switch
was - "Grant it in Settings, under Privacy", "turn the key on in Settings", "Ask
Chronoa to arm one", "the setup wizard's page installs it", "not installed" -
without providing a way to reach any of them. A person who had just been told
exactly what was wrong had to go and find the switch, and there was nothing on
screen that said where.

So each of those is now a button. **This file presses every one of them**, in a
real widget tree, and asserts the press reaches the real route - `pages.show` for
the settings targets and the application's `setup` action for the wizard. Finding
a button with the right label is not the same as a button that does anything, and
the orb in this repo is the standing example of that difference: a `Gtk.Button`
with nothing connected, which read as complete and did nothing.

The buttons are reached by walking the built panel rather than by asking a module
for its constant, because the claim is about what a person can click.
"""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "usr/lib/shani-chronoa"))

gi = pytest.importorskip("gi")
gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gtk  # noqa: E402

Adw.init()

from shani_chronoa import pages  # noqa: E402
from shani_chronoa.gui.surfaces import calendar, devices, model, triggers  # noqa: E402
from shani_chronoa.gui.surfaces import voice as voice_surface  # noqa: E402


class _StubConfig:
    """A config that answers False to every permission, so gates shut."""

    def __init__(self, **values):
        self._values = values

    def get(self, key, default=""):
        return str(self._values.get(key, default))

    def get_bool(self, key, default=False):
        return bool(self._values.get(key, default))

    def get_double(self, key, default=0.0):
        return float(self._values.get(key, default))

    privacy_mode = True
    wake_word_enabled = False
    whisper_model = ""
    stt_backend = "whisper"
    language = "en"


class _App:
    def __init__(self, **values):
        self.config = _StubConfig(**values)
        self.window = None
        self.tts = None
        self.stt = None
        self.hardware = None
        self.tool_tracker = None
        self.percept_store = None
        #: Filled by `press()`; the stand-in for the app's own GAction.
        self.activated: list = []

    def activate_action(self, name, arg):
        self.activated.append(name)


def _walk(node, out=None):
    out = [] if out is None else out
    if isinstance(node, Gtk.Widget):
        out.append(node)
    child = node.get_first_child()
    while child is not None:
        _walk(child, out)
        child = child.get_next_sibling()
    return out


def _buttons(page, label):
    """Every button on `page` carrying exactly `label`."""
    return [w for w in _walk(page)
            if isinstance(w, Gtk.Button) and w.get_label() == label]


def press(page, label, routes):
    """Press the one button with `label`, recording what the press reached.

    `routes` is the list the press appends to. Returns the button so the caller
    can assert it was really there, and raises if there is not exactly one -
    "zero" and "two" are both failures and they mean different things.
    """
    found = _buttons(page, label)
    assert len(found) == 1, (
        f"expected exactly one {label!r} button, found {len(found)}. "
        + ("The dead end is still a sentence." if not found else
           "Two buttons with the same label cannot both be the one to press."))
    found[0].emit("clicked")
    return found[0]


@pytest.fixture
def shown(monkeypatch):
    """Record every `pages.show` instead of opening a window."""
    reached: list = []
    monkeypatch.setattr(pages, "show",
                        lambda target, application=None, config=None:
                        reached.append(target) or True)
    return reached


# ---------------------------------------------------------------------------
# the four settings routes
# ---------------------------------------------------------------------------


def test_the_refused_phone_gate_offers_the_privacy_switch(shown):
    """`devices` - "Grant it in Settings, under Privacy" becomes a button.

    The refused gate is the one that can be undone from inside the app, so it is
    the one that gets a button. Driven by pinning `_permission` rather than by
    hoping the machine's phone state is convenient - the panel's answer differs on
    a machine with GSConnect, and the assertion must not.
    """
    original = devices._permission
    devices._permission = lambda cfg: devices.State(devices.STATE_REFUSED)
    try:
        page = devices.build(_App())
    finally:
        devices._permission = original

    press(page, "Open Privacy settings", shown)
    assert shown == ["settings:privacy"], (
        f"the button reached {shown!r}, not the privacy page")


def test_the_calendar_consent_gate_offers_the_senses_switch(shown, monkeypatch):
    """`calendar` - "turn the key on in Settings" becomes a button.

    `_permitted` is pinned shut for the same reason: on a machine where the
    consent key happens to be on, this panel has no gate and no button, and the
    test would pass without ever building the thing it is about.
    """
    original = calendar._permitted
    monkeypatch.setattr(calendar, "_permitted",
                        lambda app: (False, calendar.CONSENT_DETAIL))
    page = calendar.build(_App())

    assert calendar._permitted is not original, "the gate was not actually shut"
    press(page, "Open Senses settings", shown)
    assert shown == ["settings:senses"], (
        f"the button reached {shown!r}, not the senses page")


def test_the_empty_rule_list_offers_the_tool_activity_gate(shown):
    """`triggers` - "Ask Chronoa to arm one" becomes a route to the gate.

    Arming a rule is still a conversation - that sentence stays true and the
    panel keeps it. What is reachable from here is the switch every armed rule has
    to pass, so the button says what it opens rather than pretending to arm
    anything.
    """
    page = triggers.build(_App(**{"triggers-enabled": False}))
    # An empty store is the precondition; if this machine has armed rules the
    # panel is not showing its empty state and the button is (correctly) hidden.
    if not page.empty:
        pytest.skip("this machine has armed trigger rules, so the empty state "
                    "the button belongs to is not the one being built")
    press(page, "Manage triggers", shown)
    assert shown == ["settings:tool-activity"], (
        f"the button reached {shown!r}, not the tool-activity page")


@pytest.mark.parametrize("state", sorted(devices.HEALTH_SUMMARY))
def test_the_two_places_a_gate_speaks_do_not_say_the_same_thing(state):
    """One clause above, one paragraph below - and never the same words twice.

    **Found by looking at a rendered window, not by a failing test.** The status
    row and the empty state below it are two places saying one thing, and the
    obvious implementation - put `BODY` in both - printed the same four-line
    paragraph twice on the same screen with the route banner wedged between. A
    test that asserts "the panel contains the reason" passes either way, because
    both versions contain it.

    **Asserted on the built widget, not on the table.** The first version compared
    `HEALTH_SUMMARY` against `BODY` and passed with the duplication back: it was
    checking the data, and the duplication was in the call that reads it. Putting
    `state.body` back where the clause goes left every assertion in this file
    green - the fourth time in this repo that a check against the source of a
    widget passed while the widget said the wrong thing. So this reads the row's
    rendered subtitle off the page and compares it with the paragraph actually on
    screen below it.

    The row is read first and the paragraph is the elaboration; a person who reads
    only the row gets the reason and the fix, and a person who reads everything
    gets the detail once.
    """
    original = devices._permission
    devices._permission = lambda cfg: devices.State(state)
    try:
        page = devices.build(_App())
    finally:
        devices._permission = original

    row = _status_row_text(page)
    assert row, f"{state}: no status row was rendered at all"
    body = devices.State(state).body
    assert body not in row, (
        f"{state}: the status row repeats the empty state's paragraph "
        f"verbatim:\n  row:  {row}\n  body: {body}")

    # The row is `headline - clause`, so its word count is the sum of two
    # sentences' worth of English, not one. What has to hold is that it is
    # *materially* shorter than the paragraph it is an introduction to - a fixed
    # budget was wrong twice here (14 failed at 15) and would keep being wrong on
    # a reworded headline. Half the paragraph is the shape of the claim: the row
    # is a summary, and if it is not much shorter than the thing it summarises,
    # both are on screen for no reason.
    row_words, body_words = len(row.split()), len(body.split())
    assert row_words * 2 <= body_words, (
        f"{state}: the status row carries {row_words} words against a paragraph "
        f"of {body_words}. It is read first and the paragraph below is the "
        f"elaboration; a row nearly as long as its paragraph puts the whole "
        f"thing on screen twice.\n  row: {row}")


def _status_row_text(page):
    """The subtitle of the panel's status row - the clause, not the word."""
    for widget in _walk(page):
        classes = widget.get_css_classes() if isinstance(widget, Gtk.Widget) else []
        if "status-row" in classes and hasattr(widget, "get_subtitle"):
            return widget.get_subtitle()
    return ""


def test_a_gate_with_nothing_to_open_gets_no_button(shown, monkeypatch):
    """The other three of `devices`' four states offer nothing.

    **The negative half, and it is the half that matters.** A machine with no
    phone link needs a package installed and a machine with nothing paired needs
    a phone paired; neither is a page in this app. A button on those would open
    Settings and change nothing - which is a *worse* dead end than the sentence
    it replaced, because it looks like a way out.
    """
    for state in (devices.STATE_NO_LINK, devices.STATE_NONE_PAIRED):
        original = devices._permission
        devices._permission = lambda cfg, s=state: devices.State(s)
        try:
            page = devices.build(_App())
        finally:
            devices._permission = original
        # Only *route* buttons are asserted, not any button: the empty state
        # itself legitimately carries an icon button, and demanding the panel
        # hold no buttons at all would be asserting something else entirely.
        labels = {b.get_label() for b in _walk(page) if isinstance(b, Gtk.Button)}
        offered = labels & {label for label, _t in devices.ROUTES.values()}
        assert offered == set(), (
            f"{state!r} offered {sorted(offered)}; there is nothing in this app "
            "that fixes it, so a button here would open an unrelated page")


# ---------------------------------------------------------------------------
# the setup wizard, from three panels
# ---------------------------------------------------------------------------


def test_no_model_offers_the_setup_wizard():
    """`model` - "the setup wizard's page installs it" becomes a button.

    Driven by pinning `_model_name` to report no model, because on a machine with
    a model this panel shows a perfectly healthy status row and no button - so an
    unpinned test would pass without building the state it is about.
    """
    import shani_chronoa.gui.surfaces.model as model_surface

    original = model_surface._model_name
    model_surface._model_name = lambda app, config, hardware: ("", "not chosen")
    try:
        page = model_surface.build(_App())
    finally:
        model_surface._model_name = original

    app = _App()
    original = model_surface._model_name
    model_surface._model_name = lambda a, c, h: ("", "not chosen")
    try:
        page = model_surface.build(app)
    finally:
        model_surface._model_name = original

    press(page, "Set Chronoa up", app.activated)
    assert app.activated == ["setup"], (
        f"the button reached {app.activated!r}, not the setup action")


def test_no_voice_engine_offers_the_setup_wizard(monkeypatch):
    """`voice` - "not installed" becomes a button.

    `_chain` is pinned to empty, which is the honest way to reach this state: the
    real chain on a machine with espeak-ng installed always resolves something,
    and a test that relied on that would pass here and fail on a minimal image.
    """
    import shani_chronoa.gui.surfaces.voice as surface

    monkeypatch.setattr(surface, "_chain", lambda dispatcher, config: [])
    monkeypatch.setattr(surface, "_resolve_choice",
                        lambda dispatcher: (None, ""))
    app = _App()
    page = surface.build(app)

    press(page, "Set Chronoa up", app.activated)
    assert app.activated == ["setup"], (
        f"the button reached {app.activated!r}, not the setup action")


def test_a_working_voice_offers_no_setup_button(monkeypatch):
    """The negative half again: a machine that can speak gets no button.

    A "Set Chronoa up" on a working voice is a button offering to install what is
    already installed.
    """
    import shani_chronoa.gui.surfaces.voice as surface

    monkeypatch.setattr(surface, "_chain", lambda d, c: [])
    monkeypatch.setattr(surface, "_resolve_choice",
                        lambda dispatcher: ("espeak-ng", ""))
    app = _App()
    page = surface.build(app)

    assert _buttons(page, "Set Chronoa up") == [], (
        "the voice panel offered to install a voice on a machine that already "
        "has one")


# ---------------------------------------------------------------------------
# the route itself
# ---------------------------------------------------------------------------


def test_every_settings_target_is_a_page_that_exists():
    """The four targets are real page ids, read from the settings window.

    A button that opens a page id nothing registers would look identical to a
    working one from here - `pages.show` logs and returns False - so the ids are
    checked against the registry the window itself declares.
    """
    import ast
    import inspect

    from shani_chronoa.settings_window.window import SettingsWindow

    # Reading the declaration out of the source, because importing the window
    # needs a live `Gtk.Application` and this is a claim about a table.
    # `inspect.getsourcefile`, not `__file__`: a `Gtk.ApplicationWindow` subclass
    # has no `__file__`, and `inspect.getsource` resolves the module file for us.
    source = inspect.getsource(SettingsWindow)
    tree = ast.parse(source)
    declared = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and getattr(node.func, "attr", "") == "register":
            for arg in node.args:
                if isinstance(arg, ast.List):
                    for element in arg.elts:
                        if isinstance(element, ast.Tuple) and element.elts:
                            first = element.elts[0]
                            if isinstance(first, ast.Constant):
                                declared.add(str(first.value))
    for target in ("settings:privacy", "settings:senses",
                   "settings:tool-activity", "settings:models",
                   "settings:voice"):
        window_id, page_id = target.split(":", 1)
        assert page_id in declared, (
            f"{target!r} is not a page the settings window declares. It has: "
            f"{sorted(declared)}")


def test_a_target_that_does_not_exist_is_reported_not_raised():
    """`common.open_page` never raises out of a signal handler.

    A button whose callback raises takes the panel down with it, so a retired page
    id has to be a logged no-op. Measured rather than assumed: this drives the
    helper at a window id nothing registered.
    """
    from shani_chronoa.gui.surfaces import common

    common.open_page(_App(), "settings:no-such-page")          # must not raise
    common.open_page(_App(), "no-such-window:privacy")         # must not raise
    common.open_page(_App(), "")                               # must not raise
    common.open_setup(None)                                    # must not raise


def test_the_setup_route_is_one_function_shared_by_three_panels():
    """The wizard is reached one way, and it is not three copies of the way.

    `models.py` held the only copy of "open the wizard", in one of the three
    panels that need it; the other two had to import across module boundaries or
    write their own. So all three now call `common.open_setup`, and this asserts
    it - because three panels with three routes to the wizard is precisely the
    arrangement this repo's dead-code section keeps finding.

    **Read from the AST, not from the text.** The first version of this was a
    substring search, and it failed on `models.py`'s own docstring - which quotes
    `activate_action("setup", None)` while explaining that the panel no longer
    calls it. A grep cannot tell a call from a sentence about a call, and this
    file has been bitten by that three times (`test_it_runs_nothing_on_its_own`,
    the animation-selector regex, and this).
    """
    import ast
    import inspect

    from shani_chronoa.gui.surfaces import models

    for module in (models, model, voice_surface):
        tree = ast.parse(inspect.getsource(module))
        calls = [node for node in ast.walk(tree)
                 if isinstance(node, ast.Call)]
        activated_setup = [
            node for node in calls
            if isinstance(node.func, ast.Attribute)
            and node.func.attr == "activate_action"
            and node.args
            and isinstance(node.args[0], ast.Constant)
            and node.args[0].value == "setup"
        ]
        assert not activated_setup, (
            f"{module.__name__} calls activate_action('setup') itself, at line "
            f"{activated_setup[0].lineno}; there must be one implementation of "
            "that, and it is common.open_setup")

        routed = [
            node for node in calls
            if isinstance(node.func, ast.Attribute)
            and node.func.attr == "open_setup"
        ]
        assert routed, (
            f"{module.__name__} does not route through common.open_setup")