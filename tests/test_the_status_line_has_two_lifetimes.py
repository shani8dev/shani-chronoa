"""The status line holds two lifetimes, and an aside outranks a running reading.

One line, and 95 writers across 11 modules, and four kinds of intent on it: a
refusal, a settings toggle, the tool clock, and the turn loop's clear. With one
slot and no ordering between them, each could destroy the others. Measured on
the real window on 2026-10-14, before this file existed:

  * a refusal written while a tool ran was **gone within a second** - the clock
    ticks once a second and overwrote it (`Generate image... 1s`);
  * a settings toggle was destroyed the same way (`Write text file... 1s`);
  * anything said in the **last second of a turn** was erased before it was
    ever on screen, because the turn's clear ran immediately after it;
  * a **failed** turn wrote the idle label onto the detail line, so the window
    read `detail='Ready', state='Ready'` - a state word on the one line
    documented as never carrying state.

None of the four was a layout problem, and none of them is fixed by layout.
A second lane would have cost a permanent line of chrome out of a window whose
minimum is exactly its measured chrome plus 120px of transcript - zero slack.
So the fix is a *precedence rule*, held in two fields behind one writer.

Every test here drives the real code: the real `ConversationMixin` clock and the
real `_on_response_ready`, against a real `ChronoaWindow`. An earlier version of
this work hand-copied `window.set_status("")` from `conversation.py` into a
probe and then reported the bug as unfixed after the product had been fixed -
because the copy was the old call. The turn-end tests therefore call
`_on_response_ready`, not `set_progress("")`, so they cannot pass by testing
something the product does not do.

**Seven of these sixteen fail against `HEAD`; nine are guards.** The guards catch
the over-fix rather than the under-fix - a lane that is never cleared, an aside
that never decays, a rail sent back to reading the label - and by construction
cannot fail pre-fix, because pre-fix there was nothing to over-fix. They are
counted here so that nobody later reads "16 tests" as "16 reasons this defect
was real". The seven are the reason.
"""

import sys
import threading
import time

import pytest

gi = pytest.importorskip("gi")
gi.require_version("Gtk", "4.0")
from gi.repository import Gtk, GLib, Pango  # noqa: E402

sys.path.insert(0, "usr/lib/shani-chronoa")
from shani_chronoa.app.conversation import ConversationMixin  # noqa: E402
from shani_chronoa.gui import AssistantState, ChronoaWindow  # noqa: E402

#: A real refusal, in the words `permissions.py` builds. Not a stand-in: the
#: collision was measured with this sentence, and a short one would not have
#: exercised the clip either.
REFUSAL = ("Not done: That is outside what you allowed this time "
           "(/home/me/report.pdf)")

#: A settings toggle, which has **no other home at all** - the rail's Posture
#: card carries the live value, but the confirmation that it just changed was
#: only ever this line.
TOGGLE = "Privacy: ON (Local Only)"

#: Long enough that it cannot fit a 455px column on one line. Measured 854px.
LONG_ASIDE = (
    "Not done: saving 'holiday-photos-2026.tar.gz' to /home/me/Pictures would "
    "reach outside what you allowed this time; ask again if that is what you "
    "meant")


def _pump(seconds=1.5):
    ctx = GLib.MainContext.default()
    deadline = time.time() + seconds
    while time.time() < deadline:
        while ctx.pending():
            ctx.iteration(False)
        time.sleep(0.01)


@pytest.fixture(scope="module")
def gtk_app():
    """A real `Gtk.Application` with a live main loop, run once for the module.

    One per process: a second `Gtk.Application` cannot register
    (`An object is already exported for the interface org.gtk.Application`),
    which `tests/test_away_line.py`'s docstring records and this file inherited.
    """
    app = Gtk.Application(application_id="test.chronoa.statusline")
    holder = {}
    started = threading.Event()

    def on_activate(a):
        holder["app"] = a
        started.set()
        a.quit()

    app.connect("activate", on_activate)
    app.run([])
    assert started.wait(10), "Gtk.Application never activated"
    yield app


@pytest.fixture
def window(gtk_app):
    """A real window, **presented**, because two tests here measure layout.

    Building a `ChronoaWindow` is not enough: an unmapped window is never laid
    out, so `get_allocation()` is 0 and anything measured against the chat
    column's width is measured against nothing. That was a real failure - the
    bound test asserted on a 0px budget until this fixture presented the window.
    (`present()` on an application whose `startup` never fired prints a
    `Gtk-CRITICAL`; the window still maps and lays out, which is why the rest of
    the repository's tests tolerate it.)
    """
    win = ChronoaWindow(gtk_app)
    win.present()
    _pump(1.4)
    yield win
    win.destroy()


