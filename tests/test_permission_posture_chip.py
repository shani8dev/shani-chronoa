"""The permission posture, visible from the window that gets interrupted.

**Three postures existed and only one was visible.** `default`, `dont_ask` and
`explore` were all written and all reachable from Settings → Privacy. None was
reachable from the main window - and the one you most need to see is
`dont_ask`, because it is the only setting whose answer to *"will it ask me?"*
is *no*.

**It is not auto-approve.** `dont_ask` refuses anything not already granted
rather than prompting. The word on the chip says so, because "Auto" on a mode
strip reads as *it may act freely* - which is the opposite of what the mode
does - and a mode strip is read from across the room. **That word regressed**
when the strip was redesigned, inside a commit whose own tests were already red;
it is `test_the_word_does_not_say_auto` below that holds it now.

**It is deliberately not persisted**, and these tests include one that would
notice if that changed: a mode that suppresses prompts must not become the
silent default at the next unattended login.

### This file was red for most of its life, and that is why it looks defensive

At `c9ea23e`, where it was written, it was **7 failed / 9 passed**. It read
`_ModeStrip._PERMISSION`; the redesign renamed that to `_AUTO` and moved four
more names with it. **Because the file was already failing, nothing objected**
to a commit that renamed a table and put the word "Auto" back on the chip.

And it **contradicted itself**: one test asserted the strip offered
`[DEFAULT, DONT_ASK, EXPLORE]` while another asserted `EXPLORE` was *not* on it.
Two tests, one file, opposite claims - which is exactly how a red file hides a
question nobody has to answer. The measurement below answers it.

### There are two vocabularies here, and the tests hold both

| | words |
|---|---|
| the strip and the action | `ask`, `auto` |
| `permissions` | `default`, `dont_ask`, `explore` |

The chip says what a person decides; the module says what an implementer looks
up. The first version checked the chip's keys against the module's names, so
`ask` and `auto` were both **refused** and the chip did nothing - which is why
`_PERMISSION_KEYS` exists and why both sides are asserted here rather than one.
"""

import sys
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "usr/lib/shani-chronoa"))

from shani_chronoa import permissions, planmode  # noqa: E402
from shani_chronoa.gui import window as window_module  # noqa: E402


class _Config:
    privacy_mode = False
    answer_mode = "local"
    wake_word_enabled = False
    barge_in_vad_enabled = False
    reply_style = "ordinary"

    def get_bool(self, key, default=False):
        return False


def _strip():
    app = SimpleNamespace(current_llm_kind=lambda: "local",
                          _answer_mode_problem="")
    return window_module._ModeStrip(app, _Config())


def _restore():
    permissions.set_mode(permissions.Mode.DEFAULT)


def _chip_words():
    return [row[1].lower() for row in window_module._ModeStrip._AUTO]


def _app():
    from shani_chronoa.app import ChronoaApplication

    return ChronoaApplication.__new__(ChronoaApplication)


class _Action:
    def __init__(self):
        self.state = None

    def set_state(self, variant):
        self.state = variant.get_string()


# -- it is a chip, and it is on the strip ----------------------------------


def test_the_posture_is_a_chip_on_the_strip():
    """A control in Settings alone is invisible from the conversation, which is
    where the question "will it ask me?" actually arises.

    **Asserted as a parent, not as a count.** The first version said
    `len(_PERMISSION) + 6`, and the `6` was the number of *other* dropdowns at
    the time; the second counted the strip's tables and found eight against five
    chips, which looked like a missing chip and was not - **Ambient, hands-free
    and reply style are drawn by `refresh()` and reached from the Ctrl+K
    palette, and are deliberately not appended to the strip.** Both versions
    were counting something that is not the property.

    The property is one line and does not go stale: the chip the file is about is
    a child of the strip, and the table it is drawn from is one the strip draws.
    """
    strip = _strip()
    assert strip._auto_chip is not None
    assert strip._auto_chip.get_parent() is strip, (
        "the posture chip is built but not on the strip, so the control exists "
        "in a way nobody can reach")
    assert window_module._ModeStrip._AUTO, "the posture table is empty"


def test_the_posture_offers_exactly_the_two_that_are_peers():
    """**This test said "three" and claimed `EXPLORE` was one of them, while the
    test below asserted it was not.** They could not both be true, which is how a
    red file hides a contradiction nobody has to resolve.

    The measurement resolves it. `permissions` has three postures; the strip
    offers the two that mean *"will it interrupt me"* - ask first, or never ask
    and refuse instead. `EXPLORE` answers *"what may it change"*, which is a
    different question and already has its own chip. So the honest count is
    **two**, and it is asserted because a third row here would be a second
    control answering a question the strip already answers.
    """
    keys = [row[0] for row in window_module._ModeStrip._AUTO]
    assert keys == ["ask", "auto"], keys


