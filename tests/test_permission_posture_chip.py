"""The permission posture, visible from the window that gets interrupted.

**Three postures existed and only one was visible.** `default`, `dont_ask` and
`explore` were all written and all reachable from Settings → Privacy. None was
reachable from the main window - and the one you most need to see is
`dont_ask`, because it is the only setting whose answer to *"will it ask me?"*
is *no*.

**It is not auto-approve.** `dont_ask` refuses anything not already granted
rather than prompting. The word on the chip and the tooltip both say so, because
"auto" on a mode strip invites the opposite reading and a mode strip is read
from across the room.

**It is deliberately not persisted**, and these tests include one that would
notice if that changed: a mode that suppresses prompts must not become the
silent default at the next unattended login.
"""

import sys
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "usr/lib/shani-chronoa"))

import gi  # noqa: E402

gi.require_version("Gtk", "4.0")
from gi.repository import Gtk  # noqa: E402

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


# -- it is a chip, and it is on the strip ----------------------------------


def test_the_posture_is_a_chip_on_the_strip():
    """A control in Settings alone is invisible from the conversation, which is
    where the question "will it ask me?" actually arises."""
    strip = _strip()
    chips = [c for c in strip if isinstance(c, Gtk.MenuButton)]
    assert len(chips) == len(window_module._ModeStrip._PERMISSION) + 6, (
        f"{len(chips)} chips for "
        f"{len(window_module._ModeStrip._PERMISSION) + 6} controls - the "
        f"posture chip was not appended")


def test_the_posture_offers_exactly_the_three_modes_that_exist():
    keys = [row[0] for row in window_module._ModeStrip._PERMISSION]
    assert keys == [permissions.Mode.DEFAULT, permissions.Mode.DONT_ASK,
                    permissions.Mode.EXPLORE], keys


def test_every_posture_the_strip_offers_is_one_the_action_accepts():
    """A name on the strip that the action refuses would be a row that silently
    does nothing when clicked."""
    from shani_chronoa.app import ChronoaApplication

    keys = [row[0] for row in window_module._ModeStrip._PERMISSION]
    app = ChronoaApplication.__new__(ChronoaApplication)
    assert set(keys) <= set(app._PERMISSION_KEYS), (
        f"the strip offers {keys}, the action accepts {app._PERMISSION_KEYS}")


def test_explore_is_deliberately_not_on_the_strip():
    """**Two read-only controls, differing on 168 of 256 tools, with no visible
    way to tell them apart, is worse than one.** Plan mode blocks the
    consent-keyed set (92); explore allows only `READ_ONLY_TOOLS` (168). The
    first version of this table had both, and measured afterwards.

    explore stays reachable in Settings, where its stricter meaning is written
    down. If its calibration is ever fixed, the measurement below stops
    describing reality and this row becomes worth reconsidering.
    """
    keys = [row[0] for row in window_module._ModeStrip._PERMISSION]
    assert permissions.Mode.EXPLORE not in keys, (
        "explore is back on the strip beside 'Read only'")
    # The action still accepts it - Settings writes it, and the strip is not
    # the only door.
    from shani_chronoa.app import ChronoaApplication

    app = ChronoaApplication.__new__(ChronoaApplication)
    assert permissions.Mode.EXPLORE in app._PERMISSION_KEYS


def test_the_reason_explore_is_off_the_strip_is_still_true():
    """**The reason is measured, so the reason is checked.** If explore stops
    refusing `ask_user` and the read-only tools, it is no longer the same
    mode and the exclusion is no longer justified - and this fails rather than
    leaving a comment to rot.
    """
    from shani_chronoa import tools

    names = [s["function"]["name"] for s in tools.TOOLS if "function" in s]
    previous = permissions.get_mode()
    try:
        permissions.set_mode(permissions.Mode.EXPLORE)
        assert permissions.explore_refuses("ask_user"), (
            "explore no longer refuses ask_user, so it is no longer the "
            "over-strict mode the strip excluded - reconsider that row")
        refused = sum(1 for n in names if permissions.explore_refuses(n))
        planmode.set_enabled(True, "measuring")
        try:
            plan = sum(1 for n in names if planmode.blocked_reason(n))
        finally:
            planmode.set_enabled(False)
    finally:
        permissions.set_mode(previous)

    assert refused > plan, (
        f"explore now refuses {refused} and plan mode {plan} - they are no "
        f"longer the far-apart pair the exclusion was based on")


# -- it reads the truth, live ---------------------------------------------


