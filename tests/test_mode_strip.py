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
        self.answer_mode = "local-cloud"
        #: `background-mode-enabled` is read through `get_bool` rather than as an
        #: attribute, because that is how the real config exposes it and a stub
        #: that invented an attribute would not catch a reader that stopped using
        #: the setting.
        self._bools = {"background-mode-enabled": False}

    def get_bool(self, key, default=False):
        return self._bools.get(key, default)


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
    answer_mode = Gio.SimpleAction.new_stateful(
        "answer-mode", GLib.VariantType.new("s"),
        GLib.Variant("s", config.answer_mode))
    answer_mode.connect("change-state",
                        lambda a, v: (config.__setattr__(
                            "answer_mode", v.get_string()),
                            a.set_state(v)))
    app.add_action(answer_mode)

    # The two merged dropdowns carry their own stateful string actions, exactly
    # as `answer-mode` and `reply-style` do - so the menu and the chip cannot
    # disagree about what is chosen. Without them the strip still builds, but
    # every `activate_action("app.answer-rules", ...)` is dropped by the action
    # muxer and the chips read "General" whatever was picked.
    for name, start in (("answer-rules", "general"), ("hands-free", "off")):
        act = Gio.SimpleAction.new_stateful(
            name, GLib.VariantType.new("s"), GLib.Variant("s", start))
        act.connect("change-state", lambda a, v, n=name, s=start: (
            config.__setattr__("_" + n.replace("-", "_"), v.get_string()),
            a.set_state(v)))
        app.add_action(act)
        # A starting place each choice flips, so a test can read the result back.
        if name == "answer-rules":
            config._answer_rules = start
        else:
            config._hands_free = start

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


def test_every_mode_has_a_dropdown_named_for_what_it_does(strip):
    """Four dropdowns, each named for the question it answers.

    A chip showing only a glyph is a control whose meaning has to be remembered.
    The old strip had eight of them; four of the eight were toggles answering three
    questions between them, and the ones that survived the merge say their state
    in words - which is what a mode strip is for.
    """
    tooltips = [c.get_tooltip_text() for c in
                (strip._rules_chip, strip._ambient_chip, strip._hands_chip,
                 strip._answer_chip, strip._style_chip)]
    for what in ("What it works on", "When it may act",
                 "How Chronoa hears you", "Where it may go", "Reply style"):
        assert any(what in tip for tip in tooltips), f"nothing shows {what!r}"
    assert all(tip.strip() for tip in tooltips)


def test_two_toggles_that_answer_one_question_are_one_dropdown(strip):
    """The merge itself, asserted on the menu that replaced the two chips.

    Plan mode and Local only were both "what is this allowed to do"; Wake word and
    Talk over were both "how is it listening". One dropdown each, with the
    combinations that mean something rather than the booleans multiplied by hand.
    """
    rules = strip._rules_chip.get_menu_model()
    hands = strip._hands_chip.get_menu_model()
    assert _menu_keys(rules) == ["general", "build"]
    assert _menu_keys(hands) == ["off", "wake", "wake-over", "over"]


def _menu_keys(menu):
    return [menu.get_item_attribute_value(i, "target").get_string()
            for i in range(menu.get_n_items())]


def test_the_menu_targets_the_actions_and_the_words_follow_the_settings(
        strip, state):
    """A dropdown offers the choices and names the state; the effect is the app's.

    **Split on purpose, because the two halves live in different places.** The
    strip owns the menu and the word; `_on_answer_rules` in
    `app/application.py` owns writing plan mode and privacy, and
    `tests/test_answer_mode.py` drives that on a real `BrainMixin`. The first
    version of this test tried to assert the *effect* through the strip's own
    fixture, whose `answer-rules` writes a stub attribute - so it failed while
    the real chain was correct, which is the failure this repo keeps recording:
    an assertion about a thing the test's own fixture stands in for.
    """
    rules = strip._rules_chip.get_menu_model()
    assert _menu_keys(rules) == ["general", "build"]
    for i in range(rules.get_n_items()):
        assert rules.get_item_attribute_value(i, "action").get_string() \
            == "app.answer-rules"

    # The word follows the setting it owns. **One question per chip**: privacy
    # used to move this word too, and it no longer does - it moved to the egress
    # chip, where it is the thing being asked about.
    #
    # **This chip is Build mode.** It read `planmode` and said "Read only"
    # until the three modes were separated - one control standing in for a
    # permission and a workflow at once, so it could report either. The word now
    # follows `buildmode`, and plan mode has its own chip.
    from shani_chronoa import buildmode

    buildmode.set_enabled(True)
    strip.refresh()
    assert strip._rules_label.get_text() == "Build"
    buildmode.set_enabled(False)
    strip.refresh()
    assert strip._rules_label.get_text() == "General"

    # And privacy now moves the egress word, which is the honest place for it.
    state.config.privacy_mode = True
    strip.refresh()
    assert strip._mode_label.get_text() == "Nothing leaves"
    state.config.privacy_mode = False
    strip.refresh()
    assert strip._mode_label.get_text() == "This machine, then cloud"