def test_every_key_the_strip_offers_is_one_the_action_accepts():
    """A name on the strip that the action refuses is a row that silently does
    nothing when clicked - and that is not hypothetical: the first version
    checked the chip's keys against `permissions`' *own* names, so both rows
    were refused and the chip did nothing at all.
    """
    keys = [row[0] for row in window_module._ModeStrip._AUTO]
    app = _app()
    assert set(keys) <= set(app._PERMISSION_KEYS), (
        f"the strip offers {keys}, the action accepts {app._PERMISSION_KEYS}")


def test_both_strip_keys_reach_a_real_permission_mode():
    """The two vocabularies have to meet, and this is where they do.

    `ask` -> `default` and `auto` -> `dont_ask`. Driven through the real
    handler rather than a table read, because the table is the thing that was
    wrong once already.
    """
    from gi.repository import GLib

    for key, mode in (("ask", permissions.Mode.DEFAULT),
                      ("auto", permissions.Mode.DONT_ASK)):
        app = _app()
        action = _Action()
        app._refresh_strip = lambda: None
        try:
            app._on_answer_permission(action, GLib.Variant("s", key))
            assert permissions.get_mode() == mode, (
                f"{key!r} left the posture at {permissions.get_mode()!r}")
        finally:
            _restore()


def test_explore_is_deliberately_not_on_the_strip():
    """**Two read-only controls differing on most of the catalogue, with no
    visible way to tell them apart, is worse than one.** Measured now: explore
    refuses **165** of 256 tools and plan mode blocks **130**, and **50** of the
    tools explore stops are ones plan mode allows - so they are not two settings
    on one axis, they are two different questions that once shared a word.

    explore stays reachable in Settings, where its stricter meaning is written
    down.
    """
    keys = [row[0] for row in window_module._ModeStrip._AUTO]
    assert "explore" not in keys, (
        "explore is back on the strip beside the capability chip")


def test_the_reason_explore_is_off_the_strip_is_still_true():
    """**The reason is measured, so the reason is checked.** If explore stops
    being the far stricter of the pair, the exclusion stops being justified and
    this fails rather than leaving a comment to rot.

    **What is *not* asserted any more, and why.** The original reason was that
    explore also refused `ask_user`, so a mode that cannot ask a question cannot
    answer one. `permissions.explore_refuses` now honours `TALKS_ONLY`, so
    `ask_user` is **allowed** - which is a fix, not a regression, and it left
    this test failing with *"reconsider that row"* against a better mode. The
    claim is now the one that still holds and is still load-bearing: explore is
    far stricter than plan mode, and they overlap without being nested.
    """
    from shani_chronoa import tools

    names = [s["function"]["name"] for s in tools.TOOLS if "function" in s]
    previous = permissions.get_mode()
    try:
        permissions.set_mode(permissions.Mode.EXPLORE)
        refused = sum(1 for n in names if permissions.explore_refuses(n))
        explore_only = {n for n in names if permissions.explore_refuses(n)}
        planmode.set_enabled(True, "measuring")
        try:
            plan = sum(1 for n in names if planmode.blocked_reason(n))
            plan_only = {n for n in names if planmode.blocked_reason(n)}
        finally:
            planmode.set_enabled(False)
    finally:
        permissions.set_mode(previous)

    assert refused > plan * 1.2, (
        f"explore now refuses {refused} and plan mode blocks {plan} - they are "
        f"no longer the far-apart pair the exclusion was based on")
    assert explore_only - plan_only, (
        "explore refuses nothing that plan mode allows, so the two have become "
        "the same question and one of them should be removed")


# -- it reads the truth, live ---------------------------------------------


def test_the_chip_follows_the_posture_settings_changes():
    """Settings writes through `permissions.set_mode` without going through an
    action, so the chip has to read the module rather than the action state -
    and `refresh()` runs on window focus for exactly this.

    **Set with the module's names and read back with the chip's.** The first
    version passed `"auto"` to `permissions.set_mode`, which raised
    `ValueError: unknown permission mode: 'auto'` - the two vocabularies are
    different on purpose and a test that blurs them hides the mapping.
    """
    strip = _strip()
    try:
        for mode, key, word in (
                (permissions.Mode.DONT_ASK, "auto", "No questions"),
                (permissions.Mode.DEFAULT, "ask", "Ask first")):
            permissions.set_mode(mode)
            strip.refresh()
            assert strip._read_auto() == key, (
                f"in {mode!r} the chip reads {strip._read_auto()!r}")
            assert strip._auto_label.get_text() == word, (
                f"in {mode!r} the chip says "
                f"{strip._auto_label.get_text()!r}, not {word!r}")
    finally:
        _restore()


