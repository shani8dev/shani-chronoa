"""Typing into a field meant to be hidden - Ubuntu's Myna rule, ported.

`canonical/myna`, the offline dictation tool that ships with this Ubuntu,
specifies for its own injection layer:

> Blocking injection into secure fields (password fields, authentication
> prompts).

Myna can honour that with IBus, which refuses a password box at the protocol
itself. Chronoa has no IBus: its routes are the RemoteDesktop portal, uinput and
XTEST, and **all three are session-wide** - they deliver to whatever the
compositor has focused, not to the window that was asked for. So the question
has to be asked here, of the accessibility tree, immediately before the keys go.

Three things this file exists to hold, each because the first version of the code
got it wrong and only running it said so:

1. **The question is "what is focused, anywhere", not "what is focused in `app`".**
   Measured on this desktop before the correction: asking about the app returned
   "nothing holds focus" for a *running* app and "no accessibility tree" for
   every other one - both the normal state of a working desktop *before* the
   window is raised. The guard refused every call for a reason unrelated to it.
2. **A silent tree is a disclosure, not a lock.** Measured here: with
   `toolkit-accessibility` off, no node on this box reports FOCUSED + EDITABLE,
   so failing closed would refuse every keystroke the driver sends on a machine
   where it otherwise works. The guard says so in the reply instead, and
   `_SECURE_REQUIRE_KEY` is the door for a person who wants it strict.
   **A guard that cannot see anything is not a guard that saw nothing.**
3. **A password box is excluded from the observation, and was excluded by
   accident first.** `get_role_name()` returns `"password text"`
   (`Atspi.Role.PASSWORD_TEXT`); this file's `_actionable` spelled it
   `"password"`, which can never match. Correcting the spelling would have
   *added* password boxes as typing targets, which is the wrong direction.
"""

from __future__ import annotations

import sys

import pytest

sys.path.insert(0, "usr/lib/shani-chronoa")

from shani_chronoa import ui_driver  # noqa: E402


class _StateSet:
    def __init__(self, states):
        self._states = list(states)

    def get_states(self):
        return list(self._states)


class FakeNode:
    """An element with a role, a name, a state set and children."""

    def __init__(self, role="", name="", states=(), children=None):
        self._role, self._name = role, name
        self._set = _StateSet(states)
        self._children = list(children or [])

    def get_role_name(self):
        return self._role

    def get_name(self):
        return self._name

    def get_state_set(self):
        return self._set

    def get_child_count(self):
        return len(self._children)

    def get_child_at_index(self, index):
        return self._children[index] if index < len(self._children) else None


def _states(focused=False, editable=False):
    """The real Atspi state members, so nothing about AT-SPI is faked."""
    import gi
    gi.require_version("Atspi", "2.0")
    from gi.repository import Atspi
    out = []
    if focused:
        out.append(Atspi.StateType.FOCUSED)
    if editable:
        out.append(Atspi.StateType.EDITABLE)
    return out


def _desktop(monkeypatch, *apps) -> None:
    """Point `Atspi.get_desktop` at these fake applications."""
    from gi.repository import Atspi
    monkeypatch.setattr(Atspi, "get_desktop", lambda n=0: list(apps))


# --- the three verdicts are all reachable ------------------------------------
#
# The control that matters most in this file: a function whose three answers
# collapse into two is a guard that cannot tell "safe" from "could not ask".


def test_a_focused_password_box_is_the_only_refusal(monkeypatch) -> None:
    box = FakeNode("password text", "Password", _states(True, True))
    _desktop(monkeypatch, FakeNode("application", "app", children=[box]))
    verdict, reason = ui_driver._secure_field()
    assert verdict == "secure", "a focused password box must be refused"
    assert "password text" in reason, "the refusal must name the role"


def test_a_focused_ordinary_entry_is_clear(monkeypatch) -> None:
    entry = FakeNode("entry", "Search", _states(True, True))
    _desktop(monkeypatch, FakeNode("application", "app", children=[entry]))
    assert ui_driver._secure_field()[0] == "clear"


@pytest.mark.parametrize(
    "role",
    ["entry", "text", "combo box", "search box", "terminal", "table cell"])
def test_no_other_role_reads_as_a_secret(monkeypatch, role) -> None:
    """The over-fix: a refusal list that grew one word too far.

    `_SECURE_ROLES` carries three spellings of a secret box. Every other role on
    a real desktop must stay clear, or the guard refuses typing into a terminal
    or into the search box a person was reaching for. `entry` is the one that
    matters most: it is the role of almost every field a driver wants to type
    into, so a refusal list that reached it would refuse the whole feature.
    """
    node = FakeNode(role, "Whatever", _states(True, True))
    _desktop(monkeypatch, FakeNode("application", "app", children=[node]))
    assert ui_driver._secure_field()[0] == "clear", role


