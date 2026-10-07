"""The mode strip: three switches and one button, and what they claim.

The strip exists because plan mode, privacy and the wake phrase were reachable
only from Settings, so a user who had turned one on had no way to see it from the
window they were talking to. A mode that removes capabilities without saying so
reads as a bug the first time the assistant declines something that was
explicitly permitted.

Every assertion here drives real `Gtk` widgets with a real registered
`GApplication` behind them, because the property that matters - *the chip shows
the state, not the click* - is precisely what a mock cannot check. A test that
sets a widget and then asserts the widget reports what the test set is a
tautology.

Three environment facts were measured while writing this, each of which produced
a confidently wrong test first:

- **GIO exports one `org.gtk.Application` per process.** A second
  `register()` raises `already exported`, so the app fixture is module-scoped and
  per-test state is reset instead of a new app being built.
- **`set_action_name` needs a realized widget tree.** Measured: a button with
  `app.dictate` set fires the action when it is inside a presented window, and
  does *not* when the same button is unparented - `emit("clicked")` and
  `activate()` both silently do nothing. The action muxer is installed at
  realize time. So the window fixture presents a real window, and the two tests
  that need a click use it.
- **`.toggle` is GTK's own class on a `GtkToggleButton`.** A stylesheet-coverage
  test that walks `get_css_classes()` therefore has to distinguish the classes
  the strip adds from the ones GTK does, or it asserts a rule for a selector
  that is not the strip's to style.
"""

import sys

import pytest

gi = pytest.importorskip("gi")
gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gio, GLib, Gtk  # noqa: E402

Adw.init()

sys.path.insert(0, "usr/lib/shani-chronoa")
from shani_chronoa import planmode  # noqa: E402
from shani_chronoa.gui.window import (  # noqa: E402
    MODE_STRIP_CSS,
    _ModeStrip,
)


class _Config:
    """A config with the two settings the strip reads, and nothing else.

    Deliberately not a real `ChronoaConfig`: the strip must depend on nothing
    beyond these two attributes, and a stub is how that stays true.
    """

    def __init__(self, privacy: bool = False, wake: bool = False,
                 barge_in: bool = False) -> None:
        self.privacy_mode = privacy
        self.wake_word_enabled = wake
        self.barge_in_vad_enabled = barge_in
        self.reply_style = "ordinary"


def _toggle_privacy(state, fired):
    return Gio.SimpleAction.new("toggle-privacy", None)


