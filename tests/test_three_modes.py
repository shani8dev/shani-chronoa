"""Three modes, one control each: Plan, Auto, Build.

There were once four chips for two ideas and a duplicate in Settings. The strip
now carries exactly three, and each answers a different question:

| chip | question | enforced in |
|---|---|---|
| **Plan** | what would you do? | the dispatch path, and it asks |
| **Ask first / No questions** | will it stop me to ask? | the permission layer's refusal |
| **Build** | what is it working on? | which tools the model is shown |

**The second chip is called "Auto" in the table of the design and says
"No questions" on screen, and the difference is the point.** `DONT_ASK`
*refuses* what it cannot ask about; it does not act freely. "Auto" on a strip
invites the opposite reading, which this file's own
`test_auto_does_not_say_it_may_act_freely` exists to prevent - and the strip was
briefly shipping "Auto" as the chip's word, in a commit whose tests here were
already red, so nothing objected. `test_permission_posture_chip.py` holds the
word now; this file holds the three questions.

**The last of those is the weakest and is written as such.** Plan and Auto are
checks and refusals. Build changes which schemas reach the model - a strong
nudge, not a gate. Nothing refuses a general tool because Build is on, because
that would make a Build turn unable to open a browser and the failure would
arrive as "I cannot do that" rather than as the reason.

Two things this file exists to prevent, both of which happened: a chip meaning
two things at once, and an icon name the theme does not ship.
"""

import sys
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "usr/lib/shani-chronoa"))

import pytest  # noqa: E402

import gi  # noqa: E402

gi.require_version("Gtk", "4.0")
from gi.repository import Gtk  # noqa: E402

from shani_chronoa import buildmode, permissions, planmode, tools  # noqa: E402
from shani_chronoa.gui import window as window_module  # noqa: E402


class _Config:
    privacy_mode = False
    answer_mode = "local"
    wake_word_enabled = False
    barge_in_vad_enabled = False
    reply_style = "ordinary"

    def get_bool(self, key, default=False):
        return False


@pytest.fixture(autouse=True)
def _reset_modes():
    """Every test starts and ends at the plain state.

    **Autouse, because three tests leaked.** Build mode left on by one test made
    the next one read "Build" when it was checking what a *broken* config reads
    as - so a failure in an unrelated test presented as a failure in this file,
    and alphabetical ordering decided whether it happened at all.
    """
    _restore()
    yield
    _restore()


def _strip():
    app = SimpleNamespace(current_llm_kind=lambda: "local",
                          _answer_mode_problem="")
    return window_module._ModeStrip(app, _Config())


def _restore():
    buildmode.set_enabled(False)
    planmode.set_enabled(False)
    permissions.set_mode(permissions.Mode.DEFAULT)


def _tool_names():
    return {s["function"]["name"] for s in tools.TOOLS if "function" in s}


# -- three chips, three words, three questions ------------------------------


def test_the_strip_shows_the_modes_it_carries_and_nothing_else():
    """**Four chips moved off to the palette** - ambient, hands, reply style and
    the model - because they are set once per task, while the ones that stay are
    changed mid-conversation. Nine chips in a row is nine words to read past the
    one you came to change.

    Asserted as an exact count rather than "at most", because the crowding came
    from adding chips and a ceiling nobody checks drifts upwards.

    **This was 5, and it is 4 because `5ef5c62` moved Plan onto the composer.**
    That commit measured the strip at 97% full with the model switch having no
    home on screen, and carried the model, the reply style and Plan to
    `answer_button` beside Send. `_plan_chip` is still **built** and still drawn
    by `refresh()`, so a reader has no way to tell it moved - only this count
    could, and `5ef5c62` did not update this file. Where plan mode now lives is
    pinned by `tests/test_answer_control_is_beside_send.py`, which that commit
    added; the two tests together cover both ends of the move, where before this
    one asserted a strip that no longer exists.
    """
    # **3 since the 2026-10-14 redesign** (`83ab820`): one chip per question -
    # Work (what it works on, and whether it plans first), Permissions (risky
    # actions, and whether it acts on its own), Voice (mic button / wake word).
    # Presence and "where answers come from" moved into the model button under
    # the composer, beside the model they describe.
    strip = _strip()
    chips = [c for c in strip if isinstance(c, Gtk.MenuButton)]
    assert chips == [strip._work_chip, strip._permissions_chip, strip._hands_chip], (
        f"{len(chips)} chips on the strip; expected exactly Work, Permissions "
        f"and Voice, in that order.")
    # **Built, not appended** - the move has to stay visible as a move, or the
    # next reader assumes a chip nobody appended is dead code.
    assert strip._plan_chip is not None, (
        "plan mode's chip stopped being built; `refresh()` still draws it")