class _Turn(ConversationMixin):
    """The real turn machinery, against the real window.

    It inherits rather than standing in for the mixin because the methods under
    test reach for `self._stop_tool_clock` and `self._turn_finished`. A class
    that copied those beside its attributes would be testing the copy.

    Only memory capture is stubbed: it runs after everything measured here and
    short-circuits on a falsy assistant, so standing up a model to reach it
    would be a fixture with a life of its own.
    """

    def __init__(self, win):
        self.window = win
        self.assistant = None
        self._tool_clock = None
        self._tool_clock_token = 0
        self._clock_token_at_start = 0
        self._tool_clock_name = ""
        self._tool_clock_started = 0.0
        self._turn_streamed = 0
        self._turn_speech = None
        self._turn_buffer = None
        self._turn_marks = {}
        self._in_flight = False

    def _mark(self, what):
        pass

    def _log_latency(self):
        pass

    def _follow_active_conversation(self):
        pass

    def _maybe_title(self):
        pass

    def _turn_finished(self):
        pass


@pytest.fixture
def turn(window):
    """A turn whose clock is stopped again afterwards.

    `_on_tool_call_main` adds a **repeating** `GLib.timeout_add_seconds` and
    nothing in a test removes it, so without this each test left a live source
    ticking once a second against a window that was about to be destroyed - for
    the rest of the module. That is both a leak and a source of cross-test
    coupling, which is how two witnesses for the same rule ended up disagreeing
    depending on what ran before them.
    """
    made = _Turn(window)
    yield made
    made._stop_tool_clock()


def _detail(window):
    return window._detail_label.get_label()


# ----------------------------------------------------------------------
# The rule: an aside outranks a running reading.
# ----------------------------------------------------------------------


def test_a_refusal_written_while_a_tool_runs_is_still_there_after_a_tick(window,
                                                                        turn):
    """The first measured collision: the clock tick destroyed the refusal.

    Before: `Generate image... 1s` a second and a bit after the refusal was
    written. The refusal is **not** toasted - `_TOAST_PREFIXES` is
    `("Asking again", "Attached ", "Kept in", "Saved to", "Copied")` - so this
    line was the only place the sentence existed.

    **The tick is called, not waited for.** An earlier version of this test armed
    the real clock and pumped 1.4s, on the assumption that one tick lands in
    1.4s. It does not reliably: `GLib.timeout_add_seconds(1, ...)` is aligned to
    the wall-clock second, and instrumented against the real window the callback
    **did not run at all** within 1.4s (`ctx.pending()` never true). That made
    the test a coin flip which landed differently for its twin,
    `test_a_settings_toggle_survives_a_running_tool` - passing 3 of 3 in
    isolation and failing when a test ran before it. A witness that only bites
    when something else ran first is not a witness.

    `_on_tool_clock_tick` *is* the tick, so calling it drives the product's own
    code and does not depend on where the second boundary falls.
    """
    window.set_status(REFUSAL)
    turn._on_tool_call_main("generate_image")
    turn._on_tool_clock_tick()
    assert _detail(window) == REFUSAL, (
        f"the clock tick took the line back: {_detail(window)!r}")


def test_a_settings_toggle_survives_a_running_tool(window, turn):
    """The same collision, for the job with no transcript row behind it."""
    window.set_status(TOGGLE)
    turn._on_tool_call_main("write_text_file")
    turn._on_tool_clock_tick()
    assert _detail(window) == TOGGLE, (
        f"the clock took the line back: {_detail(window)!r}")


def test_the_clock_still_runs_when_nothing_else_has_been_said(window, turn):
    """The control for the two tests above: the clock was not simply disabled.

    A rule that stopped the clock entirely would pass both of them. The clock is
    the only way to see that a tool is hung, so it has to keep working when the
    line is free.

    **The elapsed time is set, not waited for.** The first version asserted that
    a tick advances the reading, which fails on a tick delivered in the same
    millisecond it was armed - and correctly so: `_tool_clock_text` omits `0s`.
    Rewinding `_tool_clock_started` gives the tick two seconds of elapsed time
    to report, and the arithmetic under test is still the product's own.
    """
    turn._on_tool_call_main("generate_image")
    assert _detail(window) == "Generate image...", (
        f"arming the clock writes nothing: {_detail(window)!r}")
    turn._tool_clock_started -= 2.0
    turn._on_tool_clock_tick()
    assert _detail(window).startswith("Generate image... 2"), (
        f"the tick does not advance the clock: {_detail(window)!r}")


