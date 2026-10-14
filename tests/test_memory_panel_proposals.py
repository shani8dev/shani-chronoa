"""The Memory panel's "Proposed" section - built, pressed, and read.

The acceptor's second consent surface. `tests/test_sleep_proposals_can_be_
answered.py` holds the function and `tests/test_review_proposals_skill.py` the
skill; this holds the panel, which is the door a person is most likely to walk
through - and the one that would be a dead control if the section rendered
without reaching the shared acceptor.

Everything here builds the **real surface** and presses the **real buttons**.
A test that calls `_proposals_section` and asserts on the widget it returns
proves the section builds; it does not prove `refresh()` puts it on screen, and
this repository has shipped features that passed every test of their own
function while nothing reached a window.

The harness (the app stub, the tree walk, the marker readers, the durable-file
reader) is imported from `test_surface_memory.py` rather than copied: two
answers to "how do I find a row in a built widget" is one too many, and the copy
would drift the week after.
"""

from __future__ import annotations

import inspect

import pytest

gi = pytest.importorskip("gi")
gi.require_version("Gtk", "4.0")
from gi.repository import Gtk  # noqa: E402

from shani_chronoa import consolidation  # noqa: E402
from shani_chronoa.config import ChronoaConfig  # noqa: E402

from tests.test_surface_memory import (  # noqa: E402
    _app,
    _durable_lines,
    _fact,
    _labels,
    _walk,
    surface,
)

CHAT = [
    {"role": "user", "content": "I moved to Pune in March."},
    {"role": "assistant", "content": "Noted."},
    {"role": "user", "content": "The build server lives in the annex."},
    {"role": "assistant", "content": "Okay."},
    {"role": "user", "content": "I keep a spare key under the mat."},
    {"role": "assistant", "content": "Sure."},
]

SESSION = "20260101-120000-abcd"
FACT = "I keep a spare key under the mat"


def _digest(session_id=SESSION, text=FACT, index=4, candidates=None):
    """A digest built by the real `consolidate`, then given its proposals.

    `consolidate` does the summarising and indexing; only the candidate list is
    replaced, because what is under test is the *accepting* of a candidate and
    not what sleep chose to notice.
    """
    digest = consolidation.consolidate(session_id, CHAT)
    digest.candidates = ([consolidation.Candidate(text, index, "user")]
                         if candidates is None else candidates)
    return digest


@pytest.fixture
def sleep_dir(tmp_path, monkeypatch):
    """The real sleep store, redirected, holding one real proposal."""
    home = tmp_path / "sleep"
    monkeypatch.setattr(consolidation, "store_dir", lambda: home)
    consolidation.save_digest(_digest(), home)
    yield home
    for path in home.glob("*.json"):
        path.unlink()


@pytest.fixture
def store(tmp_path, monkeypatch):
    """A real `PerceptStore` on this test's own directory, consent granted."""
    ChronoaConfig().set("memory-sense-enabled", "true")
    from shani_chronoa.senses import memory
    from shani_chronoa.senses.store import PerceptStore

    held = PerceptStore(
        durable_path=tmp_path / "percepts" / "memory.jsonl",
        live_path=tmp_path / "percepts" / "live.json",
    )
    monkeypatch.setattr(memory, "_STORE", held)
    return held


def _page(store):
    return surface._MemorySurface(_app(store))


def _buttons(page, label):
    """Every `Gtk.Button` on the page carrying `label`."""
    return [w for w in _walk(page)
            if isinstance(w, Gtk.Button) and w.get_label() == label]


def _text(page):
    return "\n".join(_labels(page))


def _proposal_groups(page):
    return [w for w in _walk(page)
            if surface.PROPOSED_CSS in w.get_css_classes()]


def _press(page, label):
    found = _buttons(page, label)
    assert found, f"no {label!r} button; the page reads {_text(page)!r}"
    found[0].emit("clicked")
    return found[0]


# --- the section is there, and only when it has something to say ------------

def test_a_waiting_proposal_appears_with_both_answers(store, sleep_dir):
    page = _page(store)
    assert _buttons(page, surface.KEEP_LABEL), _text(page)
    assert _buttons(page, surface.DROP_LABEL)
    assert FACT in _text(page)


def test_the_row_says_which_turn_the_sentence_came_from(store, sleep_dir):
    """A proposal is something to agree *with*, and agreeing needs the context.

    `Candidate.source_index` is **zero-based** throughout consolidation, and the
    row is written as `turn N + 1` because a person counts turns from one. So
    the fixture's index 4 has to read "turn 5" - and this is the only test that
    could tell the right turn from the wrong one.
    """
    assert "turn 5" in _text(_page(store)), _text(_page(store))