def test_each_mode_has_a_whole_number_of_words():
    """One word per chip. A mode strip is read from across the room; a chip
    saying "Read only" and another saying "Ask before risky calls" are read at
    different speeds."""
    for table in (window_module._ModeStrip._AUTO,
                  window_module._ModeStrip._PLAN,
                  window_module._ModeStrip._CAPABILITY):
        for row in table:
            assert 0 < len(row[1].split()) <= 2, row[1]


def test_the_three_chips_answer_three_different_questions():
    words = {row[1] for table in (window_module._ModeStrip._AUTO,
                                  window_module._ModeStrip._PLAN,
                                  window_module._ModeStrip._CAPABILITY)
             for row in table}
    assert words == {"Ask first", "No questions", "Working", "Plan", "General",
                     "Build"}, words


# -- Plan -------------------------------------------------------------------


def test_plan_mode_has_its_own_chip_and_reads_the_module():
    strip = _strip()
    try:
        planmode.set_enabled(False)
        strip.refresh()
        assert strip._plan_label.get_text() == "Working"
        planmode.set_enabled(True)
        strip.refresh()
        assert strip._plan_label.get_text() == "Plan"
    finally:
        _restore()


def test_planning_does_not_claim_the_machine_is_read_only():
    """They were one control once: "Read only" was plan mode. Now planning is a
    workflow and says so."""
    strip = _strip()
    try:
        buildmode.set_enabled(False)
        planmode.set_enabled(True)
        strip.refresh()
        assert strip._rules_label.get_text() == "General", (
            "entering plan mode made the strip claim a different subject")
    finally:
        _restore()


# -- Auto -------------------------------------------------------------------


def test_auto_is_a_permission_not_a_workflow():
    strip = _strip()
    try:
        permissions.set_mode(permissions.Mode.DONT_ASK)
        strip.refresh()
        assert strip._auto_label.get_text() == "No questions"
        permissions.set_mode(permissions.Mode.DEFAULT)
        strip.refresh()
        assert strip._auto_label.get_text() == "Ask first"
    finally:
        _restore()


def test_auto_does_not_say_it_may_act_freely():
    """The chip does not say "Auto", and the tooltip says *refused*.

    `DONT_ASK` refuses anything it cannot ask about. A reader who takes the
    word as "it will do as it likes" is wrong by the whole safety model - so the
    word is "No questions" and the row's sentence carries both halves: that
    risky calls are refused rather than asked about, and that this is not
    permission to act freely.
    """
    row = next(r for r in window_module._ModeStrip._AUTO if r[0] == "auto")
    assert "refus" in row[2].lower(), row[2]
    assert "not permission to act freely" in row[2].lower(), row[2]


def test_asking_is_the_quiet_posture():
    strip = _strip()
    try:
        permissions.set_mode(permissions.Mode.DEFAULT)
        strip.refresh()
        assert not strip._auto_chip.has_css_class("mode-chip-on")
    finally:
        _restore()


# -- Build ------------------------------------------------------------------