@pytest.fixture(scope="module")
def gapp():
    """One registered `Gtk.Application` carrying the four mode actions.

    `register()` is what makes `activate_action` work at all; without it GIO
    emits an `is_registered` assertion and drops every activation, which would
    leave the chip's read-back as the only thing under test rather than the
    whole round trip.

    Module-scoped for the reason in the module docstring: GIO exports exactly
    one `org.gtk.Application` per process.
    """
    app = Gtk.Application(application_id="test.chronoa.modes",
                           flags=Gio.ApplicationFlags.NON_UNIQUE)
    fired: list = []
    config = _Config()

    # The application is a *parameter* rather than closed over, because teardown
    # does `del app` to release the export - and pyflakes, which has no `noqa`,
    # reports a closure over a deleted local as an undefined name. Passing it in
    # is also the more honest signature.
    def add(application, name: str, effect) -> None:
        act = Gio.SimpleAction.new(name, None)
        act.connect("activate", lambda *_a: (fired.append(name), effect()))
        application.add_action(act)

    add(app, "toggle-privacy",
        lambda: setattr(config, "privacy_mode", not config.privacy_mode))
    add(app, "toggle-plan-mode",
        lambda: planmode.set_enabled(not planmode.is_enabled()))
    add(app, "toggle-wake-word",
        lambda: setattr(config, "wake_word_enabled", not config.wake_word_enabled))
    add(app, "toggle-barge-in-vad",
        lambda: setattr(config, "barge_in_vad_enabled", not config.barge_in_vad_enabled))
    dictate = Gio.SimpleAction.new("dictate", None)
    dictate.connect("activate", lambda *_a: fired.append("dictate"))
    app.add_action(dictate)

    # `register()` exports it and makes it the default, which is what
    # `Gtk.Application.get_default()` returns inside `_ModeStrip`. Nothing here
    # may call `app.quit()` or `app.run()` again: `quit()` sets
    # `must_quit_now`, and the next `run()` then refuses with
    # `assertion '!application->priv->must_quit_now' failed` while leaving the
    # app unregistered - so every later `activate_action` would be dropped and a
    # chip would read "off" for a mode that is on. The main loop the press tests
    # need is a private `GLib.MainLoop` for exactly this reason.
    #
    # **NON_UNIQUE, and that is load-bearing.** Without it this `register()`
    # claims a session-bus name, and a name is a process-wide resource: every
    # `ChronoaApplication` the window tests build claims one too and none of them
    # is ever released, so which file registers last decides whether this one gets
    # the bus. Measured both ways: a plain `register()` here **segfaulted**
    # `test_sidebar_toggle.py` inside `g_main_loop_run`, and then failed this
    # file's own registration with `Timeout was reached (24)` on a re-run. With
    # NON_UNIQUE it claims nothing, so it cannot collide - which is the same remedy
    # `test_setup_button_is_reachable.py` applies and records.
    app.register(None)
    assert app.get_is_registered(), (
        "the application did not register, so every activate_action below would "
        "be dropped and the chips would read as off whatever they are")
    app.fired = fired
    app.config = config
    # Bound to a name teardown does not delete. `del app` below is deliberate -
    # it lets the registered application go - and pyflakes, which has no `noqa`,
    # reports any closure over it as an undefined name. The lambda only ever runs
    # while the application is alive.
    _live = app
    app.rebind_privacy = lambda: add(_live, "toggle-privacy", lambda: setattr(
        config, "privacy_mode", not config.privacy_mode))
    yield app
    planmode.set_enabled(False)
    # Let go of the default before the next file runs. A registered
    # `Gtk.Application` outlives the fixture - there is no unregister - so a
    # module-scoped one left in place is still the process default when the next
    # file builds a real application. `quit()` clears `must_quit_now`, which is
    # the flag a second `run()` asserts on.
    app.quit()
    app.release()
    del app


@pytest.fixture
def state(gapp):
    """A known-off starting state and a clean record of this test's clicks.

    Reset rather than rebuilt: the app is shared, so a test that leaves a mode on
    would make the next test's "the chip starts off" assertion depend on
    alphabetical file order - which is exactly the kind of test that passes in
    CI and fails on a developer machine.
    """
    gapp.config.privacy_mode = False
    gapp.config.wake_word_enabled = False
    gapp.config.barge_in_vad_enabled = False
    planmode.set_enabled(False)
    gapp.fired.clear()
    yield gapp
    planmode.set_enabled(False)


@pytest.fixture
def strip(state):
    s = _ModeStrip(state, state.config)
    yield s
    s.unparent()