def test_a_desktop_with_nothing_focused_is_unknown(monkeypatch) -> None:
    entry = FakeNode("entry", "Search", _states(False, False))
    _desktop(monkeypatch, FakeNode("application", "app", children=[entry]))
    assert ui_driver._secure_field()[0] == "unknown"


def test_an_empty_desktop_is_unknown(monkeypatch) -> None:
    _desktop(monkeypatch)
    assert ui_driver._secure_field()[0] == "unknown"


def test_a_focused_button_is_not_the_field_being_typed_into(monkeypatch) -> None:
    """Focus alone is not enough, and this is the half that proves it.

    A window whose focused element is a *button* is not a window about to have
    text typed into it - and reading it as "clear" would be the guard claiming
    it checked a field when it checked a button. Neither FOCUSED nor EDITABLE
    alone is the question; both are.
    """
    button = FakeNode("push button", "Save", _states(True, False))
    _desktop(monkeypatch, FakeNode("application", "app", children=[button]))
    assert ui_driver._secure_field()[0] == "unknown", (
        "a focused button says nothing about where text would land")


def test_only_the_field_that_holds_focus_counts(monkeypatch) -> None:
    """An unfocused password box on the same screen is not the danger.

    The refusal is about *where the keys go*, and the keys go to whatever holds
    focus. A login form sitting open in the background - the commonest case -
    must not be read as the target while an ordinary entry in the same window
    has the caret.
    """
    background = FakeNode("password text", "Password", _states(False, True))
    focused = FakeNode("entry", "Search", _states(True, True))
    _desktop(monkeypatch,
             FakeNode("application", "app", children=[background, focused]))
    assert ui_driver._secure_field()[0] == "clear"


def test_the_question_is_not_about_which_app(monkeypatch) -> None:
    """The question is "what holds focus", and asking it of one app was the bug.

    Every route here is session-wide: the portal, uinput and XTEST deliver to
    whatever the compositor has focused, not to the window that was named. So
    asking "what is focused in `app`" is asking a question about the *wrong*
    thing - the app may not be the focused one at all, which is the normal state
    before a window is raised. Measured on this box before the correction: a
    running app read "nothing holds focus" and every other one read "no
    accessibility tree", so the guard refused every call for a reason that had
    nothing to do with the call.

    The signature is the witness, because the bug *was* the signature: the
    parameter existed and was used to filter the desktop's applications.
    """
    import inspect
    params = inspect.signature(ui_driver._secure_field).parameters
    assert list(params) == [], (
        f"_secure_field takes {list(params)}; the routes are session-wide, so "
        f"the focused field is not a property of the app that was named")


# --- what each verdict does to a real call -----------------------------------


def _portal_only(monkeypatch, verdict, reason, require=False):
    """Drive `portal_type` with one verdict, and record what it sent."""
    sent = []
    monkeypatch.setattr(ui_driver, "_secure_field",
                        lambda: (verdict, reason))
    monkeypatch.setattr(ui_driver, "_secure_require", lambda: require)

    class _Remote:
        def type_text(self, text):
            sent.append(text)

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    monkeypatch.setattr(ui_driver, "_portal_input", lambda: _Remote())
    return sent


def test_a_secret_box_stops_the_keys_before_any_are_sent(monkeypatch) -> None:
    sent = _portal_only(monkeypatch, "secure", "the field holding focus is a password text")
    with pytest.raises(ui_driver.GuiUnavailable) as caught:
        ui_driver.portal_type("slack", "hunter2")
    assert sent == [], "not one keystroke may leave when the field is a secret"
    assert "password text" in str(caught.value)


def test_no_switch_lifts_the_refusal(monkeypatch) -> None:
    """A password box is a password box on every machine, so there is no key.

    The witness is every consent granted at once - `_comms_override` true and
    `_secure_require` true - because a refusal that only holds while nothing is
    consented to is a refusal that has not been tested against its own
    exception. `_comms_override` is here because it is the switch next door and
    it defaults to **on** (measured, the schema's `comms-typing-enabled`), so
    "no switch" has to survive the one that is already flipped.
    """
    sent = _portal_only(monkeypatch, "secure",
                        "the field holding focus is a password text",
                        require=True)
    monkeypatch.setattr(ui_driver, "_comms_override", lambda: True)
    with pytest.raises(ui_driver.GuiUnavailable) as caught:
        ui_driver.portal_type("slack", "hunter2")
    assert sent == [], "not one keystroke may leave whatever is consented to"
    assert "no switch makes that safe" in str(caught.value), (
        "the refusal must say there is no switch, not name a key to turn")


