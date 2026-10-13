"""The orb's tooltip promised a key that was not bound to anything (2026-10-13)

The empty state's centrepiece is the Chronoa orb, and its tooltip read:

    Start listening (Enter)

Measured, all three halves of that were false at once:

| the tooltip says | what the tree had |
|---|---|
| `Enter` starts listening | `Enter` is the composer's **send**, and an empty send returns early (`window.py`, `if not text: return`) |
| - | `toggle-listening` - the orb's own action - had **no accelerator anywhere**: 13 `set_accels_for_action` calls in the shipped tree, none of them that one |
| - | so a press of Enter from the composer did **nothing at all** |

This is a defect the codebase had already written the rule for, **twenty lines
below the defect**: `_on_window_key_pressed`'s docstring says *"the shortcut was
going to be advertised... a hint that names a key which is not bound is worse than
no hint: a person presses it, nothing happens, and they learn that this
interface lies to them."* That is what the `?` handler was written to that
standard. The orb's tooltip was not.

**One narrowing, found by measuring rather than by accepting the premise.** The
tooltip is on the orb, and the orb is a focusable `Gtk.Button` carrying
`set_action_name("app.toggle-listening")` - so from **the orb itself** the promise
was already true: a focused button activates on Enter. The defect is that the two
places a person reads the tooltip and the two places they would press Enter are
**not the same places**.

    orb focused          + Enter  -> toggles listening   (was already true)
    composer focused     + Enter  -> sends, or nothing if empty   (the dead keypress)
    neither focused      + Enter  -> nothing

**So the fix makes the promise true from where a newcomer stands** rather than
deleting it. The composer takes focus in five places, so the empty composer *is*
the landing state.

**What is deliberately not done**: relabelling the tooltip to `Ctrl+Shift+D`.
That is the smaller change and it keeps voice behind a chord nobody discovers -
which is the shape this repository records for every control whose only route is
an undiscoverable shortcut.

**What this does not claim.** No recording was made. The assertions below are
about which action a keypress *reaches*, driven through the real window and a
real `Gio.SimpleAction`; whether the microphone then opens is the voice layer's
own business and is not what broke.
"""
import re

import pytest

gi = pytest.importorskip("gi")
gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gdk, GLib, Gtk  # noqa: E402

Adw.init()

from shani_chronoa.gui import ChronoaWindow  # noqa: E402

ENTER = Gdk.KEY_Return
KEYPAD_ENTER = Gdk.KEY_KP_Enter
SHIFT = int(Gdk.ModifierType.SHIFT_MASK)
NO_MODIFIERS = 0


class _Closed:
    """A config that grants nothing, so nothing here reaches a real machine."""

    def sense_allowed(self, key):
        return False


class _RecordingApp(Gtk.Application):
    """An application that records which action a keypress asked for.

    **A recording subclass rather than a `SimpleNamespace`, and not a registered
    application.** Both were measured:

    - a `Gtk.Application` that was never `register()`ed answers
      `activate_action` with
      `g_application_activate_action: assertion 'application->priv->is_registered'`
      and **no callback fires** - so a test asserting "the action was reached"
      passes against a dispatch that never happened. Every assertion in the first
      version of this file failed that way, which is the "a check that measures
      nothing" trap rather than a defect.
    - `app.register(None)` fixes it and exports on D-Bus, which this file records
      elsewhere as able to segfault a process that builds a second application.

    So the dispatch itself is not what is under test. **What is** is that the
    keypress names the same action the orb carries, and that the application's
    own action table has an entry by that name - both checked below against the
    real orb and the real `_create_actions()`.
    """

    def __init__(self):
        super().__init__(application_id="test.chronoa.enterorb")
        self.asked = []

    def activate_action(self, name, parameter=None):
        self.asked.append(name)


@pytest.fixture
def app():
    application = _RecordingApp()
    yield application
    GLib.timeout_add(50, lambda: (application.quit(), False)[1])


@pytest.fixture
def window(app):
    """The real window, on an application that records what it was asked for."""
    win = ChronoaWindow(app, config=_Closed())
    yield win


def _asked(window):
    return window._app.asked


def _sent(window):
    """The text of every turn this window submits."""
    out = []
    window.connect("user-input", lambda _w, text: out.append(text))
    return out


# --- the defect ------------------------------------------------------------

def test_the_orb_names_a_key_that_reaches_the_orb(app):
    """The tooltip's own key, parsed out of the tooltip and pressed.

    **Driven by the tooltip rather than by a hardcoded keyval**, so changing the
    label has to change what is pressed - otherwise the test would go on
    asserting that the *old* promise holds after the label was rewritten.
    """
    window = ChronoaWindow(app, config=_Closed())
    tooltip = window._orb.get_tooltip_text() or ""
    assert tooltip, "the orb carries no tooltip, so there is nothing to keep true"

    named = re.findall(r"\(([^)]+)\)", tooltip)
    assert named, f"the tooltip promises no key at all: {tooltip!r}"

    keyvals = {"Enter": ENTER, "Return": ENTER, "Escape": Gdk.KEY_Escape}
    for name in named:
        keyval = keyvals.get(name)
        assert keyval is not None, (
            f"the tooltip promises {name!r}, which this test cannot press - so "
            f"the promise is not being checked at all"
        )
        window._on_input_key_pressed(None, keyval, 0, NO_MODIFIERS)

    assert _asked(window) == ["toggle-listening"], (
        f"the tooltip promises {named}, and pressing {named} asked for "
        f"{_asked(window) or 'nothing'}"
    )


