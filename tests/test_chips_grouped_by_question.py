"""The main window's controls, one per question, measured on a built window.

**What this replaced (2026-10-14).** Eleven controls answered four questions from
three places: the mode strip held General/Build, Ask first, Here/Drowsy/Asleep and
Nothing leaves...Cloud; a button beside Send held plan mode and the reply style;
"Answers with" sat on a row of its own; the background-rules and voice chips were
built and never shown. "Which model" was offered twice, a few pixels apart.

Now each question has one home:

    strip     Work (works on + plan first) · Permissions (risky + on its own) · Voice
    composer  Answers with (where + which model + in memory) · Thinking (+ reply style)

Every assertion reads the **built** widget - parents, menu targets, action names -
because the defects this layout replaced were all invisible to source greps: a
chip appended nowhere, a menu row firing a toggle that turned plan mode *off*, a
tray item naming an action that does not exist.
"""
import sys
from pathlib import Path

import pytest

gi = pytest.importorskip("gi")
gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gtk  # noqa: E402

Adw.init()
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "usr" / "lib" / "shani-chronoa"))

from shani_chronoa.gui.window import ChronoaWindow, _ModeStrip  # noqa: E402


@pytest.fixture(scope="module")
def window():
    app = Gtk.Application(application_id="test.chronoa.chips")
    return ChronoaWindow(app)


def _children(widget):
    out, child = [], widget.get_first_child()
    while child is not None:
        out.append(child)
        child = child.get_next_sibling()
    return out


def _descendants(widget):
    out = []
    for child in _children(widget):
        out.append(child)
        out.extend(_descendants(child))
    return out


def _rows(menu):
    rows = []
    for j in range(menu.get_n_items()):
        action = menu.get_item_attribute_value(j, "action", None)
        if action is None:
            continue
        target = menu.get_item_attribute_value(j, "target", None)
        rows.append((menu.get_item_attribute_value(j, "label", None).get_string(),
                     action.get_string(), target.get_string() if target else None))
    return rows


def _sections(button):
    """`{heading: [(label, action, target)]}` read off a button's menu model.

    A flat menu (no sections) comes back under the heading `""`.
    """
    model = button.get_menu_model()
    out = {}
    for i in range(model.get_n_items()):
        heading = model.get_item_attribute_value(i, "label", None)
        sub = model.get_item_link(i, "section")
        if sub is None:
            out.setdefault("", []).extend(_rows(model)[i:i + 1])
            continue
        rows = []
        for j in range(sub.get_n_items()):
            target = sub.get_item_attribute_value(j, "target", None)
            rows.append((sub.get_item_attribute_value(j, "label", None).get_string(),
                         sub.get_item_attribute_value(j, "action", None).get_string(),
                         target.get_string() if target else None))
        out[heading.get_string() if heading else ""] = rows
    return out


def _is_inside(widget, ancestor):
    node = widget
    while node is not None:
        if node is ancestor:
            return True
        node = node.get_parent()
    return False


# --- the strip: three chips, one question each --------------------------------

def test_the_strip_holds_work_permissions_and_voice_in_that_order(window):
    strip = window._mode_strip
    assert _children(strip) == [strip._work_chip, strip._permissions_chip,
                                strip._hands_chip], [type(c).__name__ for c in _children(strip)]


def test_the_single_setting_chips_are_built_but_not_shown(window):
    strip = window._mode_strip
    for name in ("_rules_chip", "_auto_chip", "_plan_chip", "_presence_chip",
                 "_answer_chip", "_ambient_chip", "_style_chip", "_model_chip"):
        assert getattr(strip, name).get_parent() is None, (
            f"{name} is on screen again - a second control for a setting the "
            f"grouped chips already carry")


def test_the_work_chip_carries_what_it_works_on_and_plan_first(window):
    menu = _sections(window._mode_strip._work_chip)
    assert [(a, t) for _l, a, t in menu["Works on"]] == [
        ("app.answer-rules", key) for key, *_ in _ModeStrip._CAPABILITY]
    # **Targets, not a bare toggle.** Two rows firing `toggle-plan-mode` would
    # turn plan mode OFF when "Plan first" is pressed while it is already on.
    assert [(a, t) for _l, a, t in menu["Before acting"]] == [
        ("app.answer-plan", "working"), ("app.answer-plan", "plan")]


def test_the_permissions_chip_carries_risky_actions_and_acting_alone(window):
    menu = _sections(window._mode_strip._permissions_chip)
    assert {a for _l, a, _t in menu["Risky actions"]} == {"app.answer-permission"}
    assert [t for _l, _a, t in menu["Risky actions"]] == [k for k, *_ in _ModeStrip._AUTO]
    assert [t for _l, a, t in menu["On its own"] if a == "app.answer-ambient"] == [
        k for k, *_ in _ModeStrip._AMBIENT]


