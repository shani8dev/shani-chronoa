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
import time
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
    from shani_chronoa.config import ChronoaConfig as _RealConfig

    class _C:
        def sense_allowed(self, name):
            return allowed

        def sense_allowed_reason(self, name):
            # **The real wording, not a hand-written one.** The refusal a
            # person reads has to name the switch the way the Settings row
            # spells it, and a stub's own sentence would prove nothing about
            # that - the stub *was* the reason this test could not see the
            # real one.
            return "" if allowed else _RealConfig().sense_allowed_reason(name)
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
        # **The switch by the name the Settings row shows**, not by its key:
        # a person who follows a gsettings key is hunting for a spelling no
        # screen ever shows.
        from shani_chronoa.capabilities import gate_label
        assert gate_label("accessibility-sense-enabled") in out, out

    def test_it_still_answers_the_question(self, monkeypatch, no_display):
        _enable_accessibility(monkeypatch, allowed=False)
        from shani_chronoa.capabilities import gate_label
        assert gate_label("accessibility-sense-enabled") in LW._run({})


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
    """The intent of this class is unchanged: on X11 the answer is the complete
    one, not the partial accessibility-bus one.

    It used to be named "the xdotool path still wins" and drove a fake
    `subprocess.run`. The path is gone - `xdotool` is absent from both ShaniOS
    images, and `getwindowclassname` never existed in the build it called - so
    what is pinned now is the property: **an X session is answered by the X
    backend, and the a11y bus is not consulted.**

    Verified for real with `xdotool` removed from `PATH` entirely: the backend
    lists Slack's actual client window, its real `WM_CLASS`, and its geometry.
    """

    def test_on_x11_the_backend_answers_not_the_bus(self, monkeypatch):
        from shani_chronoa.windows import Window

        monkeypatch.setattr(LW, "session_problem", lambda: "")

        class _Backend:
            name = "X11"

            def list(self):
                return [Window(id="12582916", title="Slack", app_id="slack",
                               pid=4, workspace=0, focused=False, minimized=False,
                               maximized=False, fullscreen=False,
                               x=67, y=69, width=1853, height=1011)]

        monkeypatch.setattr(LW, "control_backend", lambda: (_Backend(), ""))
        out = LW._run({})

        assert "X11" in out, out
        assert "12582916" in out, out
        assert "accessibility bus" not in out, (
            "an X session was answered by the a11y bus, which is the partial "
            "observation this class exists to keep off it")
        assert "slack" in out.lower(), (
            "the answer carried no application name, so the one fact that makes "
            "a window recognisable was lost")



class TestTheControlHalfIsRecorded:
    """What control can and cannot do off X11 is written where it is read.

    These used to pin "the control half is still X11 only". Measured on
    2026-10-08 that stopped being true: GTK4 frames offer close/minimize/
    toggle-maximized as AT-SPI actions, and KWin and the GNOME extension do the
    rest - while focusing through the bus still does nothing. The docstring is
    where the next person looks before adding a route, so it must say both.
    """

    def _source(self):
        return (_REPO / "usr" / "lib" / "shani-chronoa" / "shani_chronoa"
                / "skills" / "list_windows.py").read_text()

    def test_the_docstring_names_what_the_bus_can_do(self):
        assert "window.close" in self._source() and "window.minimize" in self._source()

    def test_and_what_it_cannot(self):
        assert "cannot focus" in self._source(), (
            "the reason focus has no accessibility route should be recorded where "
            "someone will read it before adding one"
        )


class TestTheFallbackIsAlsoBounded:
    """The sense got a timeout; the skill that calls it did not.

    `_bounded` was added to the `accessibility` sense so a wedged AT-SPI bus
    could not hang the sense. `read_window_titles` - the function this skill
    calls - was left unbounded, so the more exposed path still hung: a skill
    runs in the assistant's turn and in an MCP client's request, and either
    waits forever on a bus that is not answering.

    The honest-reporting property is the point, as it is for the sense. An
    empty window list means "no windows are open", which is a claim about the
    user's screen; a timeout is an admission of not looking.
    """

    def test_a_wedged_walk_gives_up_rather_than_hanging(self, monkeypatch, no_display):
        _enable_accessibility(monkeypatch)
        monkeypatch.setattr(A, "_TIMEOUT_SECONDS", 0.3)

        def wedged():
            time.sleep(30)
            return [], False

        monkeypatch.setattr(A, "read_window_titles", wedged)
        started = time.monotonic()
        out = LW._run({})
        elapsed = time.monotonic() - started
        assert elapsed < 5, f"the call took {elapsed:.1f}s against a 0.3s budget"
        assert "did not return within" in out

    def test_a_timeout_does_not_become_an_empty_window_list(self, monkeypatch, no_display):
        _enable_accessibility(monkeypatch)
        monkeypatch.setattr(A, "_TIMEOUT_SECONDS", 0.3)
        monkeypatch.setattr(A, "read_window_titles",
                            lambda: (time.sleep(30), ([], False))[1])
        out = LW._run({})
        assert "not the same as there being no windows" in out
        assert "0 visible window" not in out, \
            "a timeout was reported as an empty screen"

    def test_the_filter_does_not_turn_a_timeout_into_no_matches(self, monkeypatch,
                                                                no_display):
        # The filtered path has its own "nothing matched" message, which would be
        # a second wrong answer for the same timeout.
        _enable_accessibility(monkeypatch)
        monkeypatch.setattr(A, "_TIMEOUT_SECONDS", 0.3)
        monkeypatch.setattr(A, "read_window_titles",
                            lambda: (time.sleep(30), ([], False))[1])
        out = LW._run({"title_contains": "anything"})
        assert "No window's title" not in out
        assert "did not return within" in out

    def test_a_fast_walk_is_untouched(self, monkeypatch, no_display):
        _enable_accessibility(monkeypatch)
        monkeypatch.setattr(A, "read_window_titles",
                            lambda: ([("App", "A Window")], False))
        out = LW._run({})
        assert "A Window" in out
        assert "did not return within" not in out

    def test_a_bus_that_raises_is_still_reported_as_raising(self, monkeypatch, no_display):
        _enable_accessibility(monkeypatch)

        def boom():
            raise A._Unavailable("the accessibility bus did not answer (test)")

        monkeypatch.setattr(A, "read_window_titles", boom)
        out = LW._run({})
        assert "did not answer (test)" in out
        assert "did not return within" not in out