def test_the_quiet_posture_is_the_one_that_needs_no_deciding():
    """Highlighting every chip highlights none."""
    strip = _strip()
    try:
        permissions.set_mode(permissions.Mode.DEFAULT)
        strip.refresh()
        assert not strip._auto_chip.has_css_class("mode-chip-on")
        permissions.set_mode(permissions.Mode.DONT_ASK)
        strip.refresh()
        assert strip._auto_chip.has_css_class("mode-chip-on")
    finally:
        _restore()


def test_an_unreadable_posture_leaves_the_safe_word_rather_than_raising():
    """`refresh()` runs from `__init__`, so a reader that threw would stop the
    window opening."""
    strip = _strip()
    assert strip._read_auto() == "ask"


def test_a_posture_nobody_declared_reads_as_the_permissive_one():
    """An unrecognised value falls back to the *asking* row rather than to the
    one that refuses.

    **The direction matters and it is the safe one.** The original fallback was
    `"default"`, which was both a different vocabulary and the same meaning; a
    reader returning `auto` for a value it does not recognise would claim the
    assistant will not ask.
    """
    strip = _strip()
    original = permissions.get_mode
    try:
        permissions.get_mode = lambda: "something-new"
        assert strip._read_auto() == "ask", (
            "an unrecognised posture is being reported as the refusing one")
    finally:
        permissions.get_mode = original


# -- the word must not invite the wrong reading ----------------------------


def test_the_word_does_not_say_auto():
    """"Auto" on a mode strip reads as "it may act freely". This mode **refuses**
    rather than prompts, which is the opposite.

    **This is the test the redesign failed.** The table moved to `_AUTO` and the
    row kept the word, and this assertion could not run because it looked for
    `_PERMISSION` - so the word that names the mode backwards shipped inside a
    commit whose own tests were already red.
    """
    words = _chip_words()
    assert not any("auto" in word for word in words), words
    assert "no questions" in words, words


def test_the_no_questions_row_says_what_actually_happens():
    row = next(r for r in window_module._ModeStrip._AUTO if r[0] == "auto")
    assert "refus" in row[2].lower(), row[2]


def test_the_tooltip_says_the_posture_does_not_survive_a_restart():
    """A chip that looked sticky would be the worst outcome - the posture is in
    memory only so that a quiet mode is never the silent default at the next
    unattended login.

    **Driven into the refusing row rather than read off the default one.** The
    first version built a fresh strip and read its tooltip, which showed the
    *Ask first* row - and that row has no reason to mention a restart, because
    it does not suppress anything. The claim belongs to the row that suppresses
    prompts, so the posture is set first.
    """
    strip = _strip()
    try:
        permissions.set_mode(permissions.Mode.DONT_ASK)
        strip.refresh()
        tooltip = strip._auto_chip.get_tooltip_text().lower()
        assert "restart" in tooltip, strip._auto_chip.get_tooltip_text()
    finally:
        _restore()


# -- the action -----------------------------------------------------------


def test_choosing_a_posture_through_the_action_sets_it():
    from gi.repository import GLib

    app = _app()
    action = _Action()
    app._refresh_strip = lambda: None
    try:
        app._on_answer_permission(action, GLib.Variant("s", "auto"))
        assert permissions.get_mode() == permissions.Mode.DONT_ASK
        assert action.state == permissions.Mode.DONT_ASK
    finally:
        _restore()


def test_explore_is_refused_by_this_action_on_purpose():
    """**Settings reaches explore by writing `permissions.set_mode` directly**,
    so the action refusing it costs nothing - and the refusal is the point: a
    key the action accepts but does not offer is a mode with no control, which
    is what `_PERMISSION_KEYS`' own docstring says happened once.

    The first version of this test asserted the opposite, having read the
    handler's mapping table and seen `explore` in it. **A mapping entry is not
    reachable code** - the key is rejected before the lookup - so the table
    carried a lie about a feature, and the test was green on the lie.
    """
    from gi.repository import GLib

    app = _app()
    action = _Action()
    app._refresh_strip = lambda: None
    try:
        app._on_answer_permission(action, GLib.Variant("s", "explore"))
        assert permissions.get_mode() == permissions.Mode.DEFAULT, (
            "the action set a posture its own key list does not contain")
        assert "explore" not in app._PERMISSION_KEYS
    finally:
        _restore()


def test_an_unknown_posture_is_refused_and_leaves_the_mode_alone():
    """A typo in a safety mode must never silently mean the permissive one."""
    from gi.repository import GLib

    app = _app()
    action = _Action()
    app._refresh_strip = lambda: None
    permissions.set_mode(permissions.Mode.EXPLORE)
    try:
        app._on_answer_permission(action, GLib.Variant("s", "yolo"))
        assert permissions.get_mode() == permissions.Mode.EXPLORE, (
            "an unrecognised mode changed the posture")
        assert action.state == permissions.Mode.EXPLORE, (
            "and the action reported a mode nobody chose")
    finally:
        _restore()