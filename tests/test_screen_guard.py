"""A password manager on screen must not end up in a screenshot.

The guard is a refusal, not a redaction: geometry a compositor reports is not
geometry a capture contains (scaling, fractional scale, workspace offsets), and
a rectangle computed from the wrong one looks applied and is not. These tests
pin the two things that make it trustworthy - it costs nothing when nobody has
configured it, and it fails closed when the desktop cannot be asked.
"""

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "usr/lib/shani-chronoa"))

from shani_chronoa import screen_guard  # noqa: E402
from shani_chronoa.screengrab import ScreenCaptureError  # noqa: E402


class Config:
    def __init__(self, value):
        self.value = value
        self.read = []

    def get(self, key, default=""):
        self.read.append(key)
        return self.value if key == screen_guard.SETTING else default


def test_no_exclusions_never_asks_the_desktop(monkeypatch):
    """Every machine that never turns this on must not pay a subprocess per shot."""
    def explode():
        raise AssertionError("the desktop was queried with no exclusions configured")

    monkeypatch.setattr(screen_guard, "visible_windows", explode)
    assert screen_guard.check(Config("")) is None
    assert screen_guard.check(Config("   ")) is None


def test_an_excluded_app_on_screen_refuses_the_capture(monkeypatch):
    monkeypatch.setattr(screen_guard, "visible_windows",
                        lambda: [("org.keepassxc.KeePassXC", "Passwords"),
                                 ("firefox", "News")])
    with pytest.raises(ScreenCaptureError) as caught:
        screen_guard.check(Config("org.keepassxc.KeePassXC"))
    assert "org.keepassxc.KeePassXC" in str(caught.value)
    assert "excluded" in str(caught.value)


def test_an_app_on_another_workspace_is_not_on_screen(monkeypatch):
    """niri reports every window it has; only the active workspace is visible."""
    answers = {
        "windows": [{"app_id": "org.keepassxc.KeePassXC", "title": "Passwords",
                     "workspace_id": 2},
                    {"app_id": "firefox", "title": "News", "workspace_id": 1}],
        "workspaces": [{"id": 1, "is_active": True}, {"id": 2, "is_active": False}],
    }

    def run(argv):
        return json.dumps(answers[argv[-1]])

    monkeypatch.setattr(screen_guard.shutil, "which", lambda name: "/usr/bin/niri")
    monkeypatch.setattr(screen_guard, "_run", run)
    assert screen_guard.check(Config("org.keepassxc.KeePassXC")) is None
    assert screen_guard.visible_windows() == [("firefox", "News")]


def test_an_excluded_app_on_the_active_workspace_refuses(monkeypatch):
    answers = {
        "windows": [{"app_id": "org.keepassxc.KeePassXC", "title": "Passwords",
                     "workspace_id": 1}],
        "workspaces": [{"id": 1, "is_active": True}],
    }
    monkeypatch.setattr(screen_guard.shutil, "which", lambda name: "/usr/bin/niri")
    monkeypatch.setattr(screen_guard, "_run", lambda argv: json.dumps(answers[argv[-1]]))
    with pytest.raises(ScreenCaptureError):
        screen_guard.check(Config("org.keepassxc.KeePassXC"))


def test_an_unknown_desktop_fails_closed_when_exclusions_are_set(monkeypatch):
    monkeypatch.setattr(screen_guard, "visible_windows", lambda: None)
    with pytest.raises(ScreenCaptureError) as caught:
        screen_guard.check(Config("firefox"))
    assert "cannot be asked" in str(caught.value)
    assert screen_guard.SETTING in str(caught.value)


def test_an_unknown_desktop_is_fine_with_no_exclusions(monkeypatch):
    monkeypatch.setattr(screen_guard, "visible_windows", lambda: None)
    assert screen_guard.check(Config("")) is None


def test_wmctrl_classes_match_either_half_of_instance_dot_class(monkeypatch):
    listing = ("0x03a00007  0 Navigator.firefox  host News\n"
               "0x03e00003  0 org.keepassxc.KeePassXC  host Passwords\n")
    desktops = "0  * DG: 1920x1080  VP: 0,0  WA: 0,0 1920x1080  Workspace 1\n"
    monkeypatch.setattr(screen_guard.shutil, "which", lambda name: "/usr/bin/wmctrl")
    monkeypatch.setattr(screen_guard, "_run",
                        lambda argv: desktops if "--" not in argv and "-d" in argv else listing)
    windows = screen_guard._wmctrl_windows()
    assert ("Navigator.firefox", "News") in windows
    assert screen_guard._matches("Navigator.firefox", ["firefox"])
    assert screen_guard._matches("org.keepassxc.KeePassXC", ["keepassxc"])


def test_matching_is_case_insensitive_and_exact():
    assert screen_guard._matches("Firefox", ["firefox"])
    assert not screen_guard._matches("firefox", ["fire"])
    assert not screen_guard._matches("", ["firefox"])


def test_every_capture_path_goes_through_the_guard():
    """The skill, the vision sense and the portal all reach `capture_screen`."""
    import inspect

    from shani_chronoa import screengrab

    body = inspect.getsource(screengrab.capture_screen)
    assert "screen_guard.check()" in body
