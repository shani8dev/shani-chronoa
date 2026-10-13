"""The empty state answers "what can I ask?" by being the answer.

**The capability signal already existed and was invisible.** `SuggestionBar` has
been in the empty state throughout, built from `capabilities.startup_suggestions`
so a skill that is not loaded is never offered. Measured on the real window at
the size it opens at:

    window 460x640
      [0] h=  18  Ask something, or press the orb to speak
      [1] h=  37  Ctrl+K for anything else - Ctrl+F to find... - ? for help
      [2] h=294  SuggestionBar
      empty state total  : 349px
      transcript viewport: 162px
      -> DOES NOT FIT, over by 187px

So the six prompts were built, correct, registry-derived - and **187px of them
were below the fold**, with the chips one per line while two fitted (199/134/
201/272/243/149px in a ~380px column). A present-but-unseen capability signal
reads exactly like no capability signal, which is why the empty composer seemed
to carry none.

After, same window, same probe:

      empty state total  : 149px
      transcript viewport: 162px
      -> FITS          (6 chips, two rows of three)

**This is the mode strip's "a menu item is a name, not a sentence", for a chip.**
A chip renders `label` and nothing beside it, so a chip can only hold a label.
`SUGGESTION_WORDS` holds that label; the tooltip, the accessible name and the
click payload all carry the question in full.

**Which is safe only because clicking fills rather than sends** - the property
`SuggestionBar`'s own docstring gives as its reason for filling. The user sees
the full question in the composer and can edit it before it goes anywhere, so
the name on the chip is a label and not a paraphrase of the request.

**What is measured here and what is recorded elsewhere.** The behaviour below -
what the chip prints, what it speaks, what it sends, and what happens when a
prompt has no word - is asserted without a display. The **pixel** fit is not:
that needs a realised window, and this repository's rule is that a display test
fails rather than skips when there is none, so the fit lives in `AGENTS.md` as a
measurement rather than here as a threshold that would only be fitted to this
machine.
"""
import pytest

gi = pytest.importorskip("gi")
gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import GLib, Gtk  # noqa: E402

from shani_chronoa import capabilities  # noqa: E402
from shani_chronoa.gui.widgets import SuggestionBar, TranscriptView  # noqa: E402

QUESTIONS = [
    "How much battery is left?",
    "What time is it?",
    "Set a timer for 10 minutes",
    "Which model would fit this machine?",
    "Search the web for the Arch wiki",
    "Take a screenshot",
]


def _chip_texts(bar):
    """The text printed on each chip.

    **`FlowBoxChild` -> `Gtk.Button` -> `Gtk.Label`, three levels.** The first
    version asked the `FlowBoxChild` for its child, got the *Button*, and
    compared six empty strings against the expected names - so it failed for a
    reason that had nothing to do with the chip. A helper shaped like the widget
    rather than like the thing on it reports its own mistake as a defect.
    """
    out = []
    child = bar.get_first_child()
    while child is not None:
        button = child.get_child()
        label = button.get_child() if isinstance(button, Gtk.Widget) else None
        out.append(label.get_text() if isinstance(label, Gtk.Label) else "")
        child = child.get_next_sibling()
    return out


def _tooltip_questions(bar):
    out = []
    child = bar.get_first_child()
    while child is not None:
        tooltip = child.get_child().get_tooltip_text() or ""
        out.append(tooltip[len("Send: "):] if tooltip.startswith("Send: ") else "")
        child = child.get_next_sibling()
    return out


@pytest.fixture
def bar():
    return SuggestionBar(list(QUESTIONS), lambda *a: None,
                         short_words=capabilities.SUGGESTION_WORDS)


@pytest.fixture
def app():
    application = Gtk.Application(application_id="test.chronoa.emptystate")
    yield application
    GLib.timeout_add(50, lambda: (application.quit(), False)[1])


# --- the name is printed, the question is what is offered --------------------

def test_each_chip_prints_the_short_name(bar):
    assert _chip_texts(bar) == [
        "Battery left?",
        "What time?",
        "Set a timer",
        "Which model?",
        "Search the web",
        "Screenshot",
    ], (
        "a chip that printed the whole question is what put the empty state "
        "187px below the fold"
    )