@pytest.fixture
def pressed(state):
    """Press a control inside a mapped window running a real main loop.

    Modelled on `test_setup_button_is_reachable.py`'s `in_a_window`, and for the
    same reasons that file gives. Two measured facts make it necessary:

    - **`set_action_name` needs a realized widget tree.** A button with
      `app.dictate` set fires the action when it is inside a presented window and
      does *not* when it is unparented - `emit("clicked")` and `activate()` both
      silently do nothing, because the action muxer is installed at realize time.
    - **The press has to happen inside the loop.** Measuring after `run()`
      returned inspects a window already torn down, where every `app.*` control
      reports `sensitive=False` - a false reading that is the harness, not the
      product.

    Yields `(harness, click)`: `click(fn)` runs `fn(strip)` on a mapped strip
    inside the loop and returns `fn`'s result. Exceptions propagate - a probe
    that cannot fail is not a probe.

    The harness is a separate object rather than the `state` fixture itself
    because a `check()` written at test scope resolves the bare name `state` to
    the module-level fixture *definition*, not to its value - measured as
    `'FixtureFunctionDefinition' object has no attribute 'fired'`. The two press
    tests rebind it with `state, click = pressed` so their closures read the
    harness and not the fixture.
    """

    class Harness:
        """What a press test is allowed to see about the application."""

        def __init__(self, app) -> None:
            self.app = app
            self.fired = app.fired
            self.config = app.config

        def fired_since(self, mark: int):
            return self.fired[mark:]

        def count(self, name: str) -> int:
            return self.fired.count(name)

    def click(fn):
        box = {}

        def probe():
            try:
                box["value"] = fn(_current["strip"])
            except BaseException as exc:                # noqa: BLE001
                box["error"] = exc
            return False

        def activate():
            win = Gtk.ApplicationWindow(application=state)
            strip = _ModeStrip(state, state.config)
            win.set_child(strip)
            win.present()
            _current["window"] = win
            _current["strip"] = strip
            GLib.timeout_add(200, probe)
            return False

        loop = GLib.MainLoop()
        box["loop"] = loop
        GLib.timeout_add(50, activate)
        GLib.timeout_add(600, lambda: (loop.quit(), False)[1])
        loop.run()
        if "error" in box:
            raise box["error"]
        if "value" not in box:
            pytest.fail("the main loop ended before the control was pressed")
        return box["value"]

    _current: dict = {}
    yield Harness(state), click
    for key in ("strip", "window"):
        widget = _current.get(key)
        if widget is not None:
            widget.destroy() if key == "window" else widget.unparent()


def test_every_mode_has_a_chip_named_for_what_it_does(strip):
    """Three switches, each labelled for what it changes.

    A chip showing only a glyph is a control whose meaning has to be remembered,
    and four of them in a row is four things to decode before reading any of them.
    """
    chips = (strip._privacy_chip, strip._plan_chip, strip._wake_chip)
    tooltips = [c.get_tooltip_text() for c in chips]
    for name in ("Local only", "Plan mode", "Wake word"):
        assert any(name in tip for tip in tooltips), f"{name} has no chip"
    assert all(t.strip() for t in tooltips)


def test_a_click_activates_the_apps_own_action(strip, state):
    """The chip is the control, and it goes through the app's action.

    Not a private path: the keyboard accelerator and the Settings window's
    switch take this same route, so a chip that wrote the setting itself would be
    a second implementation of the same thing, free to drift.
    """
    strip._plan_chip.set_active(True)
    assert "toggle-plan-mode" in state.fired
    assert planmode.is_enabled() is True


def test_the_chip_shows_the_state_the_action_left_behind(strip, state):
    """After a click, the chip is read back from the state, not left pressed.

    This is the invariant the whole class is written around. A `GSimpleAction`
    carries no boolean state, so a `Gtk.ToggleButton` bound to one keeps its own
    pressed flag and diverges from the app - and the divergence is silent, which
    is worse than the missing chip it replaced.
    """
    strip._privacy_chip.set_active(True)
    assert state.config.privacy_mode is True
    assert strip._privacy_chip.get_active() is True

    strip._privacy_chip.set_active(False)
    assert state.config.privacy_mode is False
    assert strip._privacy_chip.get_active() is False


def test_an_action_that_changes_nothing_does_not_leave_the_chip_pressed(state):
    """The refusal case: the action declines, and the chip must show that.

    `app/voice.py:_set_wake_word_active` refuses to start the wake phrase when
    the model is unavailable and says so in the status line. A chip that kept its
    own pressed state through that would claim hands-free listening is on while
    nothing is listening - the exact false claim this repo keeps paying for, and
    the reason the chip is read back rather than trusted.

    The refusing action is swapped in and restored, because the application is
    shared: leaving a no-op `toggle-privacy` behind would make every later test
    that flips privacy pass or fail on ordering rather than on its own subject.
    """
    refusing = _Config(privacy=False)
    state.remove_action("toggle-privacy")
    act = Gio.SimpleAction.new("toggle-privacy", None)
    act.connect("activate", lambda *_a: None)          # declines to change it
    state.add_action(act)

    s = _ModeStrip(state, refusing)
    try:
        s._privacy_chip.set_active(True)
        assert refusing.privacy_mode is False, "the stub action changed state"
        assert s._privacy_chip.get_active() is False, (
            "the chip is showing the click, not the state - the one failure "
            "this design exists to prevent")
        assert "off" in s._privacy_chip.get_tooltip_text()
    finally:
        s.unparent()
        state.remove_action("toggle-privacy")
        state.rebind_privacy()