def test_the_clock_stays_out_of_the_way_while_an_aside_shows(window, turn):
    """And the progress lane still records, because the rail reads it.

    The clock is not discarded while an aside shows - it is *not shown*. That
    distinction is load-bearing: the rail's Now section reports the running tool
    from this lane, so a tool that is really running must still be reported.
    """
    window.set_status(REFUSAL)
    turn._on_tool_call_main("generate_image")
    turn._on_tool_clock_tick()
    assert window._rail_tool().startswith("Generate image"), (
        f"the rail cannot see the running tool: {window._rail_tool()!r}")
    assert _detail(window) == REFUSAL


def test_the_rail_reports_a_tool_and_not_an_aside(window, turn):
    """`_fill_now` prints `Running {tool}`, so the two must not be confused.

    With the aside outranking the label, a rail that read the label would print
    *"Running Not done: That is outside what you allowed this time"* - the rail
    claiming a refusal was a tool. It used to read the label, which was correct
    only while the two lanes were one lane.
    """
    window.set_status(REFUSAL)
    turn._on_tool_call_main("generate_image")
    turn._on_tool_clock_tick()
    assert "Not done" not in window._rail_tool()


def test_a_later_aside_replaces_an_earlier_one(window):
    """The aside is the last thing that happened, and there is one of them."""
    window.set_status(TOGGLE)
    window.set_status(REFUSAL)
    assert _detail(window) == REFUSAL


# ----------------------------------------------------------------------
# The turn loop's clear is the progress lane, not the whole line.
# ----------------------------------------------------------------------


def test_an_aside_survives_the_end_of_a_successful_turn(window, turn):
    """The third measured collision: the turn's clear erased the refusal.

    Driven through `_on_response_ready` rather than by calling
    `set_progress("")`, because calling `set_progress("")` would test the fix
    rather than the product.
    """
    window.set_status(REFUSAL)
    turn._on_response_ready("Here is the answer.")
    assert _detail(window) == REFUSAL, (
        f"the turn's clear took the aside: {_detail(window)!r}")


def test_an_aside_survives_the_end_of_a_failed_turn(window, turn):
    """The same for the error path, which additionally used to write a word."""
    window.set_status(REFUSAL)
    turn._on_response_ready(RuntimeError("the model stopped"))
    assert _detail(window) == REFUSAL, (
        f"the turn's clear took the aside: {_detail(window)!r}")


def test_a_failed_turn_does_not_write_a_state_word_onto_the_detail_line(window,
                                                                       turn):
    """Measured before: after a failure the window read "Ready" **twice**.

    `conversation.py` called `set_status("Ready")`, which is a *state* word on
    the one line whose docstring says it cannot carry state, and it is also the
    `_state_label`'s own idle label - so the same widget answered "what is
    Chronoa doing" twice, which is the shape this repository records for a rail
    that tracks state separately.
    """
    window.clear_status()
    turn._on_response_ready(RuntimeError("the model stopped"))
    assert _detail(window) != "Ready", (
        "the idle label is back on the detail line")
    assert window._state_label.get_label() != "Ready", (
        "and the state line no longer says the turn is idle, so the person is "
        "left with neither line telling them anything")


def test_the_state_line_owns_the_state_after_a_successful_turn(window, turn):
    """The control: the state word is not lost, it is on the line that owns it."""
    turn._on_response_ready("Here is the answer.")
    assert window._state_label.get_label() == AssistantState.IDLE.label
    assert _detail(window) == ""


def test_clear_status_wipes_both_lanes(window, turn):
    """`clear_status` is the full clear, and stays distinct from the turn's.

    `tests/test_window_ux.py` asserts the detail label is hidden after it, so it
    has to clear the progress lane too - otherwise the label would keep showing
    a clock the caller believes it just erased.
    """
    window.set_status(REFUSAL)
    turn._on_tool_call_main("generate_image")
    turn._on_tool_clock_tick()
    window.clear_status()
    assert not window._detail_label.get_visible()
    assert window._rail_tool() == "", (
        f"a tool is still reported after a full clear: {window._rail_tool()!r}")