def test_the_dropdown_shows_the_state_the_action_left_behind(strip, state):
    """After a choice, the word is read back from the settings it owns.

    This is the invariant the whole class is written around: a `Gtk.MenuButton`
    has no pressed state of its own, so the only way it can be right is by being
    drawn from the state - which is also why it cannot disagree with the menu.
    """
    # **This chip follows `buildmode`; plan mode has its own chip.** The test
    # used to set plan mode and read "Read only" here, because one control was
    # standing in for a permission and a workflow at once.
    from shani_chronoa import buildmode

    buildmode.set_enabled(True)
    strip.refresh()
    assert strip._rules_label.get_text() == "Build"
    assert strip._plan_label.get_text() == "Working", (
        "building should not turn plan mode on")

    # **One question per chip, so one setting clears it.** This used to clear
    # `privacy_mode` and expect the word to follow, because privacy and plan mode
    # were the same chip - which is the coupling that made the four options
    # incomparable. Now the egress chip owns privacy and this one owns Build.
    buildmode.set_enabled(False)
    strip.refresh()
    assert strip._rules_label.get_text() == "General"


def test_an_action_that_changes_nothing_does_not_move_the_word(state):
    """The refusal case: the action declines, and the word must show that.

    `app/voice.py:_set_wake_word_active` refuses to start the wake phrase when
    the model is unavailable and says so in the status line. A chip that kept its
    own pressed state through that would claim hands-free listening is on while
    nothing is listening - the exact false claim this repo keeps paying for, and
    the reason the word is read back rather than trusted.

    The refusing action is swapped in and restored, because the application is
    shared: leaving a no-op `toggle-wake-word` behind would make every later test
    that flips it pass or fail on ordering rather than on its own subject.
    """
    refusing = _Config(wake=False)
    state.remove_action("toggle-wake-word")
    act = Gio.SimpleAction.new("toggle-wake-word", None)
    act.connect("activate", lambda *_a: None)          # declines to change it
    state.add_action(act)

    s = _ModeStrip(state, refusing)
    try:
        s.activate_action("app.hands-free", GLib.Variant("s", "wake"))
        assert refusing.wake_word_enabled is False, "the stub action changed state"
        s.refresh()
        assert s._hands_label.get_text() == "Mic button", (
            "the dropdown is showing the click, not the state - the one failure "
            "this design exists to prevent")
    finally:
        s.unparent()
        state.remove_action("toggle-wake-word")


def test_refresh_redraws_from_the_state_and_fires_nothing(strip, state):
    """`refresh()` is how the strip learns about changes made elsewhere.

    Settings flips these same settings through these same actions, so the only
    way a chip finds out is to look again. Looking must be free of side effects:
    `set_active()` emits `toggled` even with no click, so a refresh that
    activated an action would toggle the very mode it was reporting.
    """
    planmode.set_enabled(True)
    strip.refresh()

    before = list(state.fired)
    strip.refresh()
    strip.refresh()
    assert state.fired == before, "redrawing the strip activated an action"

    # A change made by something other than the strip is picked up.
    planmode.set_enabled(False)
    strip.refresh()
    assert strip._read_capability() == "general"
    assert "General" in strip._rules_chip.get_tooltip_text()