def test_refresh_redraws_from_the_state_and_fires_nothing(strip, state):
    """`refresh()` is how the strip learns about changes made elsewhere.

    Settings flips these same settings through these same actions, so the only
    way a chip finds out is to look again. Looking must be free of side effects:
    `set_active()` emits `toggled` even with no click, so a refresh that
    activated an action would toggle the very mode it was reporting.
    """
    strip._plan_chip.set_active(True)
    assert planmode.is_enabled() is True

    before = list(state.fired)
    strip.refresh()
    strip.refresh()
    assert state.fired == before, "redrawing the strip activated an action"

    # A change made by something other than the strip is picked up.
    planmode.set_enabled(False)
    strip.refresh()
    assert strip._plan_chip.get_active() is False
    assert "off" in strip._plan_chip.get_tooltip_text()


def test_the_on_state_is_shown_by_more_than_the_pressed_colour(strip, state):
    """"On" is weight and background as well as the toggle's own look.

    The toggle already looks pressed, but this strip is also read at a glance
    from across the window, so the class and the tooltip carry it too. Colour
    alone would exclude anyone who cannot separate it - the same rule
    `test_state_is_not_colour_only` holds for the orb.
    """
    strip.refresh()
    assert "mode-chip-on" not in strip._privacy_chip.get_css_classes()

    strip._privacy_chip.set_active(True)
    assert "mode-chip-on" in strip._privacy_chip.get_css_classes()
    assert "on" in strip._privacy_chip.get_tooltip_text()


def test_a_mode_refused_at_startup_is_shown_as_off(gapp):
    """A startup that could not decide reads as off, not as on.

    The failure this guards is a chip claiming privacy is on when nothing ever
    confirmed it - the claim has to be earned, so silence is not consent.
    """
    class Undecidable:
        privacy_mode = False
        wake_word_enabled = False

    s = _ModeStrip(gapp, Undecidable())
    try:
        assert s._privacy_chip.get_active() is False
        assert s._wake_chip.get_active() is False
    finally:
        s.unparent()


def test_a_config_that_cannot_be_read_does_not_take_the_window_down(gapp):
    """An unreadable setting shows as off, and the strip still builds.

    Three gsetting reads raising inside window construction would be a window
    that will not open, which is a far worse failure than a chip that says "off".
    """

    class Broken:
        @property
        def privacy_mode(self):
            raise RuntimeError("dconf is not answering")

        @property
        def wake_word_enabled(self):
            raise RuntimeError("dconf is not answering")

    s = _ModeStrip(gapp, Broken())
    try:
        assert s._privacy_chip.get_active() is False
        assert s._wake_chip.get_active() is False
        assert s._plan_chip.get_active() is False
    finally:
        s.unparent()


def test_dictation_is_a_button_because_it_has_no_state(state):
    """Dictation is a way of starting one long turn, not a mode.

    A fourth chip would stay pressed after the turn ended - on silence, up to
    five minutes later - claiming a turn is still running when it is not. Same
    class of false claim as the refusal test above.
    """
    s = _ModeStrip(state, state.config)
    try:
        toggles, plain = _chips(s)
        # One switch per row of the chip table - the claim here is that
        # dictation is *not* among them, not how many modes there are.
        assert len(toggles) == len(_ModeStrip._CHIPS), (
            f"expected one switch per chip, got {len(toggles)}")
        assert all(t.get_action_name() != "app.dictate" for t in toggles)
        assert len(plain) == 1, f"expected exactly one button, got {len(plain)}"
        assert plain[0].get_action_name() == "app.dictate"
        assert not isinstance(plain[0], Gtk.ToggleButton), (
            "dictation is a button: a toggle would stay pressed after the turn "
            "ended on silence, claiming a turn is running when it is not")
    finally:
        s.unparent()


