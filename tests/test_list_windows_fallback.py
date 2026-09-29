"""Listing windows where xdotool cannot see them.

`xdotool` drives X11. Under Wayland it is absent, or worse - present and talking
to Xwayland, which knows only about X11 clients, so it lists a fraction of the
windows and looks like it listed them all.

The two paths answer *different* questions, and the skill says so rather than
presenting one as a repair of the other. Measured on this machine: 23 windows by
xdotool, 11 titled windows on the accessibility bus. The gap is the point - an app
that does not implement AT-SPI is absent, and a mirroring desktop reports a
window twice.

The control half is unchanged and must stay that way. AT-SPI exposes no
`WindowAction` interface on any window inspected here, so there is no portable
way to raise or close one, and those skills continue to refuse.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

_REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_REPO / "usr" / "lib" / "shani-chronoa"))

from shani_chronoa import config as config_mod  # noqa: E402
from shani_chronoa.senses import accessibility as A  # noqa: E402
from shani_chronoa.skills import list_windows as LW  # noqa: E402


@pytest.fixture
def no_display(monkeypatch):
    monkeypatch.delenv("DISPLAY", raising=False)


class _Bus:
    """A stand-in accessibility bus with a fixed set of windows."""

    def __init__(self, windows):
        self._windows = windows

    def read(self):
        return self._windows, False


def _enable_accessibility(monkeypatch, allowed=True):
    class _C:
        def sense_allowed(self, name):
            return allowed

        def sense_allowed_reason(self, name):
            return "" if allowed else "the accessibility sense is turned off"
    monkeypatch.setattr(A, "ChronoaConfig", _C)
    monkeypatch.setattr(config_mod, "ChronoaConfig", _C)


class TestTheFallbackIsReachable:
    def test_without_x11_it_does_not_just_say_no(self, monkeypatch, no_display):
        """Listing is read-only, so a real alternative exists.

        Stopping at "could not" would be accurate and wasteful when a different
        mechanism answers the same question.
        """
        _enable_accessibility(monkeypatch)
        monkeypatch.setattr(A, "read_window_titles",
                            lambda: ([("slack", "engineering")], False))
        out = LW._run({})
        assert "accessibility bus" in out
        assert "slack" in out

    def test_it_names_the_mechanism_it_used(self, monkeypatch, no_display):
        """So the user is never misled about which observation this is."""
        _enable_accessibility(monkeypatch)
        monkeypatch.setattr(A, "read_window_titles",
                            lambda: ([("app", "a window")], False))
        out = LW._run({})
        assert "xdotool cannot see this session" in out
        assert "accessibility bus" in out

    def test_it_admits_the_list_is_not_complete(self, monkeypatch, no_display):
        """The honest part, and the reason the two paths are not merged.

        An app that does not implement AT-SPI is simply absent, so calling this
        "the window list" would be the quiet wrongness the module docstring
        exists to avoid.
        """
        _enable_accessibility(monkeypatch)
        monkeypatch.setattr(A, "read_window_titles",
                            lambda: ([("app", "a window")], False))
        assert "do not implement AT-SPI" in LW._run({})

    def test_it_reports_the_full_count_not_just_what_is_shown(self, monkeypatch,
                                                             no_display):
        _enable_accessibility(monkeypatch)
        many = [(f"app{i}", f"window{i}") for i in range(LW._MAX_WINDOWS + 3)]
        monkeypatch.setattr(A, "read_window_titles", lambda: (many, True))
        out = LW._run({})
        assert str(LW._MAX_WINDOWS + 3) in out.splitlines()[0]
        assert "more windows" in out or "more window" in out


class TestWithoutTheSenseItExplainsWhy:
    def test_a_shut_sense_is_not_silently_bypassed(self, monkeypatch, no_display):
        """The fallback reads the accessibility bus, so the gate applies to it."""
        _enable_accessibility(monkeypatch, allowed=False)
        out = LW._run({})
        assert "turned off" in out
        assert "accessibility-sense-enabled" in out

    def test_it_still_answers_the_question(self, monkeypatch, no_display):
        _enable_accessibility(monkeypatch, allowed=False)
        assert "accessibility-sense-enabled" in LW._run({})


class TestUnavailableIsNotEmpty:
    def test_a_dead_bus_does_not_report_no_windows(self, monkeypatch, no_display):
        _enable_accessibility(monkeypatch)

        def dead():
            raise A._Unavailable("the accessibility bus did not answer")
        monkeypatch.setattr(A, "_desktop", dead)
        out = LW._run({}).lower()
        assert "did not answer" in out
        assert "no window" not in out

    def test_an_empty_bus_says_it_is_not_proof_of_an_empty_desktop(self, monkeypatch,
                                                                   no_display):
        _enable_accessibility(monkeypatch)
        monkeypatch.setattr(A, "read_window_titles", lambda: ([], False))
        out = LW._run({})
        assert "not proof the desktop is empty" in out


class TestX11IsUnchanged:
    def test_on_x11_the_xdotool_path_still_wins(self, monkeypatch):
        """No behaviour change where xdotool works.

        It is authoritative there: window ids, geometry, and every window whether
        or not it implements accessibility. The fallback is for sessions xdotool
        cannot see, not a replacement.
        """
        monkeypatch.setenv("DISPLAY", ":99")
        monkeypatch.setattr(LW, "session_problem", lambda: "")

        class _Proc:
            stdout = "12345\n"
            stderr = ""
            returncode = 0

        monkeypatch.setattr(LW.subprocess, "run", lambda *a, **kw: _Proc())
        monkeypatch.setattr(LW, "files", type("F", (), {
            "cap_list": lambda rows, limit: (rows, 0),
            "withheld_note": lambda *a, **kw: "",
            "tool_missing": lambda *a, **kw: "xdotool missing",
        }))
        out = LW._run({})
        assert "accessibility bus" not in out


class TestTheControlHalfIsUnchanged:
    """Focusing and closing have no portable path, and must keep refusing."""

    def test_the_docstring_says_they_still_refuse(self):
        source = (_REPO / "usr" / "lib" / "shani-chronoa" / "shani_chronoa"
                  / "skills" / "list_windows.py").read_text()
        assert "control half is still X11 only" in source

    def test_and_explains_why_there_is_no_fallback(self):
        source = (_REPO / "usr" / "lib" / "shani-chronoa" / "shani_chronoa"
                  / "skills" / "list_windows.py").read_text()
        assert "WindowAction" in source, (
            "the reason there is no control fallback should be recorded where "
            "someone will read it before adding one"
        )