def test_a_clear_field_types_in_silence(monkeypatch) -> None:
    sent = _portal_only(monkeypatch, "clear", "")
    reply = ui_driver.portal_type("slack", "hello")
    assert sent == ["hello"], "the text must reach the app"
    assert "secret box" not in reply, "nothing to disclose when it was checked"


def test_a_silent_tree_types_and_says_so(monkeypatch) -> None:
    """The measured case on this box: the guard cannot see, so it discloses."""
    sent = _portal_only(monkeypatch, "unknown",
                        "nothing on this desktop reports a focused text field")
    reply = ui_driver.portal_type("slack", "hello")
    assert sent == ["hello"], "a silent tree must not stop the driver"
    assert "focused text field" in reply, "the gap must be visible in the reply"


def test_the_require_key_turns_the_silence_into_a_refusal(monkeypatch) -> None:
    sent = _portal_only(monkeypatch, "unknown",
                        "nothing on this desktop reports a focused text field",
                        require=True)
    with pytest.raises(ui_driver.GuiUnavailable) as caught:
        ui_driver.portal_type("slack", "hello")
    assert sent == []
    said = str(caught.value)
    assert "Only type into a known field" in said, (
        "the refusal must name the row a person can find, not a raw key")
    assert ui_driver._SECURE_REQUIRE_KEY not in said, (
        "a refusal that names the key sends whoever reads it hunting for a "
        "switch no screen spells that way")


def test_the_switch_that_turns_the_silence_into_a_refusal_is_reachable() -> None:
    """A key the code reads but the schema does not carry takes its Python
    default **forever** - the `heard-sound` / `calendar-write-enabled` class,
    measured 47 times across 20 files in this repository.

    So the key is pinned against the shipped schema, its default is pinned
    (off, because a desktop with no accessibility bus publishes nothing and a
    guard that cannot see is a guard that has made the feature unusable), and
    the label the refusal quotes is read out of the same table the Settings row
    is built beside - a refusal naming a row that does not exist is the dead
    end this whole file's sibling change closed.
    """
    import gi

    gi.require_version("Gtk", "4.0")
    from gi.repository import Gio

    import shani_chronoa.config as config_mod

    source = Gio.SettingsSchemaSource.get_default()
    schema = source.lookup(config_mod.SCHEMA_ID, True)
    key = schema.get_key(ui_driver._SECURE_REQUIRE_KEY)
    assert key is not None, (
        f"{ui_driver._SECURE_REQUIRE_KEY} is read by the driver and is not in "
        f"the schema, so it takes its Python default for ever")
    assert key.get_default_value().get_boolean() is False, (
        "on by default would refuse every keystroke on a desktop whose "
        "accessibility bus publishes nothing")

    from shani_chronoa import capabilities

    label = capabilities.gate_label(ui_driver._SECURE_REQUIRE_KEY)
    assert label and label != ui_driver._SECURE_REQUIRE_KEY, label
    assert ui_driver._secure_require_label() == label, (
        "the refusal and the Help window must name the same row")


def test_the_refusal_falls_back_to_the_key_when_the_table_cannot_be_read(
        monkeypatch) -> None:
    """Both directions, because either alone is satisfiable by a refusal that
    names both things at once."""
    assert ui_driver._secure_require_label() != ui_driver._SECURE_REQUIRE_KEY

    import shani_chronoa.capabilities as cap

    def _boom(_key):
        raise RuntimeError("no table here")

    monkeypatch.setattr(cap, "gate_label", _boom)
    assert ui_driver._secure_require_label() == ui_driver._SECURE_REQUIRE_KEY


def test_an_unreadable_config_is_not_a_stricter_one() -> None:
    """`_secure_require` fails open: demanding more must not be imposed by a
    config that cannot be read, or a broken schema would stop the driver."""
    class Broken:
        def get_bool(self, key, default=False):
            raise RuntimeError("no settings backend")
    import shani_chronoa.config as config_mod
    original = config_mod.ChronoaConfig
    config_mod.ChronoaConfig = Broken
    try:
        assert ui_driver._secure_require() is False
    finally:
        config_mod.ChronoaConfig = original


# --- a chord is a string too --------------------------------------------------
#
# `key()` is a second entry point with its own two routes, and neither had a
# witness. A chord can paste into a password box as readily as a string can type
# into one, and both routes are session-wide - so a guard that covered only
# `type_text` would leave the whole chord path uncovered while every test in
# this file stayed green.