def test_the_row_names_what_the_conversation_was_about(store, sleep_dir):
    said = _text(_page(store))
    assert "I moved to Pune in March" in said, said


def test_nothing_waiting_means_no_section_at_all(store, sleep_dir):
    """Silence, not a heading that says there is nothing.

    The panel's *fact list* must say "no facts remembered" because the panel is
    opened to ask. This section is not the thing being asked about, and a
    permanent heading would put a second answer on the page.

    The digest is rewritten with zero candidates rather than deleted, so the
    store is genuinely non-empty and the "nothing waiting" answer is the only
    thing under test.
    """
    consolidation.save_digest(_digest(candidates=[]), sleep_dir)
    page = _page(store)
    assert not _buttons(page, surface.KEEP_LABEL)
    assert surface.PROPOSED_TITLE not in _text(page)


# --- pressing the buttons --------------------------------------------------

def test_keep_stores_the_fact_where_the_panel_says_it_is_stored(
        store, sleep_dir):
    _press(_page(store), surface.KEEP_LABEL)
    assert any(FACT in line for line in _durable_lines(store)), \
        _durable_lines(store)


def test_keep_makes_the_fact_visible_in_the_list_below(store, sleep_dir):
    """The row disappears, so the list is the only place it can be confirmed."""
    _press(_page(store), surface.KEEP_LABEL)
    assert FACT in _text(_page(store))


def test_not_now_drops_it_and_stores_nothing(store, sleep_dir):
    _press(_page(store), surface.DROP_LABEL)
    assert _durable_lines(store) == []
    assert not _buttons(_page(store), surface.KEEP_LABEL)


def test_an_answered_proposal_is_not_offered_again_after_a_rebuild(
        store, sleep_dir):
    """The decision is written into the digest, so a fresh panel agrees.

    Read through `pending_proposals` rather than the panel, because that is the
    fact the durable part of the decision buys: a second panel - tomorrow, or
    after a restart - must not re-ask.
    """
    from shani_chronoa.senses.memory import pending_proposals

    _press(_page(store), surface.KEEP_LABEL)
    assert pending_proposals(sleep_dir) == []
    assert not _buttons(_page(store), surface.KEEP_LABEL)


# --- the answer is on screen, and says which answer ------------------------

def test_a_kept_answer_is_visible_rather_than_logged(store, sleep_dir):
    """A press whose row then disappears must say what it did.

    `refresh()` rebuilds every child, so a note living inside the answered row
    would vanish with it - and the person would be shown a proposal gone and
    nothing below it, which is how "kept" and "kept-unrecorded" become
    indistinguishable.
    """
    page = _page(store)
    _press(page, surface.KEEP_LABEL)

    notes = [w for w in _walk(page) if surface.NOTE_CSS in w.get_css_classes()]
    assert notes, _text(page)
    assert "Remembered" in " ".join(_labels(notes[0])), _text(page)
    assert surface.PROPOSED_ANSWERED_TITLE in _text(page)


def test_a_write_that_failed_is_said_to_have_failed(store, sleep_dir,
                                                    monkeypatch):
    """The distinction the whole outcome vocabulary exists for, on screen.

    This is the **over-fix guard** for the test above: if the note were a
    constant string, the one above would pass and this one would not.
    """
    from shani_chronoa.senses import memory

    monkeypatch.setattr(memory, "store_fact", lambda *a, **k: None)
    page = _page(store)
    _press(page, surface.KEEP_LABEL)

    said = _text(page)
    assert "NOT in your memory" in said, said
    assert "Remembered - it will be" not in said, said


def test_the_note_carries_the_reason_and_not_only_the_verdict(
        store, sleep_dir, monkeypatch):
    """`"NOT in your memory"` is a verdict; the reason is what a person acts on.

    The two are one row, and only one of them says *what to do next*: "the
    write was refused" is something to retry, "the store is turned off" is
    something to switch on, and a sentence with a colon and nothing after it
    is neither. Asserted as **both** - the prefix without the reason passes
    the test above, which is the over-fix this closes.
    """
    from shani_chronoa.senses import memory

    monkeypatch.setattr(memory, "store_fact", lambda *a, **k: None)
    page = _page(store)
    _press(page, surface.KEEP_LABEL)

    said = _text(page)
    assert "NOT in your memory" in said, said
    # The acceptor's own reason, not a second string the panel made up.
    assert "the write was refused" in said, said


