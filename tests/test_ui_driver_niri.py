"""Driving an app on niri: its window by id, focus confirmed, keys by wtype.

What the fakes here stand for was measured first, in a nested niri 26.04
(shani-shell's dev session, `dev/session.sh`), with two GTK 4.24 windows and a
Qt 6.12 one:

- `niri msg action focus-window --id N` focused the asked window 30 times out
  of 30, and `focused-window` agreed immediately.
- **An id that does not exist exits 0 too**, and focus stays where it was. So
  the action's status is not evidence; `focused-window` is.
- wtype reached only the focused window (4 of 4 switches) - including a
  focused password entry, which is why the secret-field gate still runs after
  the focus.
- niri lists each window's `pid`, and the accessibility bus gives each app's
  process id, so the two are joined on that, not on a guessed name.

Nothing here reaches a real niri, a real bus or a real keyboard.
"""

from __future__ import annotations

import subprocess
import sys

import pytest

sys.path.insert(0, "usr/lib/shani-chronoa")

from shani_chronoa import ui_driver  # noqa: E402
from shani_chronoa.windows import WindowError  # noqa: E402
from shani_chronoa.windows import niri as niri_backend  # noqa: E402


class FakeNiri:
    """`niri msg` as the measured niri answers it."""

    def __init__(self, windows, focused=None, stubborn=False):
        self.windows = windows
        self.focused = focused
        self.stubborn = stubborn  # focus-window exits 0 and does nothing
        self.calls = []
        self.workspaces = [{"id": 1, "idx": 1, "output": "DP-1", "is_active": True},
                           {"id": 2, "idx": 2, "output": "DP-1", "is_active": False},
                           {"id": 5, "idx": 1, "output": "HDMI-A-1", "is_active": True}]
        self.outputs = {
            "DP-1": {"name": "DP-1", "logical": {"x": 0, "y": 0, "width": 1920, "height": 1080, "scale": 1.0}},
            "HDMI-A-1": {"name": "HDMI-A-1", "logical": {"x": 1920, "y": 0, "width": 2560, "height": 1440, "scale": 1.5}},
        }

    def __call__(self, *args):
        import json
        self.calls.append(args)
        if args[:1] == ("action",):
            if args[1] == "focus-window":
                wid = int(args[3])
                if not self.stubborn and any(w["id"] == wid for w in self.windows):
                    self.focused = wid
                return ""
            return ""
        if args == ("-j", "windows"):
            return json.dumps([dict(w, is_focused=w["id"] == self.focused)
                               for w in self.windows])
        if args == ("-j", "workspaces"):
            return json.dumps(self.workspaces)
        if args == ("-j", "outputs"):
            return json.dumps(self.outputs)
        if args == ("-j", "focused-window"):
            found = [w for w in self.windows if w["id"] == self.focused]
            return json.dumps(dict(found[0], is_focused=True) if found else None)
        raise WindowError(f"unexpected {args}")


class Node:
    def __init__(self, role, name, children=(), pid=0, states=()):
        self._role, self._name, self._children = role, name, list(children)
        self.pid, self.states = pid, set(states)

    def get_role_name(self):
        return self._role

    def get_name(self):
        return self._name

    def get_child_count(self):
        return len(self._children)

    def get_child_at_index(self, i):
        return self._children[i]

    def get_process_id(self):
        return self.pid


def _win(wid, title, pid, app_id="dev.probe"):
    return {"id": wid, "title": title, "pid": pid, "app_id": app_id,
            "workspace_id": 1, "is_floating": False, "layout": {}}


@pytest.fixture
def niri(monkeypatch):
    fake = FakeNiri([_win(3, "Probe A", 100), _win(4, "Probe B", 200),
                     _win(2, "Probe Qt", 300)], focused=4)
    monkeypatch.setattr(niri_backend, "_niri", fake)
    monkeypatch.setattr(ui_driver, "_niri_session", lambda: True)
    monkeypatch.setattr(ui_driver.time, "sleep", lambda s: None)
    apps = [Node("application", "probe-a", [Node("frame", "Probe A")], pid=100),
            Node("application", "probe-b", [Node("frame", "Probe B")], pid=200)]
    monkeypatch.setattr(ui_driver, "_desktop_apps", lambda: apps)
    monkeypatch.setattr(ui_driver, "_ax_states", lambda n: frozenset(n.states))
    monkeypatch.setattr(ui_driver, "_secure_field", lambda: ("clear", ""))
    monkeypatch.setattr(ui_driver, "_comms_app", lambda app: "")
    monkeypatch.setattr(ui_driver, "start_cue", lambda app: (None, ""))
    fake.apps = apps
    return fake


@pytest.fixture
def keyboard(monkeypatch):
    """wtype, recorded and never run."""
    sent = []

    def run(argv, **kw):
        sent.append((list(argv), kw.get("input")))
        return subprocess.CompletedProcess(argv, 0, "", "")

    monkeypatch.setattr(ui_driver.subprocess, "run", run)
    monkeypatch.setattr(ui_driver.shutil, "which",
                        lambda b: f"/usr/bin/{b}" if b == "wtype" else None)
    # the field reads back what wtype was given - no real bus, no OCR
    monkeypatch.setattr(ui_driver, "_read_back",
                        lambda app, text: "".join(i or "" for _, i in sent))
    return sent