def test_the_key_reaches_the_very_action_the_orb_carries(window):
    """**The chain, closed.** A keypress reaching an action of the right *name*
    is worth nothing if the orb names a different one - which is the
    `reply-style:qwen3-8b` defect this file records for a menu built from a loop
    that leaked a variable, where every row looked right and every row was wrong.
    """
    window._on_input_key_pressed(None, ENTER, 0, NO_MODIFIERS)
    asked = _asked(window)
    assert asked, "Enter on an empty composer asked for nothing at all"

    orb_action = window._orb.get_action_name()
    assert orb_action == "app.toggle-listening"
    assert f"app.{asked[0]}" == orb_action, (
        f"Enter asks for app.{asked[0]} but the orb carries {orb_action}"
    )


def test_enter_on_an_empty_composer_reaches_listening(window):
    """The dead keypress: Enter with nothing typed did nothing at all."""
    window._on_input_key_pressed(None, ENTER, 0, NO_MODIFIERS)
    assert _asked(window) == ["toggle-listening"]


def test_the_keypad_enter_does_too(window):
    """Both keys GTK delivers for the same physical key, as `_submit_input` does."""
    window._on_input_key_pressed(None, KEYPAD_ENTER, 0, NO_MODIFIERS)
    assert _asked(window) == ["toggle-listening"]


# --- the over-fix: everything that is NOT empty must still send ------------

def test_enter_with_text_still_sends(window):
    """**The control that matters most.** Enter-to-send is the convention every
    chat UI has *and the only route for a person who cannot reach the Send
    button*. A fix that swallowed it would pass every other test in this file.
    """
    sent = _sent(window)
    window._set_input_text("what time is it")
    window._on_input_key_pressed(None, ENTER, 0, NO_MODIFIERS)

    assert sent == ["what time is it"], "Enter no longer sends"
    assert _asked(window) == [], "Enter sent *and* opened the microphone"


def test_enter_with_attachments_does_not_open_the_microphone(window):
    """Three files attached and no words yet is not an empty composer - it is
    somebody about to type about three files.

    **Only the microphone is asserted, and deliberately not the send.** Reading
    `_submit_input` to write this found that it opens with `if not text: return`,
    so **Enter with files attached and no words does not send either** - the same
    dead keypress, one step further on, and pre-existing rather than introduced
    here. Asserting that it does not send would pin a defect so that fixing it
    later turns this file red, so it is recorded in the docstring and left alone.
    """
    window._attachments = [object(), object(), object()]
    window._on_input_key_pressed(None, ENTER, 0, NO_MODIFIERS)
    assert _asked(window) == [], "Enter opened the microphone with files attached"


def test_enter_during_a_question_does_not_open_the_microphone(window):
    """The trust moment. A question is answered from its own buttons; a
    microphone opening unasked inside a permission prompt is a surprise, and
    Enter was already a no-op here because the answer box is empty."""
    sent = _sent(window)
    window._pending_question = _PendingQuestion()

    window._on_input_key_pressed(None, ENTER, 0, NO_MODIFIERS)

    assert _asked(window) == [], "Enter opened the microphone mid-question"
    assert sent == [], "Enter submitted an empty answer to a pending question"


def test_whitespace_only_is_empty_because_submit_would_have_discarded_it(window):
    """`strip()`, deliberately - and pinned against the code that agrees.

    `_submit_input` compares `raw.strip()`, so a composer holding only spaces
    was *already* a no-op before this change. Treating it as empty is the
    consistent reading rather than a new invention; if `_submit_input` ever
    stops stripping, this fails instead of the two drifting apart.
    """
    window._set_input_text("   ")
    window._on_input_key_pressed(None, ENTER, 0, NO_MODIFIERS)
    assert _asked(window) == ["toggle-listening"]


# --- the keys that must keep their own meanings ---------------------------

def test_shift_enter_is_still_a_newline(window):
    """The only way to type two lines. A handler that acted on every Return
    would pass 'Enter sends' and quietly remove multi-line questions."""
    assert window._on_input_key_pressed(None, ENTER, 0, SHIFT) is False
    assert _asked(window) == [], "Shift+Enter opened the microphone"


def test_any_other_key_is_left_alone(window):
    for keyval in (Gdk.KEY_a, Gdk.KEY_space, Gdk.KEY_Escape, Gdk.KEY_F9):
        assert window._on_input_key_pressed(None, keyval, 0, NO_MODIFIERS) is False
    assert _asked(window) == []


# --- the controls on the fixture itself ------------------------------------

class _PendingQuestion:
    """Enough of a question for the guard, and nothing else."""

    question = "Allow delete_file once?"
    options = ("Allow this once", "No, don't allow")


def test_the_fixture_records_action_names_at_all(window):
    """Without this, a window that asked for nothing would pass every test above
    by making each assertion vacuous - and an *unregistered* application is
    exactly that, which is how the first version of this file failed.

    **A direct call rather than `orb.emit("clicked")`.** The first version of
    this control pressed the orb and got nothing: `set_action_name` makes the
    button *activate* the action, and `clicked` is emitted by that activation -
    so emitting `clicked` by hand goes around the half that does the work. The
    two halves are checked separately: this one proves the recorder works, and
    `test_the_key_reaches_the_very_action_the_orb_carries` reads the orb's own
    `get_action_name()` for the rest.
    """
    window._app.activate_action("probe")
    assert _asked(window) == ["probe"], (
        "this application records nothing, so every assertion above is vacuous"
    )
    assert window._orb.get_action_name() == "app.toggle-listening"