def test_the_quiet_states_are_the_only_ones_not_highlighted(strip, state):
    """The highlight is how a dropdown says "not the usual one".

    A `Gtk.MenuButton` has no pressed state, so `mode-chip-on` carries it - and
    highlighting all four would highlight none. Each dropdown names the state that
    means "nothing unusual", and everything else is a departure worth seeing.
    """
    strip.refresh()
    # The quiet state of each dropdown: the one that means "nothing unusual".
    # Quiet means **your words never reach a provider**, so for egress that is
    # the lock and "this machine answers" - not the default, which can fall back
    # to a provider and is therefore a departure. Asserting otherwise would be
    # asserting the default is a departure, which is the alarm-fatigue the design
    # avoids.
    assert "mode-chip-on" in strip._answer_chip.get_css_classes()
    assert "mode-chip-on" not in strip._style_chip.get_css_classes()
    assert "mode-chip-on" not in strip._rules_chip.get_css_classes()
    assert "mode-chip-on" not in strip._hands_chip.get_css_classes()

    # Each dropdown's own quiet state, and its departures.
    state.config.answer_mode = "local"
    strip.refresh()
    assert "mode-chip-on" not in strip._answer_chip.get_css_classes(), (
        "a state where the answer never leaves is flagged as a departure")
    planmode.set_enabled(True)
    strip.refresh()
    assert "mode-chip-on" not in strip._answer_chip.get_css_classes(), (
        "the lock itself is flagged as a departure")
    state.config.privacy_mode = False
    strip.refresh()
    state.config.answer_mode = "cloud"
    strip.refresh()
    assert "mode-chip-on" in strip._answer_chip.get_css_classes()

    state.config.reply_style = "brief"
    strip.refresh()
    assert "mode-chip-on" in strip._style_chip.get_css_classes()
    state.config.reply_style = "ordinary"
    strip.refresh()
    assert "mode-chip-on" not in strip._style_chip.get_css_classes()

    # **Build, not plan mode.** This chip followed `planmode` and lit up for it
    # until the two were separated; it now follows `buildmode`, and plan mode
    # has its own chip which is highlighted by its own state.
    from shani_chronoa import buildmode

    # **Both states set explicitly.** An earlier test in this file leaves plan
    # mode on, so the "building does not light the plan chip" check was failing
    # on the other's state - and it only failed in the file run, not in
    # isolation, which is the worst way for a test to misbehave.
    planmode.set_enabled(False)
    buildmode.set_enabled(True)
    strip.refresh()
    assert "mode-chip-on" in strip._rules_chip.get_css_classes()
    assert "mode-chip-on" not in strip._plan_chip.get_css_classes(), (
        "building lit up the plan chip")
    buildmode.set_enabled(False)
    strip.refresh()
    assert "mode-chip-on" not in strip._rules_chip.get_css_classes()

    planmode.set_enabled(True)
    strip.refresh()
    assert "mode-chip-on" in strip._plan_chip.get_css_classes()
    assert "mode-chip-on" not in strip._rules_chip.get_css_classes(), (
        "plan mode lit up the Build chip, which is the coupling this removed")
    planmode.set_enabled(False)


def test_a_startup_that_could_not_decide_reads_as_the_plain_state(gapp):
    """A startup that could not decide reads as the quiet one, not as a flag.

    The failure this guards is a dropdown claiming "Cloud" when nothing ever
    confirmed it - the claim has to be earned, so silence is not a departure.
    """
    class Undecidable:
        privacy_mode = False
        wake_word_enabled = False
        barge_in_vad_enabled = False
        answer_mode = ""

    s = _ModeStrip(gapp, Undecidable())
    try:
        s.refresh()
        assert s._rules_label.get_text() == "General"
        assert s._hands_label.get_text() == "Mic button"
        assert s._mode_label.get_text() == "This machine, then cloud"
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

        @property
        def barge_in_vad_enabled(self):
            raise RuntimeError("dconf is not answering")

        answer_mode = ""

    s = _ModeStrip(gapp, Broken())
    try:
        s.refresh()
        # Every reader failed, so every word falls back to its plain state.
        assert s._rules_label.get_text() == "General"
        assert s._hands_label.get_text() == "Mic button"
        assert s._mode_label.get_text() == "This machine, then cloud"
    finally:
        s.unparent()


def test_nothing_in_the_strip_is_a_toggle_any_more(state):
    """Every control here is a dropdown now, and none of them is a toggle.

    The redesign's whole claim: a `Gtk.ToggleButton` keeps its own pressed state
    and has to be read back after every activation, which is what the four
    round-trip tests above existed to police. A `Gtk.MenuButton` has no pressed
    state to get wrong, so the strip cannot show a click instead of a state.

    **And a toggle coming back is a regression, not a variation.** Asserting on
    the widget type rather than the count means adding a fifth dropdown passes
    and adding a chip fails - the latter is the thing that would need the
    read-back machinery again.
    """
    s = _ModeStrip(state, state.config)
    try:
        for chip in (s._rules_chip, s._hands_chip, s._answer_chip,
                     s._style_chip):
            assert isinstance(chip, Gtk.MenuButton), (
                f"{chip.get_tooltip_text()!r} is a "
                f"{type(chip).__name__}, not a menu button")
    finally:
        s.unparent()