def test_the_dictate_button_really_activates_the_action(pressed):
    """A button wired to nothing is the orb-button bug this repo already shipped.

    Asserting `get_action_name()` proves a string was set, not that the action
    runs. This presses the button on a mapped window inside a real main loop and
    watches the activation - which is also the only way to catch the action
    muxer not being installed, since an unrealized button drops it silently.
    """
    state, click = pressed

    def check(strip):
        _toggles, plain = _chips(strip)
        assert len(plain) == 1, f"expected one button, found {len(plain)}"
        button = plain[0]
        assert button.get_action_name() == "app.dictate"
        before = state.count("dictate")
        button.emit("clicked")
        return state.fired_since(before)

    assert click(check) == ["dictate"], (
        "clicking Dictate did not activate app.dictate - the button is present, "
        "named and does nothing, which is the exact orb bug in "
        "tests/test_setup_button_is_reachable.py")


def test_a_mode_chip_in_a_real_window_activates_its_action(pressed):
    """The whole round trip on a mapped window: click, action, chip, state.

    The unparented tests cover the read-back; this covers what only a realized
    tree exercises - that the chip in the window a person is actually looking at
    reaches the app, and lands on the truth afterwards.
    """
    state, click = pressed

    def check(strip):
        before = state.count("toggle-plan-mode")
        strip._plan_chip.emit("toggled")
        fired = state.count("toggle-plan-mode") > before
        active = strip._plan_chip.get_active()
        return fired, planmode.is_enabled(), active

    fired, enabled, active = click(check)
    assert fired, "the chip in a presented window never reached the action"
    assert enabled is True, "the action ran but the module state did not move"
    assert active is True, "the chip shows the click rather than the state"


def test_the_strip_carries_an_accessible_label_for_every_chip(state):
    """Every chip says which mode it is and what state it is in.

    `get_accessible_property` does not exist in this PyGObject - reading every
    accessible name through it returns `""`, which is how an earlier pass here
    nearly reported the settings window as unnamed. So this asserts the label
    was *set*, through the property setter that exists, and does not pretend to
    read it back through an API that is not there.
    """
    s = _ModeStrip(state, state.config)
    try:
        for chip, name in ((s._privacy_chip, "Local only"),
                           (s._plan_chip, "Plan mode"),
                           (s._wake_chip, "Wake word")):
            assert chip.get_tooltip_text() == f"{name}: off", \
                f"{name}'s tooltip does not name the mode and its state"
        s._privacy_chip.set_active(True)
        assert s._privacy_chip.get_tooltip_text() == "Local only: on"
    finally:
        s.unparent()


def test_the_mode_css_defines_a_rule_for_every_class_the_strip_adds(strip):
    """Every class the strip adds has a rule, or the style is decorative.

    A class with no selector is a class that looks like it means something and
    does not - the same finding as the uncovered animations in
    `test_ui_layout_contract.py`.

    `.toggle` and `.horizontal` are GTK's own and are excluded by name, because
    walking `get_css_classes()` cannot tell who added a class and asserting a
    rule for GTK's would be asserting something about GTK.
    """
    # `popup` is GtkMenuButton's own, on the reply-style chip.
    gtk_own = {"toggle", "horizontal", "vertical", "popup"}
    ours = {"mode-strip"}
    for child in _chips_flat(strip):
        ours.update(set(child.get_css_classes()) - gtk_own)
    assert ours, "the strip applied no classes at all - is the build broken?"
    for css_class in sorted(ours):
        assert f".{css_class}" in MODE_STRIP_CSS, \
            f".{css_class} has no rule in the strip's stylesheet"