def _no_xtest(monkeypatch, sent):
    """Everything `key()` does around the guard, stubbed; `sent` records keys."""
    monkeypatch.setattr(ui_driver, "_require_xtest", lambda *a, **k: None)
    monkeypatch.setattr(ui_driver, "_which", lambda *a, **k: "xdotool")
    monkeypatch.setattr(ui_driver, "window_box", lambda w: None)
    monkeypatch.setattr(ui_driver, "_cue_announce", lambda app, box: (None, ""))
    monkeypatch.setattr(ui_driver, "_comms_app", lambda app: "")
    monkeypatch.setattr(ui_driver, "_comms_override", lambda: False)
    monkeypatch.setattr(ui_driver, "_secure_require", lambda: False)
    monkeypatch.setattr(ui_driver, "_xdotool",
                        lambda argv, **k: sent.append(argv) or _Rc(0))


class _Rc:
    def __init__(self, code=0):
        self.returncode = code


def test_a_chord_through_the_portal_is_held_to_the_same_rule(monkeypatch) -> None:
    """`portal_key`'s own gate, driven through the real function.

    Stubs `keysym_for` and `_portal_input` rather than the function, because the
    guard being tested is *inside* `portal_key` - stubbing `portal_key` itself is
    the "a test that reaches past the call site to the function behind it" shape
    this file has been bitten by all session.
    """
    from shani_chronoa import portal as portal_mod
    sent = []
    monkeypatch.setattr(ui_driver, "_secure_field",
                        lambda: ("secure", "the field holding focus is a password text"))

    class _Remote:
        def tap(self, ks):
            sent.append(ks)

        def chord(self, ks):
            sent.append(ks)

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    monkeypatch.setattr(portal_mod, "keysym_for", lambda t: 0x61)
    monkeypatch.setattr(ui_driver, "_portal_input", lambda: _Remote())
    with pytest.raises(ui_driver.GuiUnavailable):
        ui_driver.portal_key("slack", "ctrl-v")
    assert sent == [], "a chord must not reach a password box either"


def test_a_chord_on_the_xtest_route_is_guarded_too(monkeypatch) -> None:
    """The XTEST route raises the window first, then asks - and asks.

    Order matters here as much as presence: a window that has just been
    activated is the one moment the focused field is knowable, so the question
    placed *before* the raise measured "nothing holds focus" on this box and
    refused every call.
    """
    sent = []
    _no_xtest(monkeypatch, sent)
    order = []
    monkeypatch.setattr(ui_driver, "find_window", lambda *a, **k: "6291460")
    monkeypatch.setattr(ui_driver, "_secure_field",
                        lambda: (order.append("secure"),
                                 ("secure", "the field holding focus is a password text"))[1])
    with pytest.raises(ui_driver.GuiUnavailable):
        ui_driver.key("slack", "Slack", "ctrl-v")
    assert order == ["secure"], "the chord route asks the same question"
    assert [argv for argv in sent if argv[0] == "key"] == [], (
        "no chord may be sent once the field is known to be a secret")


def test_a_chord_onto_a_clear_field_goes_through(monkeypatch) -> None:
    """The other direction, because a guard that only ever refuses is not a guard."""
    sent = []
    _no_xtest(monkeypatch, sent)
    monkeypatch.setattr(ui_driver, "find_window", lambda *a, **k: "6291460")
    monkeypatch.setattr(ui_driver, "_secure_field", lambda: ("clear", ""))
    assert "Sent ctrl-v to slack." == ui_driver.key("slack", "Slack", "ctrl-v")
    assert ["windowactivate", "--sync", "6291460"] in sent, "the raise still happens"
    assert ["key", "ctrl-v"] in sent, "and the chord still goes"


# --- the observation's own exclusion, which was an accident ------------------


def test_a_password_box_is_not_a_typing_target() -> None:
    node = FakeNode("password text", "Password", _states(True, True))
    assert ui_driver._actionable(node) is False, (
        "a password box must not be offered to the automation driver: its "
        "keystrokes are session-wide, so it would open the hole this closes")


def test_an_ordinary_entry_still_is() -> None:
    assert ui_driver._actionable(FakeNode("entry", "Search")) is True


def test_the_spelling_that_matched_nothing_is_not_the_reason() -> None:
    """`_actionable` once spelled the role `"password"`.

    `get_role_name()` returns `"password text"`, so the old line excluded
    password boxes by accident rather than by decision. The exclusion is now
    written where a reader can see it - and pinning the *shape* rather than the
    string is what stops the next reader "correcting" it into a hole.
    """
    assert "password text" not in ("entry", "text", "combo box")
    node = FakeNode("password", "Password", _states(True, True))
    assert ui_driver._actionable(node) is False, (
        "a bare 'password' role must still be excluded")