def test_the_voice_chip_does_not_share_the_dictate_glyph(window):
    """Two identical microphones side by side meant two different things."""
    off_icon = _ModeStrip._HANDS[0][3]
    assert off_icon != window._dictate_button.get_icon_name(), off_icon


# --- the composer: the message's own controls ---------------------------------

def test_the_composer_card_holds_the_field_and_the_toolbar(window):
    card = window._composer_card
    assert _is_inside(window._input_entry, card)
    for name in ("_send_button", "_attach_button", "_dictate_button",
                 "_model_picker_button", "_thinking_picker_button"):
        assert _is_inside(getattr(window, name), card), f"{name} left the composer card"


def test_dictate_has_a_button_and_it_fires_dictate(window):
    assert window._dictate_button.get_action_name() == "app.dictate"


def test_the_model_popover_carries_where_it_answers_and_memory(window):
    picker = window._model_picker_button.get_popover()
    checks = [w for w in _descendants(picker) if isinstance(w, Gtk.CheckButton)]
    by_action = {}
    for check in checks:
        by_action.setdefault(check.get_action_name(), []).append(
            check.get_action_target_value().get_string())
    assert by_action.get("app.answer-egress") == [k for k, *_ in _ModeStrip._EGRESS]
    assert by_action.get("app.answer-presence") == [k for k, *_ in _ModeStrip._PRESENCE]
    # The menu's words are its own, but they must cover the strip's keys.
    assert [k for k, *_ in ChronoaWindow._PRESENCE_WORDS] == [k for k, *_ in _ModeStrip._PRESENCE]


def test_the_thinking_menu_carries_the_reply_style(window):
    menu = _sections(window._thinking_picker_button)
    assert [a for _l, a, _t in menu["How hard it thinks"]] == ["app.thinking"] * 6
    assert [t for _l, _a, t in menu["How it replies"]] == [k for k, *_ in _ModeStrip._STYLES]


def test_no_control_picks_a_model_except_the_answers_with_button(window):
    """The duplicate this redesign started from: a second model picker."""
    for widget in _descendants(window):
        if isinstance(widget, Gtk.MenuButton) and widget.get_menu_model() is not None:
            for rows in _sections(widget).values():
                assert all(a != "answer-model" for _l, a, _t in rows), (
                    f"a menu on screen picks a model: {rows}")


# --- the conversation gets the height ---------------------------------------

def test_the_orb_goes_compact_with_a_conversation_and_back_without(window):
    orb = window._orb
    window.add_user_turn("hello")
    assert "orb-compact" in orb.get_css_classes()
    window.clear_transcript()
    assert "orb-compact" not in orb.get_css_classes()


def test_idle_organ_lights_are_icons_only(window):
    labels = window._organ_strip._labels
    assert labels and not any(label.get_visible() for label in labels.values()), (
        "an idle organ shows its caption, which wrapped the strip onto a second row")


def test_the_minimap_overrides_the_button_minimum_width():
    """`set_size_request(3, -1)` is a floor; Adwaita's button minimum is wider."""
    from shani_chronoa.gui import style
    src = Path(style.__file__).read_text(encoding="utf-8")
    block = src[src.index(".minimap-tick {"):]
    block = block[:block.index("}")]
    assert "min-width: 3px" in block, block


# --- the two bugs found on the way --------------------------------------------

def test_the_tray_settings_item_names_a_registered_action():
    """The item named `app.settings`, which is not an action: GIO drops it."""
    from shani_chronoa.app import tray
    actions = [a for _l, a, _t in _rows(tray._menu(None))]
    assert "app.open-settings" in actions, actions
    assert "app.settings" not in actions, actions


def test_the_mic_button_row_does_not_promise_a_hold():
    off = next(row for row in _ModeStrip._HANDS if row[0] == "off")
    assert "hold" not in off[2].lower(), off[2]


# --- the voice states, the status line, the first frame -------------------------

def test_an_armed_wake_word_says_so_on_the_state_line(window, monkeypatch):
    from shani_chronoa.gui.widgets import AssistantState
    monkeypatch.setattr(window._mode_strip, "_read_hands_free", lambda: "wake")
    window.set_state(AssistantState.IDLE)
    window._sync_from_state()
    phrase = window._config.wake_phrase
    assert window._state_label.get_label() == f"Ready - say “{phrase}”"
    monkeypatch.setattr(window._mode_strip, "_read_hands_free", lambda: "off")
    window._sync_from_state()
    assert window._state_label.get_label() == AssistantState.IDLE.label