# --- which window -----------------------------------------------------------


def test_the_window_is_the_one_with_the_apps_process_id(niri) -> None:
    assert ui_driver.niri_window("probe-a")["id"] == 3


def test_two_windows_of_one_process_are_told_apart_by_the_frame_title(niri) -> None:
    niri.windows.append(_win(9, "Preferences", 100))
    niri.apps[0]._children.append(Node("frame", "Preferences", states={"active"}))
    niri.apps[0]._children[0]._name = "Probe A"
    # both titles are the app's frames; the active frame decides
    assert ui_driver.niri_window("probe-a")["id"] == 9


def test_windows_that_cannot_be_told_apart_are_refused(niri) -> None:
    niri.windows.append(_win(9, "Probe A", 100))
    with pytest.raises(ui_driver.GuiUnavailable, match="cannot tell apart"):
        ui_driver.niri_window("probe-a")


def test_an_app_niri_does_not_list_is_refused(niri) -> None:
    niri.windows[:] = []
    with pytest.raises(ui_driver.GuiUnavailable, match="lists no window"):
        ui_driver.niri_window("probe-a")


# --- focus, and believing niri rather than the exit status --------------------


def test_focus_is_by_id_and_confirmed_by_focused_window(niri) -> None:
    assert ui_driver.niri_focus(niri.windows[0]) == ""
    assert ("action", "focus-window", "--id", "3") in niri.calls
    assert niri.calls[-1] == ("-j", "focused-window")


def test_a_focus_that_did_not_move_is_a_reason_not_a_success(niri) -> None:
    """Measured: a stale id exits 0 and focus stays put."""
    niri.stubborn = True
    why = ui_driver.niri_focus(niri.windows[0], patience=0)
    assert "'Probe B'" in why and "'Probe A'" in why


def test_keys_follow_a_confirmed_focus_through_wtype(niri, keyboard) -> None:
    said = ui_driver.type_text(app="probe-a", title="Probe A", text="-n hello")
    assert "read them back, through wtype" in said
    assert niri.focused == 3
    wtype = [call for call in keyboard if call[0][0] == "wtype"]
    # text on stdin, so a leading dash is text and not an option
    assert wtype[0] == (["wtype", "-d", "12", "-"], "-n hello")


def test_no_key_is_sent_when_the_focus_did_not_take(niri, keyboard) -> None:
    niri.stubborn = True
    with pytest.raises(ui_driver.GuiUnavailable, match="not sending keys"):
        ui_driver.type_text(app="probe-a", title="Probe A", text="hello")
    assert keyboard == []


def test_niri_never_waits_for_an_x_window(niri, keyboard, monkeypatch) -> None:
    monkeypatch.setattr(ui_driver, "find_window",
                        lambda *a, **k: pytest.fail("looked for an X window"))
    ui_driver.type_text(app="probe-a", title="Probe A", text="x")


def test_the_password_gate_runs_after_the_focus(niri, keyboard, monkeypatch) -> None:
    order = []
    monkeypatch.setattr(ui_driver, "_secure_field",
                        lambda: order.append(niri.focused) or ("secure", "a password text"))
    with pytest.raises(ui_driver.GuiUnavailable, match="password"):
        ui_driver.type_text(app="probe-a", title="Probe A", text="hunter2")
    assert order == [3]
    assert keyboard == []


@pytest.mark.parametrize("keys, args", [
    ("Return", ["-k", "Return"]),
    ("ctrl+l", ["-M", "ctrl", "-k", "l", "-m", "ctrl"]),
    ("ctrl+shift+t", ["-M", "ctrl", "-M", "shift", "-k", "t", "-m", "shift", "-m", "ctrl"]),
    ("super+Down", ["-M", "logo", "-k", "Down", "-m", "logo"]),
])
def test_a_chord_is_pressed_and_released_in_order(keys, args) -> None:
    assert ui_driver._wtype_chord(keys) == args


def test_an_unknown_modifier_is_refused() -> None:
    with pytest.raises(ui_driver.GuiUnavailable, match="not a modifier"):
        ui_driver._wtype_chord("hyper+x")


def test_a_chord_goes_to_the_focused_window_only(niri, keyboard) -> None:
    said = ui_driver.key(app="probe-b", title="Probe B", keys="ctrl+l")
    assert niri.focused == 4
    assert keyboard[-1][0] == ["wtype", "-M", "ctrl", "-k", "l", "-m", "ctrl"]
    assert said.startswith("Sent ctrl+l")


def test_a_chat_window_is_refused_before_niri_is_asked_anything(niri, keyboard, monkeypatch) -> None:
    monkeypatch.setattr(ui_driver, "_comms_app", lambda app: "'slack' is a communication surface")
    monkeypatch.setattr(ui_driver, "_comms_override", lambda: False)
    with pytest.raises(ui_driver.GuiUnavailable, match="communication surface"):
        ui_driver.key(app="slack", title="Slack", keys="Return")
    assert niri.calls == [] and keyboard == []