def test_build_mode_is_a_subject_not_a_restriction():
    """Build adds tools; it removes nothing. A mode that hid tools would make
    some questions unanswerable and the refusal would arrive as "I cannot do
    that"."""
    buildmode.set_enabled(True)
    try:
        assert buildmode.extra_tools(), "Build mode offered nothing"
        assert all(t in _tool_names() for t in buildmode.extra_tools()), (
            "Build names a tool that does not exist")
    finally:
        _restore()


def test_build_adds_file_tools_and_takes_nothing_away():
    buildmode.set_enabled(True)
    try:
        _assert_build_tools()
    finally:
        _restore()


def _assert_build_tools():
    assert len(buildmode.extra_tools()) >= 4, (
        "Build mode should guarantee more than one way to reach a file")
    for tool in ("read_document", "edit_file"):
        assert tool in buildmode.extra_tools(), tool


def test_build_off_contributes_nothing():
    """So a caller can add `extra_tools()` to `in_use` unconditionally rather
    than branching - and a caller that forgets the branch gets the ordinary
    behaviour rather than a crash."""
    buildmode.set_enabled(False)
    assert buildmode.extra_tools() == ()


def test_the_build_chip_reads_the_module():
    strip = _strip()
    try:
        buildmode.set_enabled(False)
        strip.refresh()
        assert strip._rules_label.get_text() == "General"
        buildmode.set_enabled(True)
        strip.refresh()
        assert strip._rules_label.get_text() == "Build"
    finally:
        _restore()


def test_build_says_it_may_still_use_other_tools():
    """Build is a nudge. A tooltip that implied a boundary the dispatch path
    does not enforce would be a lie with a hover."""
    row = next(r for r in window_module._ModeStrip._CAPABILITY
               if r[0] == "build")
    assert "still use anything else" in row[2], row[2]


def test_read_only_says_where_it_went():
    """It left the strip, and a person who remembers it should be able to find
    it rather than conclude it was deleted."""
    note = buildmode.read_only_note()
    assert "Settings" in note and "Privacy" in note, note


# -- the three combine ------------------------------------------------------


def test_all_three_can_be_on_at_once():
    strip = _strip()
    try:
        permissions.set_mode(permissions.Mode.DONT_ASK)
        planmode.set_enabled(True)
        buildmode.set_enabled(True)
        strip.refresh()
        assert strip._auto_label.get_text() == "No questions"
        assert strip._plan_label.get_text() == "Plan"
        assert strip._rules_label.get_text() == "Build"
    finally:
        _restore()


def test_settings_offers_exactly_the_two_postures_the_strip_offers():
    """Two controls for one axis, one of them offering a mode the strip cannot
    show, is the redundancy this replaced."""
    from shani_chronoa.app import ChronoaApplication

    app = ChronoaApplication.__new__(ChronoaApplication)
    assert tuple(app._PERMISSION_KEYS) == tuple(
        row[0] for row in window_module._ModeStrip._AUTO), (
        f"Settings accepts {app._PERMISSION_KEYS}, the strip offers "
        f"{[r[0] for r in window_module._ModeStrip._AUTO]}")


# -- icons ------------------------------------------------------------------


def test_every_mode_icon_is_one_the_theme_ships():
    """An icon name Adwaita lacks renders as an empty box that reads as a
    rendering bug. Measured over the whole theme: it ships no build, tool,
    hammer, wrench, construction or gear symbolic at all, so
    `system-build-symbolic` and `applications-development-symbolic` are Yaru
    names - and `emblem-default-symbolic`, which this strip used for one row,
    is one too."""
    theme = Path("/usr/share/icons/Adwaita")
    if not theme.is_dir():
        return
    for table in (window_module._ModeStrip._AUTO,
                  window_module._ModeStrip._PLAN,
                  window_module._ModeStrip._CAPABILITY):
        for row in table:
            if len(row) > 3 and row[3]:
                assert any(theme.rglob(f"{row[3]}.svg")), (
                    f"{row[1]} names {row[3]}, which Adwaita does not ship")