def test_a_refused_decision_is_logged_not_only_shown(
        store, sleep_dir, monkeypatch, caplog):
    """The row survives the failure; the log is the only other record.

    Every test in this file presses a button and reads the page, so a handler
    that caught the exception and said nothing would pass all of them. What is
    left is the difference between a rare failure and one that is merely rare
    **and silent** - and a silent refusal is the shape this repository's own
    `_tool_ok` entry is about.
    """
    from shani_chronoa.senses import memory

    def _raise(*args, **kwargs):
        raise OSError("the file is read-only")

    monkeypatch.setattr(memory, "decide_candidate", _raise)
    with caplog.at_level("WARNING"):
        page = _page(store)
        _press(page, surface.KEEP_LABEL)

    assert "could not be recorded" in _text(page), _text(page)
    assert any("proposed fact" in record.message for record in caplog.records), (
        [r.message for r in caplog.records])


def test_a_sleep_pass_that_cannot_be_read_does_not_take_the_panel_down(
        store, sleep_dir, monkeypatch):
    """The panel is the surface whose job is to show what is held.

    An ImportError from the sense, raised *while the page is being built*, means
    a person cannot open Memory at all - so the read is guarded, and the guard
    returns "nothing is proposed" rather than a traceback. `pending_proposals`
    is imported inside the function precisely so the stub lands on the module
    attribute and not on a name bound at import.
    """
    from shani_chronoa.senses import memory

    def _cannot_read(*args, **kwargs):
        raise ImportError("no module named shani_chronoa.senses.consolidation")

    monkeypatch.setattr(memory, "pending_proposals", _cannot_read)
    page = _page(store)          # must not raise

    assert _proposal_groups(page) == [], _text(page)
    # ...and the panel is still a panel. Asked of the page's own text rather
    # than of a group marker: this store holds nothing, so there are no fact
    # groups to find and looking for one would assert a fixture, not the guard.
    assert "No facts remembered yet" in _text(page), _text(page)


def test_the_row_names_a_speaker_that_is_not_the_person(
        store, sleep_dir, monkeypatch):
    """`source_role` is real data, and only the user's own words are a quote.

    Sleep reads the whole conversation, so a candidate can be drawn from the
    assistant's side. For those, "said in turn 5" alone reads as the person
    having said it - which is the same sentence carrying two meanings, and the
    reason the role is appended when it is anything but `user`.
    """
    home = sleep_dir
    consolidation.save_digest(
        _digest(candidates=[consolidation.Candidate(
            FACT, 4, "assistant")]), home)

    page = _page(store)
    said = _text(page)
    assert "by assistant" in said, said
    # ...and the user's own words get no such tag, or every row would carry it.
    consolidation.save_digest(_digest(), home)
    assert "by user" not in _text(_page(store)), _text(_page(store))


def test_a_long_proposal_is_clipped_so_the_button_stays_on_screen(
        store, sleep_dir):
    """A proposal is a span out of a conversation and may be a paragraph.

    The row is the only place the sentence appears, and the buttons are inside
    it - so an unclipped one can be taller than the panel, and the control that
    answers it is pushed off the bottom of a page whose only job is that
    control. Clipped at 90 by `_clip`, so the assertion is on the shape rather
    than on the exact bound.
    """
    long_text = FACT + " and a whole paragraph of extra words " * 4
    consolidation.save_digest(_digest(text=long_text), sleep_dir)

    page = _page(store)
    # The **shortest** widget carrying the sentence. Every ancestor of the row
    # carries it too - `_labels` walks the subtree - so the first match was the
    # page, 1239 characters, and the first version of this test asserted
    # against the page's own size and could never have told clipped from not.
    rows = sorted((" ".join(_labels(w)) for w in _walk(page) if FACT in " ".join(_labels(w))),
                  key=len)
    assert rows, _text(page)
    shown = rows[0]
    assert len(shown) < len(long_text), (len(shown), len(long_text))
    # The module's own ellipsis, not three typed dots: `_clip` ends with U+2026
    # and a test asserting "..." would have failed on correct code.
    assert shown.endswith("\u2026"), shown
    # The buttons are still there, which is the point of clipping.
    assert _buttons(page, surface.KEEP_LABEL), _text(page)


def test_an_acceptor_that_raised_is_reported_as_not_recorded(
        store, sleep_dir, monkeypatch):
    """A refusal that raises must not take the window down, and must say so."""
    from shani_chronoa.senses import memory

    def _raise(*args, **kwargs):
        raise OSError("the file is read-only")

    monkeypatch.setattr(memory, "decide_candidate", _raise)
    page = _page(store)
    _press(page, surface.KEEP_LABEL)

    assert "could not be recorded" in _text(page), _text(page)