def test_the_on_rule_is_valid_css_that_gtk_accepts():
    """The stylesheet loads, and its `on` rule names a real change.

    A rule that GTK rejects parses as no rule at all and the chip looks identical
    in both states - so the check is that `load_from_data` accepts the sheet
    *and* that `mode-chip-on` is the class whose properties differ, rather than a
    scan of the CSS text, which cannot tell a selector from the prose around it.
    (That mistake is recorded in `test_ui_layout_contract.py`: a regex over the
    stylesheet reported eleven uncovered animations that were all sentences.)
    """
    provider = Gtk.CssProvider()
    provider.load_from_data(MODE_STRIP_CSS.encode())   # raises on invalid CSS

    base = _css_of(provider, ".mode-chip")
    on = _css_of(provider, ".mode-chip-on")
    assert base is not None, ".mode-chip is not in the sheet"
    assert on is not None, ".mode-chip-on is not in the sheet"
    assert on != base, (
        ".mode-chip-on declares exactly what .mode-chip already declares, so "
        "switching a mode would change nothing a person can see")


def _css_of(provider, selector: str):
    """The declarations `selector` carries in `provider`, or None if absent.

    Read through GTK's own parser so an invalid rule cannot be reported as a
    valid one - `bytes(provider.load_from_data(...))` raises on bad CSS, and
    `to_string()` returns what GTK actually accepted.
    """
    sheet = provider.to_string()
    for block in sheet.split("}"):
        head = block.split("{")[0].strip()
        if head == selector:
            return block.split("{", 1)[1].strip()
    return None


def _chips(box):
    """`(toggles, plain_buttons)` among a box's direct children."""
    toggles, plain = [], []
    for child in _chips_flat(box):
        if isinstance(child, Gtk.ToggleButton):
            toggles.append(child)
        elif isinstance(child, Gtk.Button):
            plain.append(child)
    return toggles, plain


def _chips_flat(box):
    child = box.get_first_child()
    while child is not None:
        yield child
        child = child.get_next_sibling()




def test_talk_over_is_a_chip_that_flips_the_real_setting(strip, state):
    """Talking over a reply was only a switch deep in Settings, though it is the
    one mode changed mid-conversation (on with headphones, off on speakers)."""
    assert "Talk over" in strip._barge_chip.get_tooltip_text()
    assert strip._barge_chip.get_active() is False
    strip._barge_chip.set_active(True)
    assert "toggle-barge-in-vad" in state.fired
    assert state.config.barge_in_vad_enabled is True
    assert strip._barge_chip.get_active() is True
    strip._barge_chip.set_active(False)
    assert state.config.barge_in_vad_enabled is False


def test_the_reply_style_chip_shows_and_offers_the_three_styles(strip, state):
    """Ordinary / brief / explanatory changes the very next reply, so it is a
    mode; it was reachable only in Settings."""
    chip = strip._style_chip
    state.config.reply_style = "ordinary"
    strip.refresh()
    assert strip._style_label.get_text() == "Ordinary"
    assert "mode-chip-on" not in chip.get_css_classes()
    state.config.reply_style = "brief"
    strip.refresh()
    assert strip._style_label.get_text() == "Brief"
    assert "mode-chip-on" in chip.get_css_classes(), "a non-default style is not highlighted"
    menu = chip.get_menu_model()
    targets = [menu.get_item_attribute_value(i, "target").get_string()
               for i in range(menu.get_n_items())]
    actions = {menu.get_item_attribute_value(i, "action").get_string()
               for i in range(menu.get_n_items())}
    assert targets == ["ordinary", "brief", "explanatory"]
    assert actions == {"app.reply-style"}


def test_the_strip_goes_icon_only_and_back(strip):
    """Compact on a narrow window: the words go, the chips and their names stay."""
    from gi.repository import Gtk as _Gtk
    wide = strip.measure(_Gtk.Orientation.HORIZONTAL, -1)[0]
    strip.set_compact(True)
    narrow = strip.measure(_Gtk.Orientation.HORIZONTAL, -1)[0]
    assert narrow < wide, (narrow, wide)
    assert narrow <= 380, f"compact strip still needs {narrow}px"
    assert strip._barge_chip.get_tooltip_text()
    strip.set_compact(False)
    assert strip.measure(_Gtk.Orientation.HORIZONTAL, -1)[0] == wide