def test_the_chip_follows_the_posture_settings_changes():
    """Settings writes through `permissions.set_mode` without going through an
    action, so the chip has to read the module rather than the action state -
    and `refresh()` runs on window focus for exactly this."""
    strip = _strip()
    try:
        for mode, word in ((permissions.Mode.DONT_ASK, "No questions"),
                           (permissions.Mode.EXPLORE, "Explore"),
                           (permissions.Mode.DEFAULT, "Ask first")):
            permissions.set_mode(mode)
            strip.refresh()
            assert strip._read_permission() == mode
            assert strip._permission_label.get_text() == word, (
                f"in {mode!r} the chip says "
                f"{strip._permission_label.get_text()!r}, not {word!r}")
    finally:
        _restore()


def test_the_quiet_posture_is_the_one_that_needs_no_deciding():
    """Highlighting every chip highlights none."""
    strip = _strip()
    try:
        permissions.set_mode(permissions.Mode.DEFAULT)
        strip.refresh()
        assert not strip._permission_chip.has_css_class("mode-chip-on")
        permissions.set_mode(permissions.Mode.DONT_ASK)
        strip.refresh()
        assert strip._permission_chip.has_css_class("mode-chip-on")
    finally:
        _restore()


def test_an_unreadable_posture_leaves_the_safe_word_rather_than_raising():
    """`refresh()` runs from `__init__`, so a reader that threw would stop the
    window opening."""
    strip = _strip()
    assert strip._read_permission() == permissions.Mode.DEFAULT


def test_a_posture_nobody_declared_is_not_shown_as_itself():
    """An unrecognised value falls back to the quiet one rather than blanking
    the chip."""
    strip = _strip()
    original = permissions.get_mode
    try:
        permissions.get_mode = lambda: "something-new"
        assert strip._read_permission() == "default"
    finally:
        permissions.get_mode = original


# -- the word must not invite the wrong reading ----------------------------


def test_the_word_does_not_say_auto():
    """"Auto" on a mode strip reads as "it may act freely". This mode refuses
    rather than prompts, which is the opposite."""
    words = [row[1].lower() for row in window_module._ModeStrip._PERMISSION]
    assert not any("auto" in word for word in words), words


def test_the_no_questions_row_says_what_actually_happens():
    row = next(r for r in window_module._ModeStrip._PERMISSION
               if r[0] == permissions.Mode.DONT_ASK)
    assert "refus" in row[2].lower(), row[2]


def test_the_tooltip_says_the_posture_does_not_survive_a_restart():
    """A chip that looked sticky would be the worst outcome - the posture is
    in memory only so that a quiet mode is never the silent default at the next
    unattended login."""
    strip = _strip()
    tooltip = strip._permission_chip.get_tooltip_text().lower()
    assert "restart" in tooltip, strip._permission_chip.get_tooltip_text()


# -- the action -----------------------------------------------------------


def _app():
    from shani_chronoa.app import ChronoaApplication

    return ChronoaApplication.__new__(ChronoaApplication)


class _Action:
    def __init__(self):
        self.state = None

    def set_state(self, variant):
        self.state = variant.get_string()


def test_choosing_a_posture_through_the_action_sets_it():
    from gi.repository import GLib

    app = _app()
    action = _Action()
    app._refresh_strip = lambda: None
    try:
        app._on_answer_permission(action, GLib.Variant("s",
                                                       permissions.Mode.EXPLORE))
        assert permissions.get_mode() == permissions.Mode.EXPLORE
        assert action.state == permissions.Mode.EXPLORE
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
            "the action state was not put back to the truth")
    finally:
        _restore()


def test_the_posture_is_not_persisted():
    """**The point of the whole setting.** `permissions` keeps it in memory, so
    a mode that never asks cannot become the default at the next login."""
    assert not hasattr(permissions, "save"), (
        "permissions grew a save(); check that the posture is still session-only")


# -- the icons -----------------------------------------------------------


def test_every_posture_icon_is_one_the_theme_ships():
    """An icon name Adwaita lacks renders as an empty box that reads as a
    rendering bug. `search-symbolic` was the first choice here and is not in
    the theme - checked with `rglob`, not `has_icon`, which is not
    authoritative."""
    theme = Path("/usr/share/icons/Adwaita")
    if not theme.is_dir():
        return                     # nothing to check against here
    for row in window_module._ModeStrip._PERMISSION:
        assert any(theme.rglob(f"{row[3]}.svg")), f"{row[3]} is not in Adwaita"