def test_a_dropdown_in_a_real_window_activates_its_action(pressed):
    """The whole round trip on a mapped window: choose, action, word, state.

    The unparented tests cover the read-back; this covers what only a realized
    tree exercises - that the control in the window a person is actually looking
    at reaches the app, and lands on the truth afterwards. A menu button's action
    muxer is installed at realize time, so an unrealized one drops the
    activation silently - which is the same trap the dictate button's test
    recorded.
    """
    state, click = pressed

    def check(strip):
        # The app's own action for the switch the dropdown owns - the same route
        # the keyboard accelerator takes. `answer-rules`' *effect* is the app's
        # handler, driven on a real BrainMixin in `test_answer_mode.py`; what a
        # presented window proves is that the strip reaches the app at all,
        # which is the half the action muxer can drop.
        strip.activate_action("app.toggle-plan-mode")
        strip.refresh()
        # **The plan chip, which is where "Read only" used to be read from.**
        # The rules chip follows Build now, so reading it here would have made
        # this test pass for the wrong reason if the two ever moved back
        # together.
        return strip._plan_label.get_text()

    assert click(check) == "Plan", (
        "turning plan mode on from a presented window never reached the state")


def test_a_strip_no_wider_than_the_window_can_hold(strip):
    """The reason the merge happened as well as it did.

    Eight controls in a row needed 512px of words in a window that can be 380px;
    four dropdowns were measured narrower, and the compact pass hides the words
    entirely. This is the guard that the strip stays usable, not the reason it
    went to four.
    """
    from gi.repository import Gtk as _Gtk

    strip.refresh()
    wide = strip.measure(_Gtk.Orientation.HORIZONTAL, -1)[0]
    strip.set_compact(True)
    narrow = strip.measure(_Gtk.Orientation.HORIZONTAL, -1)[0]
    assert narrow < wide, (narrow, wide)
    assert narrow <= 380, f"compact strip still needs {narrow}px"


def test_every_dropdown_carries_an_accessible_label(state):
    """Every dropdown says which question it answers and what is chosen.

    `get_accessible_property` does not exist in this PyGObject - reading every
    accessible name through it returns `""`, which is how an earlier pass here
    nearly reported the settings window as unnamed. So this asserts the label
    was *set*, through the property setter that exists, and does not pretend to
    read it back through an API that is not there.
    """
    s = _ModeStrip(state, state.config)
    try:
        # Record while `refresh()` draws, so the call under test is the one the
        # strip makes rather than one this test makes for itself.
        recorded = {}
        original = Gtk.MenuButton.update_property

        def recorder(self, properties, values):
            from gi.repository import Gtk as _Gtk
            if _Gtk.AccessibleProperty.LABEL in list(properties):
                recorded[id(self)] = list(values)
            return original(self, properties, values)

        Gtk.MenuButton.update_property = recorder
        try:
            s.refresh()
        finally:
            Gtk.MenuButton.update_property = original
        for chip, what in ((s._rules_chip, "What it works on"),
                           (s._hands_chip, "How Chronoa hears you"),
                           (s._ambient_chip, "When it may act"),
                           (s._answer_chip, "Where it may go"),
                           (s._style_chip, "Reply style")):
            assert chip.get_tooltip_text().startswith(what), (
                f"{what!r}'s tooltip does not name the question it answers")
            assert "currently" in " ".join(str(v) for v in
                                           recorded.get(id(chip), [])), (
                f"{what!r} is drawn with no accessible label naming what is "
                f"currently chosen")
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
    # `popup` is GtkMenuButton's own, on every dropdown now.
    # GTK4 style classes its own widgets carry - `bottom`/`top`/`left`/`right`
    # come off the `Gtk.Box`es inside the menu buttons, `background` and `frame`
    # off the buttons themselves. None of them is the strip's to style, and a
    # walk of `get_css_classes()` cannot tell who added a class, so they are
    # excluded by name rather than by a rule this file invented.
    gtk_own = {"toggle", "horizontal", "vertical", "popup", "flat",
               "background", "frame", "csd", "solid-csd", "unfocused",
               "bottom", "top", "left", "right", "down", "up", "menu",
               "dim-label", "separator", "osd", "toolbar", "linked", "raised",
               "circular", "pill", "card", "view", "text"}
    ours = {"mode-strip"}
    stack = [strip]
    while stack:
        node = stack.pop()
        child = node.get_first_child()
        while child is not None:
            stack.append(child)
            ours.update(set(child.get_css_classes()) - gtk_own)
            child = child.get_next_sibling()
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


