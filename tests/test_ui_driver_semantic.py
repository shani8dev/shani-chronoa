"""The driver acting on what an element *is*, not where it was.

Measured in a nested niri 26.04 (shani-shell's dev session) with a GTK 4.24
window and a Qt 6.12 one before any of this was written:

- **Both trees are on the bus**, and both publish an `Action` and an
  `EditableText` interface: `do_action` on GTK's "Increment" (`click`) and Qt's
  (`Press`) each pressed the button once, and `set_text_contents` put text into
  both entries and read back exactly. So a press and a type need no pointer, no
  keyboard and no focus at all.
- **Neither knows where it is on screen.** Every SCREEN extent came back
  relative to the window (GTK's frame at 0,0; Qt's button at 0,23), because no
  Wayland client may learn its position - so a coordinate click computed from
  the tree is a guess, and it is the route of last resort here.
- **GTK4 never sets ENABLED**: an enabled button is VISIBLE SHOWING SENSITIVE
  FOCUSABLE, a disabled one lacks SENSITIVE. Qt sets both. "Enabled" is either.
- **GTK4's `grab_focus` raises** (`atspi_error: (1)`) on an entry, so focusing a
  field through the tree is not a step to rely on.

The fakes here stand in for exactly those shapes; nothing reaches a real bus.
"""

from __future__ import annotations

import json
import sys

import pytest

sys.path.insert(0, "usr/lib/shani-chronoa")

from shani_chronoa import ui_driver  # noqa: E402

ON = frozenset({"visible", "showing", "sensitive", "enabled", "focusable"})


class FakeNode:
    """An accessibility element with what the driver reads off one."""

    def __init__(self, role="", name="", children=None, states=ON,
                 actions=(), text=None, pid=0):
        self._role, self._name = role, name
        self._children = list(children or [])
        self.states = set(states)
        self.actions = list(actions)
        self.text = text
        self.pid = pid
        self.done = []
        self.insert_ok = True

    def get_role_name(self):
        return self._role

    def get_name(self):
        return self._name

    def get_child_count(self):
        return len(self._children)

    def get_child_at_index(self, index):
        return self._children[index] if index < len(self._children) else None

    def get_process_id(self):
        return self.pid

    def insert(self, index, node):
        self._children.insert(index, node)


@pytest.fixture(autouse=True)
def _fake_bus(monkeypatch, tmp_path):
    """Every seam to AT-SPI answered from the fakes; no screen read, no OCR."""
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "data"))
    monkeypatch.setattr(ui_driver, "_secure_field", lambda: ("clear", ""))
    monkeypatch.setattr(ui_driver, "screen_text",
                        lambda app: (_ for _ in ()).throw(ui_driver.GuiUnavailable("no")))
    apps = []
    monkeypatch.setattr(ui_driver, "_desktop_apps", lambda: list(apps))
    return apps


def _app(*children, name="editor", pid=4242):
    frame = FakeNode("frame", "Untitled", list(children))
    return FakeNode("application", name, [frame], pid=pid)


def _button(name, **kw):
    return FakeNode("button", name, actions=kw.pop("actions", ["click"]), **kw)


# --- 1. stable references -----------------------------------------------------


def test_a_reference_reads_back_as_itself() -> None:
    ref = ui_driver.ElementRef((0, 2, 1), "button", "Save: all")
    assert ui_driver.ElementRef.parse(ref.token()) == ref


@pytest.mark.parametrize("token", ["", "button", "0.x:button:Save", "0.1:", "0.1::Save"])
def test_a_malformed_reference_is_refused(token) -> None:
    with pytest.raises(ui_driver.GuiUnavailable):
        ui_driver.ElementRef.parse(token)


def test_the_number_means_what_was_shown_after_a_row_is_added_above(_fake_bus) -> None:
    """The defect: "click 2" re-counted the live tree, so a row inserted above
    the button the model chose made it press the row instead."""
    frame_children = [_button("New"), _button("Delete")]
    app = _app(*frame_children)
    _fake_bus.append(app)
    ui_driver.observation("editor")
    app.get_child_at_index(0).insert(0, _button("Undo close"))  # a toast appears
    chosen = ui_driver.control_by_number("editor", 2)
    assert chosen.get_name() == "Delete"


def test_an_element_that_is_gone_is_refused_not_replaced(_fake_bus) -> None:
    app = _app(_button("New"), _button("Delete"))
    _fake_bus.append(app)
    ui_driver.observation("editor")
    app.get_child_at_index(0)._children[1] = _button("Archive")
    with pytest.raises(ui_driver.StaleRef, match="no longer in editor"):
        ui_driver.control_by_number("editor", 2)


def test_two_identical_controls_after_a_move_are_refused(_fake_bus) -> None:
    app = _app(_button("Delete"), FakeNode("label", "row"), _button("Delete"))
    _fake_bus.append(app)
    ref = ui_driver.ElementRef((0, 0), "button", "Delete")
    app.get_child_at_index(0).insert(0, _button("Pinned"))
    with pytest.raises(ui_driver.StaleRef, match="2 elements"):
        ui_driver.resolve_ref("editor", ref)


def test_the_path_still_tells_two_identical_controls_apart(_fake_bus) -> None:
    second = _button("Delete")
    app = _app(_button("Delete"), second)
    _fake_bus.append(app)
    assert ui_driver.resolve_ref(
        "editor", ui_driver.ElementRef((0, 1), "button", "Delete")) is second


def test_the_saved_list_is_written_where_the_test_said(_fake_bus, tmp_path) -> None:
    _fake_bus.append(_app(_button("New")))
    ui_driver.observation("editor")
    saved = json.loads(open(ui_driver._refs_path("editor")).read())
    assert ui_driver._refs_path("editor").startswith(str(tmp_path))
    assert saved["refs"] == ["0.0:button:New"]


def test_drive_takes_a_reference_and_resolves_it(_fake_bus, monkeypatch) -> None:
    _fake_bus.append(_app(_button("New"), _button("Delete")))
    pressed = []
    monkeypatch.setattr(ui_driver, "act_on", raising=False, value=lambda app, node, verb, text="":
                        pressed.append(node.get_name()) or "pressed")
    monkeypatch.setattr(ui_driver, "click", lambda **kw:
                        pressed.append(kw["target"]) or "clicked")
    ui_driver.drive("click", app="editor", ref="0.1:button:Delete")
    assert pressed == ["Delete"]