# --- consent ---------------------------------------------------------------

def test_with_consent_off_the_row_explains_and_offers_no_buttons(
        store, sleep_dir):
    """A Keep that can only ever refuse is a dead control."""
    ChronoaConfig().set("memory-sense-enabled", "false")
    page = _page(store)

    assert not _buttons(page, surface.KEEP_LABEL), _text(page)
    assert surface.PROPOSED_OFF_TITLE in _text(page)


def test_the_refusal_names_the_settings_row_not_the_key(store, sleep_dir):
    """67 of 92 gated refusals still name a gsettings key no screen spells."""
    ChronoaConfig().set("memory-sense-enabled", "false")
    said = _text(_page(store))
    assert "memory-sense-enabled" not in said, said
    assert "Remembered facts" in said, said


def test_consent_off_does_not_even_list_what_is_waiting(store, sleep_dir):
    """The over-fix guard for the two above.

    A section that listed the sentence and then explained it could not be
    answered would put the words on screen behind a button nobody can press.
    """
    ChronoaConfig().set("memory-sense-enabled", "false")
    assert FACT not in _text(_page(store))


# --- placement, and the shape the panel already holds to --------------------

def test_the_section_is_above_the_facts_because_it_is_the_only_action(
        store, sleep_dir):
    """It is the only thing on this page anyone has to *act* on.

    Below the fact groups it would be the last thing on a panel that is mostly
    things already settled, at the bottom of a scroller.
    """
    store.add(_fact("memory", "An older fact that was typed in"))
    said = _text(_page(store))
    assert said.index(surface.PROPOSED_TITLE) < said.index("An older fact")


def test_a_rebuild_does_not_stack_a_second_group(store, sleep_dir):
    """`refresh()` removes every child, so a stack-up would be a live defect.

    `routines.refresh()` shipped exactly this once, so it is pinned rather than
    assumed.
    """
    page = _page(store)
    page.refresh()
    page.refresh()

    assert len(_proposal_groups(page)) == 1, _proposal_groups(page)
    assert len(_buttons(page, surface.KEEP_LABEL)) == 1


def test_two_proposals_each_carry_their_own_buttons(store, sleep_dir):
    """A closure over the loop variable would give both rows the *last* proposal.

    Asserted on the file rather than on what is still on screen: pressing the
    first button puts that row's sentence into the fact list, so "it is on the
    page" cannot distinguish a correct press from one that kept the wrong
    sentence. The file can.
    """
    other = "The annex holds the build server"
    consolidation.save_digest(
        _digest("20260102-120000-bbcd", other, 2), sleep_dir)

    page = _page(store)
    assert len(_buttons(page, surface.KEEP_LABEL)) == 2, _text(page)

    # Which sentence is *first* is the product's answer, not a fixture's: the
    # listing is newest-first, and the digest written a moment ago is the newer
    # one. Asserting against `pending_proposals()` keeps this test honest if
    # that order ever changes.
    from shani_chronoa.senses.memory import pending_proposals

    wanted = pending_proposals(sleep_dir)[0].text

    _press(page, surface.KEEP_LABEL)
    held = _durable_lines(store)
    assert len(held) == 1, held
    assert wanted in held[0], (wanted, held)
    # ...and the *other* row was not what got stored, which is what a closure
    # over the loop variable would produce.
    untouched = FACT if wanted == other else other
    assert untouched not in held[0], (untouched, held)


# --- one function behind two doors ----------------------------------------

def test_the_panel_reaches_the_shared_acceptor_and_writes_nothing_itself():
    """Two doors, one function. The other door is the `review_proposals` skill.

    The check is on `_decide`'s own source rather than the module's, because
    `_remember()` reaches `remember_fact` - the typed-fact path, which predates
    this section and is a different question entirely.
    """
    decide = inspect.getsource(surface._MemorySurface._decide)
    assert "decide_candidate" in decide
    for absent in ("store_fact", "remember_fact", "PerceptStore", ".add("):
        assert absent not in decide, \
            f"_decide writes to the store itself ({absent}), which is a second path"


def test_the_check_above_can_distinguish_the_two_paths():
    """A control, because an absence means nothing if every path is absent."""
    typed = inspect.getsource(surface._MemorySurface._remember)
    assert "remember_fact" in typed, \
        "if the typed path stops calling remember_fact, the guard above is vacuous"


def test_both_doors_name_the_same_function():
    from tests._source import package_source

    panel = inspect.getsource(surface._MemorySurface._decide)
    skill = package_source("skills.review_proposals")
    assert "decide_candidate" in panel
    assert "decide_candidate" in skill