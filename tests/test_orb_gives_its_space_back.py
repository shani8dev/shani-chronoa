"""The orb was sized for the halo's loudest moment, in a silent room too (2026-10-13)

Measured on the real window at the size it opens at:

    window 460x640, chat column 566px
      ChronoaOrbWidget          124px   21.9%
      organ strip                72px   12.7%
      mode strip                 40px    7.1%
      composer                   36px    6.4%
      model row                  34px    6.0%
      task card                  18px    3.2%
      state + pill               40px    7.0%
      the conversation          162px   28.6%

The orb was **76px taller than the entire conversation**, and `set_size_request(124,
124)` was hardcoded with the comment *"Roomier than the 80px orb so the level halo
has somewhere to go"*. It never gave any of it back: after three real turns the
transcript measured **161px**, one pixel smaller than when the window was empty.

**A person asking their third question reads it in a box 77% the height of a
decorative microphone.**

The halo is why 124 is the right number for *some* state - `_halo_size` runs from
84px at rest to 120px at full input level, and the button has to hold the largest.
So the fix is not a smaller orb; it is an orb sized to the halo it is actually
holding.

**After** (same window, same probe): orb **94**, transcript **191** - +29px, +18%.

**Not claimed:** that the idle -> listening *transition* re-lays-out. On broadway
the allocation does not move at all on a state change (measured at 260ms and again
at 1200ms), so the transition is asserted below at the **size request**, which is
deterministic and needs no display, and its layout effect is recorded here as not
established rather than assumed.
"""
import pytest

gi = pytest.importorskip("gi")
gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, GLib, Gtk  # noqa: E402

Adw.init()

from shani_chronoa.gui import ChronoaWindow  # noqa: E402
from shani_chronoa.gui.widgets import (  # noqa: E402
    _LIVE_STATES,
    AssistantState,
    ChronoaOrbWidget,
    _halo_size,
    _orb_size,
)


class _Closed:
    """A config that grants nothing, so nothing here reaches a real machine."""

    def sense_allowed(self, key):
        return False


@pytest.fixture
def app():
    application = Gtk.Application(application_id="test.chronoa.orb")
    yield application
    GLib.timeout_add(50, lambda: (application.quit(), False)[1])


@pytest.fixture
def window(app):
    return ChronoaWindow(app, config=_Closed())


def _states_that_feed_a_level(window):
    """Which states let a real input/playback level reach the halo.

    **Derived by driving the window's own two methods, not by reading a table.**
    There are exactly two - `set_input_level` and `set_playback_level` - and each
    guards itself on a state, so that a level arriving after a turn ended cannot
    make the halo pulse at nothing. Asking those guards *is* the question
    `_LIVE_STATES` answers; reading a hand-kept list beside them is how the two
    would drift.

    `_level_target` is what is read rather than the halo's size, because
    `set_level` eases toward its target on a tick. The guard's decision is the
    thing being asked, and it is recorded the moment the guard makes it.

    **The window's `set_state`, not the orb's.** The first version drove
    `orb.set_state(state)` and found *zero* states feeding a level, which was the
    probe's fault and not the code's: `set_input_level` guards on
    **`self._state`**, the window's own copy, so moving the orb alone leaves the
    window's state stale and every guard declines. The window is what the state
    machine is, and the orb is told about it.
    """
    feeding = set()
    for state in AssistantState:
        window._orb._level = 0.0
        window._orb._level_target = 0.0
        window.set_state(state)
        window.set_input_level(1.0)
        window.set_playback_level(1.0)
        if window._orb._level_target > 0.0:
            feeding.add(state)
    return feeding


# --- the invariant, which is the whole change ------------------------------

def test_the_orb_is_never_smaller_than_the_halo_it_can_contain(window):
    """**The property, over every state, with the halo's bound derived per state.**

    The first version of this change sized the orb for LISTENING only, reasoning
    that listening is when the halo grows. Measuring *who feeds the level* found
    `set_playback_level` does the same thing while **SPEAKING** - so the halo
    would have reached 120px inside an 88px button for the whole length of every
    spoken reply. This assertion is what that version would have failed.
    """
    feeding = _states_that_feed_a_level(window)
    assert len(feeding) >= 2, (
        f"only {feeding} feed a level; the two that do were supposed to be found, "
        f"so this is measuring a window that cannot produce the defect"
    )

    orb = window._orb
    for state in AssistantState:
        orb._level = 0.0
        orb._level_target = 0.0
        orb.set_state(state)
        side = orb.get_size_request()[0]
        halo = _halo_size(1.0)[0] if state in feeding else _halo_size(0.0)[0]
        assert side >= halo, (
            f"in {state.name} the orb asks for {side}px and the halo can reach "
            f"{halo}px, so the halo is clipped"
        )


def test_a_quiet_window_does_not_carry_the_reserve(window):
    """The space half. 124 everywhere was the defect; 124 nowhere would be the
    over-fix, and these two assertions are what tell them apart."""
    resting = {_orb_size(s) for s in AssistantState if s not in _LIVE_STATES}
    assert len(resting) == 1, f"the resting sizes disagree: {resting}"
    assert resting.pop() < _halo_size(1.0)[0], (
        "the resting orb is already as big as the halo's loudest size, so the "
        "reserve was never what this change measures and it measures nothing"
    )


def test_the_states_that_grow_are_exactly_the_states_that_feed_a_level(window):
    """The mapping, so a fourth level producer cannot be added silently."""
    assert _states_that_feed_a_level(window) == _LIVE_STATES


# --- construction and the change, separately -------------------------------

def test_a_freshly_built_orb_is_already_at_resting_size():
    """A window built at startup must not come up carrying the reserve - that is
    the measurement this whole change exists for. Applying the size only on the
    first *state change* would pass every test above."""
    orb = ChronoaOrbWidget()
    assert orb.get_size_request()[0] == _orb_size(AssistantState.IDLE)


def test_the_size_follows_the_state(window):
    """Both directions: into a live state and back out of it."""
    orb = window._orb
    for state in sorted(_LIVE_STATES, key=lambda s: s.name):
        orb.set_state(state)
        assert orb.get_size_request()[0] == _orb_size(state), state.name

    resting = AssistantState.THINKING
    assert resting not in _LIVE_STATES
    orb.set_state(resting)
    assert orb.get_size_request()[0] == _orb_size(resting)


def test_a_repeated_state_does_not_move_the_size(window):
    """`set_state` returns early when nothing changed. A control that only ever
    reads the size cannot tell "resized once" from "resized twice", so this
    states the inertness rather than assuming it."""
    orb = window._orb
    orb.set_state(AssistantState.THINKING)
    before = orb.get_size_request()
    orb.set_state(AssistantState.THINKING)
    assert orb.get_size_request() == before


# --- the controls on the measurement itself -------------------------------

def test_the_halo_bound_is_not_vacuous():
    """The invariant above passes with every state resting at 124px, so it needs
    a statement about what it is bounded against."""
    assert _halo_size(0.0)[0] < _halo_size(1.0)[0], (
        "the halo is the same size at rest and at full level, so 'orb >= halo' "
        "is not testing the reserve at all"
    )


def test_every_state_is_reachable():
    """A loop over zero states satisfies an invariant and means nothing."""
    assert len(list(AssistantState)) >= 7
    assert len(_LIVE_STATES) >= 2