def test_each_chip_still_offers_the_whole_question(bar):
    assert _tooltip_questions(bar) == QUESTIONS, (
        "the spoken prompt must stay the full question even though the chip "
        "prints a name - a screen reader hearing 'Battery left?' is being told "
        "less than one hearing the question"
    )


def test_clicking_fills_the_question_not_the_name():
    """**The behavioural witness, and the reason the name is safe.**

    If this ever sends the short word, the empty state would offer a prompt
    Chronoa cannot answer - and it would do so from the one surface whose whole
    job is to show what it can answer.
    """
    seen = []
    b = SuggestionBar(list(QUESTIONS), lambda _btn, text: seen.append(text),
                      short_words=capabilities.SUGGESTION_WORDS)
    b.get_first_child().get_child().emit("clicked")
    assert seen == [QUESTIONS[0]], (
        f"the click carried {seen!r}; the chip prints a name but must send the "
        f"question it stands for"
    )


# --- the fallback, which is what makes this safe to extend --------------------

def test_a_prompt_with_no_word_falls_back_to_the_question():
    """A new skill added to `_SUGGESTION_ORDER` without a word still works.

    The failure mode of the fallback is that the chip is too long again, which is
    the bug this change fixed; it can never be that the prompt disappears, and
    this is what says so.
    """
    q = "Do something nobody has written a name for?"
    b = SuggestionBar([q], lambda *a: None,
                      short_words=capabilities.SUGGESTION_WORDS)
    assert _chip_texts(b) == [q]
    assert _tooltip_questions(b) == [q]


def test_a_bar_built_without_the_table_still_prints_the_questions():
    """Every existing caller passes plain strings and keeps the old behaviour."""
    b = SuggestionBar(list(QUESTIONS), lambda *a: None)
    assert _chip_texts(b) == QUESTIONS
    assert _tooltip_questions(b) == QUESTIONS


# --- the table itself, which is where a rot would hide ----------------------

def test_every_key_is_a_prompt_the_module_actually_offers():
    """A key nothing offers is a dead row in a hand-kept table.

    This is the `capabilities._GROUPS` / `GATED` shape this repository records
    repeatedly: the list is filtered against the live registry, so a typo in a
    key is silent - the prompt it was meant to shorten simply never gets
    shortened, and the empty state goes back below the fold with nothing saying
    why.
    """
    assert set(capabilities.SUGGESTION_WORDS) <= set(capabilities._SUGGESTION_ORDER)


def test_every_order_entry_that_survives_has_a_word():
    """The other direction, and the one that bites the newcomer.

    A prompt offered with no word still **works** - so the control above cannot
    fail on this - but it is **long**, which is the whole defect. Asserting the
    coverage rather than the table is what makes a new prompt without a word
    visible, and the control below is that the empty table really does produce
    the state measured at 349px in a 162px viewport.
    """
    assert set(capabilities._SUGGESTION_ORDER) == set(capabilities.SUGGESTION_WORDS), (
        "a prompt with no word still works but is long, which is the defect this "
        f"table exists to remove; missing: "
        f"{set(capabilities._SUGGESTION_ORDER) - set(capabilities.SUGGESTION_WORDS)}"
    )
    bar = SuggestionBar(list(QUESTIONS), lambda *a: None)
    assert _chip_texts(bar) == QUESTIONS, (
        "with no word table the chips print the questions again - the control "
        "for the assertion above, which would otherwise pass on a table nobody "
        "had emptied"
    )


def test_no_word_is_longer_than_the_question_it_stands_for():
    """A word that grew into a sentence is the bug arriving in the other guise."""
    for question, word in capabilities.SUGGESTION_WORDS.items():
        assert len(word) < len(question), (
            f"{word!r} is no shorter than {question!r}, so the chip would be no "
            f"narrower than showing the question - the table has rotted into a "
            f"no-op"
        )


def test_the_words_are_names_rather_than_sentences():
    """A name, not a question - the rule the mode strip already follows.

    A chip that ends in a full stop and reads as a question is a sentence that
    happens to be shorter, and it wraps to the width of the window it is in. The
    measured chips are 104-132px against a ~380px column, so three fit a row;
    the longest of these is 18 characters and none carries terminal punctuation.
    """
    for question, word in capabilities.SUGGESTION_WORDS.items():
        assert word and not word.endswith((".", "!")), (
            f"{word!r} reads as a sentence rather than a name"
        )
        assert len(word) <= 18, (
            f"{word!r} is {len(word)} characters; the measured chips that pack "
            f"three to a row are 104-132px, and these must stay in that range"
        )