def test_dictation_is_not_drawn_as_ordinary_listening(window, monkeypatch):
    from shani_chronoa.gui.widgets import AssistantState
    window.set_state(AssistantState.LISTENING)
    monkeypatch.setattr(window, "_is_dictating", lambda: True)
    window._sync_from_state()
    assert window._state_label.get_label() == "Dictating…"
    assert window._dictate_button.get_tooltip_text().startswith("Stop dictating")
    monkeypatch.setattr(window, "_is_dictating", lambda: False)
    window._sync_from_state()
    assert window._state_label.get_label() == AssistantState.LISTENING.label
    window.set_state(AssistantState.IDLE)


@pytest.mark.parametrize("model,expected", [
    ("local", "Answering on this computer"),
    ("qwen3-1.7b", "Answering on this computer: qwen3-1.7b"),
])
def test_the_status_line_does_not_repeat_ready(model, expected):
    """The state line already says Ready; this line says where answers come from."""
    from shani_chronoa.app.brain import BrainMixin

    class _LLM:
        local = True

        def __init__(self):
            self.model = model

        def is_available(self):
            return True

    holder = type("Holder", (), {"llm": _LLM()})()
    assert BrainMixin._llm_status_text(holder) == expected


def test_a_narrow_window_is_collapsed_before_its_first_frame():
    """Before allocation the layout uses the default size, so the first frame
    is not the uncollapsed 665px split clipped to 460px."""
    app = Gtk.Application(application_id="test.chronoa.firstframe")
    fresh = ChronoaWindow(app)
    assert fresh.get_width() == 0, "the window is already allocated; the test proves nothing"
    fresh._apply_narrow_layout()
    assert fresh._split.get_collapsed()


def test_the_message_field_keeps_a_line_when_height_is_short(window):
    assert window._input_entry.get_size_request()[1] >= 32


# --- Models panel: "Use it" on a speech card ----------------------------------

class _Recorder:
    def __init__(self):
        self.writes = {}

    def set(self, key, value):
        self.writes[key] = value


@pytest.mark.parametrize("key,expected", [
    ("parakeet:tdt-0.6b-v2", {"stt-backend": "parakeet"}),
    ("base.en", {"whisper-model": "base.en", "stt-backend": "whisper"}),
])
def test_use_it_on_a_speech_card_selects_the_engine_it_names(key, expected):
    """A Parakeet card wrote `whisper-model = "parakeet:..."` and left the backend
    on whisper: nothing that listens changed, and whisper was handed a name it
    cannot load."""
    from shani_chronoa.gui.surfaces import models
    app = type("App", (), {"config": _Recorder()})()
    models._pick(app, {"kind": "stt", "key": key})
    assert app.config.writes == expected, app.config.writes


# --- Diff panel: side by side ---------------------------------------------------

def test_split_rows_pair_a_change_and_number_the_old_side():
    from shani_chronoa.gui.surfaces.diff import compute_file_diff, split_rows
    before = "a\nb\nc\nd\n"
    after = "a\nB1\nB2\nc\n"           # b -> B1,B2 (change + insert), d removed
    hunks = compute_file_diff(Path("x.txt"), before, after).hunks
    rows = [r for h in hunks for r in split_rows(h)]
    kinds = [kind for _l, _r, kind in rows]
    assert ("change" in kinds and "insert" in kinds and "delete" in kinds), rows
    change = next(r for r in rows if r[2] == "change")
    assert change[0] == (2, "b") and change[1] == (2, "B1"), change
    insert = next(r for r in rows if r[2] == "insert")
    assert insert[0] is None and insert[1] == (3, "B2"), insert
    delete = next(r for r in rows if r[2] == "delete")
    assert delete[0] == (4, "d") and delete[1] is None, delete


# --- the project folder on the Work chip ------------------------------------------

def test_the_work_chip_names_the_project_in_build_mode(window, monkeypatch):
    from shani_chronoa import buildmode
    strip = window._mode_strip
    monkeypatch.setattr(strip, "_read_capability", lambda: "build")
    buildmode.set_enabled(True)
    buildmode.set_project("/home/x/src/rocket")
    try:
        strip.refresh()
        assert "rocket" in strip._work_label.get_text(), strip._work_label.get_text()
        assert "/home/x/src/rocket" in strip._work_chip.get_tooltip_text()
        menu = _sections(strip._work_chip)
        assert [a for _l, a, _t in menu["Project"]] == ["app.choose-project", "app.clear-project"]
        assert "/home/x/src/rocket" in buildmode.prompt_clause()
        assert "run_command" in buildmode.prompt_clause()
    finally:
        buildmode.set_project("")
        buildmode.set_enabled(False)
        strip.refresh()
    assert buildmode.prompt_clause() == "", "no project, no clause"


def test_a_project_only_applies_in_build_mode():
    from shani_chronoa import buildmode
    buildmode.set_project("/home/x/src/rocket")
    try:
        buildmode.set_enabled(False)
        assert buildmode.project() == "", "a general conversation must not be redirected"
    finally:
        buildmode.set_project("")
