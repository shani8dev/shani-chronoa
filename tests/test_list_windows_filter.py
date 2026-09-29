"""A desktop has more windows open than anyone wants to read.

`list_windows` took no arguments and returned every window every time — 22 on
this machine, ~2,200 characters of context to answer "which window is Slack?".
The filter is read through one checked helper on both the xdotool and the
AT-SPI path, and the count reported is of everything *found*, so a filter that
matches nothing is distinguishable from a filter that was ignored.

The other thing pinned here is that a malformed argument must not take the turn
down. A model can send a number, a list or null for a parameter the schema
declares as a string; reading it as text without a check raises AttributeError
on `.strip()` inside the tool loop.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

_REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_REPO / "usr" / "lib" / "shani-chronoa"))

from shani_chronoa.skills import list_windows as LW  # noqa: E402


@pytest.fixture
def no_display(monkeypatch):
    """Force the path that cannot use xdotool, so the fallback is exercised."""
    monkeypatch.delenv("DISPLAY", raising=False)


ROWS = [
    ("101", "Slack - engineering", "slack", "0,0 800x600"),
    ("102", "Inbox", "thunderbird", "0,0 800x600"),
    ("103", "* platform - Verteil Engineering - Slack", "mutter-x11-frames", "0,0 800x600"),
    ("104", "", "", ""),
    ("105", "notes.txt - Editor", "gedit", "0,0 800x600"),
]


class _Proc:
    def __init__(self, stdout="", returncode=0, stderr=""):
        self.stdout, self.returncode, self.stderr = stdout, returncode, stderr


@pytest.fixture
def windows(monkeypatch):
    """Drive the xdotool path with a fixed set of windows."""
    names = {r[0]: r[1] for r in ROWS}
    classes = {r[0]: r[2] for r in ROWS}
    monkeypatch.setattr(LW, "session_problem", lambda: "")
    monkeypatch.setattr(LW, "_MAX_WINDOWS", 60)
    seen = []

    def run(cmd, **kw):
        seen.append(list(cmd))
        if cmd[1:3] == ["search", "--onlyvisible"]:
            return _Proc("\n".join(names))
        wid = cmd[-1]
        if cmd[1] == "getwindowname":
            return _Proc(names.get(wid, ""))
        if cmd[1] == "getwindowclassname":
            return _Proc(classes.get(wid, ""))
        if cmd[1] == "getwindowgeometry":
            return _Proc("Position: 0,0\nGeometry: 800x600+0+0\n")
        return _Proc("")

    monkeypatch.setattr(LW.subprocess, "run", run)
    return seen


def _titles(out: str) -> list:
    return [line for line in out.splitlines() if line.strip().startswith(("101", "102", "103", "104", "105"))]


class TestFiltering:
    def test_no_filter_still_lists_everything(self, windows):
        out = LW._run({})
        assert "showing 5" in out
        assert len(_titles(out)) == 5

    def test_a_filter_narrows_the_list(self, windows):
        out = LW._run({"title_contains": "Slack"})
        assert "showing 2" in out, "two windows on this machine match 'Slack'"
        assert "Inbox" not in out

    def test_the_filter_is_case_insensitive(self, windows):
        assert "showing 2" in LW._run({"title_contains": "slack"})

    def test_it_also_matches_the_class(self, windows):
        """A model asked about an app will reach for the application name."""
        out = LW._run({"title_contains": "thunderbird"})
        assert "Inbox" in out

    def test_the_count_reports_everything_found(self, windows):
        """So a filter that matched nothing is not a filter that was ignored."""
        out = LW._run({"title_contains": "slack"})
        assert "5 visible window(s)" in out and "showing 2" in out

    def test_no_match_says_so_and_says_how_many_are_open(self, windows):
        out = LW._run({"title_contains": "zzzz-nothing"})
        assert "No visible window" in out
        assert "5 window(s) are open" in out

    def test_and_points_at_the_unfiltered_call(self, windows):
        assert "no filter" in LW._run({"title_contains": "zzzz-nothing"})

    def test_an_empty_filter_is_no_filter(self, windows):
        assert "showing 5" in LW._run({"title_contains": ""})

    def test_the_schema_declares_the_argument(self):
        """Not a grep of the source, which the implementation itself satisfies.

        The first version asserted the string `"title_contains"` appears in the
        file - which it does, in the handler that reads it. Renaming the schema
        key to anything else left the test green, so a model that could never
        pass the argument looked like a working feature. This reads the actual
        published schema, which is what a client sees.
        """
        from shani_chronoa.tools import TOOLS
        tool = next(t for t in TOOLS if t["function"]["name"] == "list_windows")
        properties = tool["function"]["parameters"].get("properties", {})
        assert "title_contains" in properties, (
            f"the filter is not in the published schema - the model cannot "
            f"pass it. Properties are {sorted(properties)}"
        )
        assert properties["title_contains"]["type"] == "string"


class TestAMalformedArgumentDoesNotTakeTheTurnDown:
    """A model can send anything for a string parameter."""

    @pytest.mark.parametrize("bad", [123, 45.5, ["slack"], {"a": 1}, None, True])
    def test_a_non_string_is_treated_as_no_filter(self, windows, bad):
        out = LW._run({"title_contains": bad})
        assert "showing 5" in out, (
            f"{bad!r} was not treated as an absent filter"
        )

    def test_and_does_not_raise(self, windows):
        # The point: this used to raise AttributeError on `.strip()` inside the
        # tool loop, which takes the turn with it rather than one call.
        LW._run({"title_contains": 123})

    def test_the_helper_is_the_single_guarded_read(self):
        assert LW._text_argument({"title_contains": "x"}, "title_contains") == "x"
        assert LW._text_argument({"title_contains": 1}, "title_contains") == ""
        assert LW._text_argument({}, "title_contains") == ""
        assert LW._text_argument(None, "title_contains") == ""


class TestTheFilterWorksOnTheAccessibilityPathToo:
    def test_it_filters_by_application_as_well_as_title(self, monkeypatch, no_display):
        monkeypatch.delenv("DISPLAY", raising=False)

        class _On:
            def sense_allowed(self, name):
                return True

            def sense_allowed_reason(self, name):
                return ""

        import shani_chronoa.config as config_module
        from shani_chronoa.senses import accessibility as A
        # `_via_accessibility` imports ChronoaConfig inside the function, so the
        # patch goes on the config *module*. Patching the accessibility sense's
        # copy instead left the gate shut and every test asserted the refusal.
        monkeypatch.setattr(config_module, "ChronoaConfig", lambda: _On())
        monkeypatch.setattr(A, "read_window_titles", lambda: (
            [("slack", "engineering"), ("Chrome", "a page")], False))
        out = LW._via_accessibility({"title_contains": "slack"})
        assert "slack" in out
        assert "a page" not in out

    def test_no_match_on_that_path_says_how_many_were_seen(self, monkeypatch,
                                                            no_display):
        monkeypatch.delenv("DISPLAY", raising=False)

        class _On:
            def sense_allowed(self, name):
                return True

            def sense_allowed_reason(self, name):
                return ""

        import shani_chronoa.config as config_module
        from shani_chronoa.senses import accessibility as A
        monkeypatch.setattr(config_module, "ChronoaConfig", lambda: _On())
        monkeypatch.setattr(A, "read_window_titles", lambda: (
            [("slack", "engineering")], False))
        out = LW._via_accessibility({"title_contains": "zzz"})
        assert "1 window(s) are on the accessibility bus" in out