def test_the_exclusion_is_what_carries_the_rule_not_the_role_tuple() -> None:
    """**The exclusion is currently redundant, and that was measured.**

    `if role == "password text": return False` sits above
    `role in ("entry", "text", "combo box")`, and neither `"password text"` nor
    `"password"` is in that tuple - so removing the exclusion, or correcting it
    to the bare spelling, changes the answer for **every** input. Both are
    equivalent mutants, and the mutation set says so rather than counting them.

    It is kept, because a future edit that added `"password text"` to the tuple
    - which is the one change that would turn the driver into something that
    addresses a login box - would meet no objection at all, and because the line
    is where a reader looks. **What makes it safe rather than decorative is this
    test**: it holds the two halves in step, so the exclusion cannot be deleted
    *and* the role quietly accepted.

    The two directions, because one alone is satisfiable by a pair that have
    simply never been looked at together.
    """
    accepted = ("entry", "text", "combo box")
    assert "password text" not in accepted, (
        "the role is now accepted on its own, so the explicit exclusion above it "
        "is the only thing refusing a password box - check it still is")
    assert "password" not in accepted, (
        "the bare spelling is accepted too, which is the reading `Atspi` "
        "publishes for some toolkits")
    # And the refusal is therefore real, whichever half is doing it.
    for role in ("password text", "password"):
        node = FakeNode(role, "Password", _states(True, True))
        assert ui_driver._actionable(node) is False, role


# --- where the question is asked --------------------------------------------
#
# The first version asked beside the comms guard, before `find_window`, and
# measured "nothing holds focus" for a running app. The call order is the
# property, so the test records it.


def test_the_field_is_asked_after_the_window_is_raised(monkeypatch) -> None:
    """Raise first, then ask: before the raise there is no answer to give."""
    order = []

    def note_focus(window, title=""):
        order.append("focus")
        return ""

    monkeypatch.setattr(ui_driver, "find_window", lambda *a, **k: "6291460")
    monkeypatch.setattr(ui_driver, "_focus_the_window", note_focus)
    monkeypatch.setattr(ui_driver, "_comms_app", lambda app: "")
    monkeypatch.setattr(ui_driver, "_comms_surface", lambda w: "")
    monkeypatch.setattr(ui_driver, "window_box", lambda w: None)
    monkeypatch.setattr(ui_driver, "start_cue", lambda app: (None, ""))
    monkeypatch.setattr(ui_driver, "_stop_cue", lambda cue: None)
    monkeypatch.setattr(ui_driver, "_typing_routes", lambda app: [])
    monkeypatch.setattr(ui_driver, "_secure_field",
                        lambda: (order.append("secure"), ("clear", ""))[1])
    ui_driver.type_text("slack", "Slack", "hello")
    assert order == ["focus", "secure"], (
        "the focused field is only knowable once the window has been raised")


def test_the_comms_refusal_is_still_the_fastest_thing_in_the_function(monkeypatch) -> None:
    """The secure guard must not have displaced the 30-second lesson.

    Ordering, not coverage: `_comms_app` answers without a window, so a chat
    surface is refused without any window search at all - two tests once took
    **exactly 30 seconds each** here, because the search ran first.

    **And the state has to be arranged.** `comms-typing-enabled` defaults to
    **true** in the schema (`org.shani.chronoa.gschema.xml:728`), so under
    `conftest`'s keyfile backend `_comms_override()` answers True and the
    refusal never fires at all. The first version of this test therefore failed
    on a harness where consent had been granted rather than on a harness where
    a guard was slow - a witness that was measuring the default, not the order.
    A test about which guard runs first has to say which consent it assumes.
    """
    monkeypatch.setattr(ui_driver, "_comms_app",
                        lambda app: "'slack' is a communication surface")
    monkeypatch.setattr(ui_driver, "_comms_override", lambda: False)
    monkeypatch.setattr(ui_driver, "find_window",
                        lambda *a, **k: pytest.fail("no window may be looked for"))
    monkeypatch.setattr(ui_driver, "_secure_field",
                        lambda: pytest.fail("the comms refusal must come first"))
    with pytest.raises(ui_driver.GuiUnavailable) as caught:
        ui_driver.type_text("slack", "Slack", "hello")
    assert "communication surface" in str(caught.value), (
        "the comms refusal, not the secure one, is what refused")