def test_a_status_of_nothing_clears_the_aside(window):
    """`set_status("")` still means "there is no aside".

    The turn loop no longer uses it - that is the whole point of the second
    lane - but a caller asking for no aside is asking for no aside, and
    `clear_status` is the way to ask for *both* lanes.
    """
    window.set_status(REFUSAL)
    window.set_status("")
    assert not window._detail_label.get_visible()


# ----------------------------------------------------------------------
# And a long aside is readable rather than clipped.
# ----------------------------------------------------------------------


def test_the_detail_line_wraps_within_a_bound(window):
    """It had no wrap and no ellipsize, so a refusal was clipped mid-sentence.

    Measured at the window's own minimum: a 150-character refusal wanted 854px
    of a 455px chat column, so **399px was clipped** - and the clipped end is
    where a refusal names the file and the limit, which is the part that tells
    the person what to do next.
    """
    label = window._detail_label
    assert label.get_wrap(), "the detail line cannot wrap, so a long aside is clipped"
    assert label.get_ellipsize() != Pango.EllipsizeMode.NONE, (
        "a wrapped label without ellipsize grows without limit")
    assert label.get_lines() == 2, (
        f"the line is bounded at {label.get_lines()} lines, not 2, so the "
        f"chrome can grow to 82px out of a minimum with no slack")


def test_a_long_aside_is_cut_at_the_bound_and_not_before_it(window):
    """Two lines is a promise about height, so measure the height.

    The first version of this test used a 150-character aside and asserted the
    height was under three lines. The mutation run showed it **could not fail**:
    150 characters need two lines whether the bound is 2 or 5, so loosening the
    bound left the height at 33px and the test passed. Measured at the window's
    own minimum, one line being 17px:

    | aside | bound 2 | bound 5 |
    |---|---|---|
    | 150 characters | 33px | 33px |
    | 300 characters | **33px** | 66px |
    | 1200 characters | **33px** | 82px |

    So the bound is asserted on text long enough for it to bite.

    **And measured against the column, not against `-1`.** The second version of
    this test still could not fail, for a reason worth recording: it called
    `measure(VERTICAL, -1)`, which is "height with no width constraint", so the
    text laid out on a single line and reported 17px whatever the bound was. The
    binding constraint for a wrapped label is the width it is given, so the
    budget has to be the column's own allocation - the same instrument correction
    that the first version of the width probe needed, when it compared a label's
    natural width against its own allocation and so could not disagree.
    """
    budget = window._chat_column.get_allocation().width
    assert budget > 0, (
        "the chat column has no allocation, so this test cannot measure "
        "anything against it - the window was never laid out")

    window.set_status("Asking again")
    _pump(0.25)
    label = window._detail_label
    one_line = label.measure(Gtk.Orientation.VERTICAL, budget)[1]

    window.set_status(LONG_ASIDE * 2)
    _pump(0.35)
    two_lines = label.measure(Gtk.Orientation.VERTICAL, budget)[1]

    assert two_lines <= one_line * 2 + 1, (
        f"a 300-character aside took {two_lines}px against {one_line}px for one "
        f"line at a {budget}px column, so the bound is not holding and the "
        f"chrome can grow without limit")


def test_a_short_aside_costs_one_line_so_the_chrome_does_not_move(window):
    """The window's minimum is exactly its measured chrome plus 120px of
    transcript - **zero slack** - so a permanent second line would have had to
    be paid for out of the conversation. A bound that only costs when it wraps
    is not that."""
    window.set_status("Asking again")
    _pump(0.25)
    budget = window._chat_column.get_allocation().width
    short = window._detail_label.measure(Gtk.Orientation.VERTICAL, budget)[1]
    window.set_status("Ready")
    _pump(0.25)
    other_short = window._detail_label.measure(Gtk.Orientation.VERTICAL, budget)[1]
    assert short == other_short, (
        "two different short asides need different heights, so the chrome is "
        "not stable and the measured minimum is stale")


def test_a_refusal_that_does_not_fit_the_column_still_shows_its_start(window):
    """The clip was on the *end*, and the end is the actionable half."""
    window.set_status(LONG_ASIDE)
    _pump(0.3)
    shown = window._detail_label.get_label()
    assert shown.startswith("Not done: saving")