# --- 5. a window's capture: grim, its own output, cropped where niri says ------


def _floating(win, x, y, w=942, h=920):
    win.update(is_floating=True, layout={
        "pos_in_scrolling_layout": None, "tile_size": [w, h], "window_size": [w, h],
        "tile_pos_in_workspace_view": [x, y], "window_offset_in_tile": [0.0, 0.0]})
    return win


@pytest.fixture
def grim(monkeypatch, tmp_path):
    runs = []
    monkeypatch.setattr(ui_driver.tempfile, "tempdir", str(tmp_path))

    def run(argv, **kw):
        runs.append(list(argv))
        if argv[0] == "grim":
            with open(argv[-1], "wb") as handle:
                handle.write(b"\x89PNG fake")
        return subprocess.CompletedProcess(argv, 0, "", "")

    monkeypatch.setattr(ui_driver.subprocess, "run", run)
    monkeypatch.setattr(ui_driver.shutil, "which", lambda b: f"/usr/bin/{b}")
    return runs


def test_a_tiled_window_is_its_output_and_nothing_else(niri, grim) -> None:
    path, note = ui_driver.window_shot("probe-a")
    assert grim[-1][:3] == ["grim", "-o", "DP-1"]
    assert "no position for a tiled window" in note


def test_a_floating_window_is_cropped_to_its_rectangle(niri, grim) -> None:
    _floating(niri.windows[0], 539.0, 80.0)  # the measured layout
    path, note = ui_driver.window_shot("probe-a")
    assert grim[-1][:3] == ["grim", "-g", "539,80 942x920"]


def test_the_rectangle_is_offset_by_the_outputs_own_position(niri, grim) -> None:
    niri.windows[0]["workspace_id"] = 5  # on the monitor to the right
    _floating(niri.windows[0], 100.0, 50.0, 400, 300)
    ui_driver.window_shot("probe-a")
    assert grim[-1][:3] == ["grim", "-g", "2020,50 400x300"]


def test_a_window_on_a_workspace_not_showing_is_refused(niri, grim) -> None:
    """niri keeps such a window's AT-SPI frame SHOWING (measured), so only
    niri can say the capture would be of something else."""
    niri.windows[0]["workspace_id"] = 2
    with pytest.raises(ui_driver.GuiUnavailable, match="not the one showing"):
        ui_driver.window_shot("probe-a")
    assert grim == []


def test_another_floating_window_over_it_is_said(niri, grim) -> None:
    _floating(niri.windows[0], 100.0, 100.0, 400, 300)
    _floating(niri.windows[1], 300.0, 200.0, 400, 300)
    niri.focused = 4
    _path, note = ui_driver.window_shot("probe-a")
    assert "'Probe B'" in note


def test_the_capture_never_goes_through_niris_own_screenshot(niri, grim) -> None:
    """screenshot-window exists in 26.04 and replaces the clipboard (measured)."""
    ui_driver.window_shot("probe-a")
    assert not any("screenshot-window" in call for call in niri.calls)
    assert not any(call[:2] == ("action", "screenshot-screen") for call in niri.calls)


def test_a_capture_that_wrote_nothing_is_said(niri, monkeypatch, tmp_path) -> None:
    monkeypatch.setattr(ui_driver.tempfile, "tempdir", str(tmp_path))
    monkeypatch.setattr(ui_driver.shutil, "which", lambda b: "/usr/bin/grim")
    monkeypatch.setattr(ui_driver.subprocess, "run", lambda argv, **kw:
                        subprocess.CompletedProcess(argv, 1, "", "unknown output 'DP-1'"))
    with pytest.raises(ui_driver.GuiUnavailable, match="unknown output"):
        ui_driver.window_shot("probe-a")


# --- the coordinate fallback on niri: only where the point is certain ---------


def test_a_tiled_window_without_an_action_is_not_clicked_at_a_guess(niri, monkeypatch) -> None:
    pressed = []
    monkeypatch.setattr(ui_driver, "_press_pointer", lambda *a, **k: pressed.append(a))
    target = Node("button", "Odd")
    with pytest.raises(ui_driver.GuiUnavailable, match="nowhere certain to click"):
        ui_driver._click_on_niri("probe-a", target)
    assert pressed == []


def test_a_floating_window_is_clicked_at_its_element_after_the_focus(niri, monkeypatch) -> None:
    _floating(niri.windows[0], 539.0, 80.0)
    pressed = []
    monkeypatch.setattr(ui_driver, "_press_pointer",
                        lambda app, x, y, button="left": pressed.append((niri.focused, x, y)) or "ydotool")
    monkeypatch.setattr(ui_driver, "_ax_window_box", lambda n: (0, 63, 942, 34))
    said = ui_driver._click_on_niri("probe-a", Node("button", "Increment"))
    assert pressed == [(3, 539 + 471, 80 + 63 + 17)]
    assert "through ydotool" in said