# --- the wiring, which is where every other test here is blind --------------

def test_the_empty_state_itself_prints_the_names(app):
    """**The product surface, not the widget.**

    Every other test in this file builds a `SuggestionBar` itself, so a
    `TranscriptView` that stopped passing `SUGGESTION_WORDS` would leave the
    whole file green while the empty state went back to printing six questions -
    the state measured at **349px in a 162px viewport**.

    That is not hypothetical: reverting the one line that passes the table was
    the mutation this test was written for, and **the other nine did not see
    it**. A test that reaches past the call site to the function behind it is
    testing the function.
    """
    from shani_chronoa.gui import ChronoaWindow

    window = ChronoaWindow(app, config=_Closed())
    chips = _chips(window)
    assert chips, "an empty window with no suggestions is a different bug"
    printed = []
    for chip in chips:
        label = chip.get_child()
        printed.append(label.get_text() if isinstance(label, Gtk.Label) else "")
    questions = set(capabilities.startup_suggestions(window._transcript._caps))
    expected = {capabilities.SUGGESTION_WORDS.get(q, q) for q in questions}
    assert set(printed) == expected, (
        f"the empty state printed {sorted(set(printed))}, expected "
        f"{sorted(expected)} - the word table is not reaching the widget"
    )
    assert set(printed) != questions, (
        "the empty state printed the questions themselves, so nothing changed "
        "and the capability signal is 187px below the fold again"
    )


def _chips(window):
    """Every chip button in the window's transcript, as the product builds them."""
    out = []

    def walk(node):
        if isinstance(node, Gtk.Button) and node.has_css_class("suggestion-chip"):
            out.append(node)
        child = node.get_first_child()
        while child is not None:
            walk(child)
            child = child.get_next_sibling()

    walk(window._transcript)
    return out


class _Closed:
    """A config that grants nothing, so nothing here reaches a real machine."""

    def sense_allowed(self, key):
        return False


# --- and the scroll rule the empty state also depends on --------------------

def _sized(view, upper=400.0, page=200.0, value=0.0):
    """Give the view's adjustment a content taller than its viewport.

    **Without this the bottom-of-the-transcript assertions cannot fail.** An
    unallocated view reports `upper == page_size == 0`, so
    `max(0.0, upper - page_size)` is `0.0` and "followed to the bottom" is
    indistinguishable from "scrolled to the top" - the two rules this pair of
    tests exists to tell apart would agree on every input.

    The numbers are the ones measured on the real window: the empty state's
    content was **375px** in a **201px** viewport and the scrollbar read
    **194**.
    """
    adj = view.get_vadjustment()
    adj.set_upper(upper)
    adj.set_page_size(page)
    adj.set_value(value)
    return adj


def test_the_empty_state_is_read_from_the_top():
    """A placeholder is a prompt, not a tail, so it is scrolled to the top.

    Before the fix the follow rule jumped to the bottom of the suggestion bar,
    which took the prompt and the shortcut hints off the top with it - so the
    first screen of a new install showed the middle of a chip row and none of
    the capability signal.
    """
    view = TranscriptView()
    view.show_placeholder()
    adj = _sized(view, value=194.0)
    assert adj.get_value() == 194.0, (
        "the precondition did not hold, so this assertion is already true before "
        "anything runs - a test that cannot fail"
    )
    view._apply_follow()
    assert adj.get_value() == 0.0, (
        f"the empty state is scrolled to {adj.get_value()}, so the prompt and "
        f"the shortcut hints are above the fold"
    )


def test_a_conversation_is_still_followed_to_the_bottom():
    """**The over-fix control.** Reading the empty state from the top must not
    have swallowed the rule every other turn depends on - a newcomer typing a
    first message and watching the reply scroll into view is the whole
    transcript's purpose."""
    view = TranscriptView()
    view.add_user_turn("hello")
    adj = _sized(view, value=0.0)
    view._apply_follow()
    assert adj.get_value() == 200.0, (
        f"a new turn left the scrollbar at {adj.get_value()}; a reply arriving "
        f"off-screen is the failure the follow rule exists to prevent"
    )
