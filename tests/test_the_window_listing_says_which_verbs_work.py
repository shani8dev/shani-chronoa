"""The window listing advised the same verbs for every window, most of which refuse.

`AtspiBackend.controllable()` existed and had **no caller in the tree**. Its
docstring is one line: *"The verbs this window offers, for a listing to show."*

And this is the listing. What it actually said, for every window regardless of the
application behind it:

    Use the id with focus_window, close_window or arrange_window.

Measured on a frame whose bus reports only `window.close`:

| verb pressed | result |
|---|---|
| `close` | offered |
| `minimize` | **refused** — "this application does not offer 'minimize' on the accessibility bus (GTK4 apps do; GTK3 apps and Chromium-based browsers do not)" |
| `maximize` | **refused**, same message |

and on a frame reporting `activate`, all three refused. So the closing line was a
guaranteed failure for most windows on the bus.

## What is deliberately *not* claimed

- **`None` means say nothing, never "none offered".** A listing that cannot tell
  must not assert an empty set — that would be the same bug, inverted.
- **Only the three verbs `ACTIONS` declares.** `arrange_window` also needs
  `fullscreen`/`move`/`resize`, which `controllable` does not report. Annotating
  with three verbs is honest; annotating with "arrange_window works" is not.
- **Non-atspi backends are untouched.** `controllable` is not declared on
  `Backend` at all, so `getattr` finds `None` and the output is byte-identical.
- **Probing happens after the cap**, never for withheld rows, and is bounded —
  each `controllable()` walks `_frames()`, so an unbounded probe turns an instant
  listing into 60 bus enumerations.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "usr/lib/shani-chronoa"))

from shani_chronoa import windows  # noqa: E402
from shani_chronoa.skills import list_windows as LW  # noqa: E402


class _Atspi:
    """A backend with the a11y verbs: one full window, one with only close."""

    name = "the accessibility bus"

    def __init__(self, rows):
        self._rows = rows

    def list(self):
        return [windows.Window(id=str(i), title=t, app_id=a, pid=1)
                for i, (t, a) in enumerate(self._rows, start=1)]

    def controllable(self, wid):
        return {1: ["close", "minimize", "maximize"], 2: ["close"]}[int(wid)]


class _Plain:
    """xdotool / kwin: no `controllable`, so nothing to say."""

    name = "xdotool"

    def list(self):
        return [windows.Window(id="7", title="Konsole", app_id="konsole", pid=1)]


ROWS = [("Text Editor", "org.gnome.TextEditor"), ("Chromium", "chromium")]


def _listing(backend, **kw):
    return LW._via_backend(backend, {})


def test_each_window_shows_the_verbs_it_offers():
    """**The defect.** One blanket sentence for every window; Chromium refuses
    minimize and maximize, and the listing did not say so."""
    said = _listing(_Atspi(ROWS))

    assert "<close, minimize, maximize>" in said, said
    assert "<close>" in said, (
        "the window offering only close is not marked as such, so asking for "
        "minimize is a guaranteed refusal: " + said)


def test_a_window_with_no_verbs_says_so():
    class _Bare(_Atspi):
        def controllable(self, wid):
            return []

    said = _listing(_Bare(ROWS))
    assert "no verbs on the accessibility bus" in said, said


def test_a_window_that_leaves_the_bus_is_not_reported_as_having_no_verbs():
    """**The distinction that matters.** Vanished mid-listing is not the same as
    offering nothing, and saying so would be a claim about an application that is
    no longer there to check."""
    class _Gone(_Atspi):
        def controllable(self, wid):
            if int(wid) == 2:
                raise windows.WindowError("it left the bus")
            return ["close"]

    said = _listing(_Gone(ROWS))
    assert "verbs unknown" in said, said
    assert "no verbs" not in said, (
        "a window that vanished is being reported as offering nothing: " + said)


def test_a_backend_that_cannot_say_changes_nothing():
    """`controllable` is not declared on `Backend`, so every non-atspi backend
    reaches `getattr(...) is None`. Its output must be untouched."""
    said = _listing(_Plain())

    assert "<" not in said, said
    assert "Use the id with focus_window" in said, said


def test_nothing_is_invented_when_the_probe_itself_fails():
    """**`None` means say nothing, never "none offered".** Asserting an empty
    set on failure is the same bug inverted."""
    class _Broken(_Atspi):
        def controllable(self, wid):
            raise RuntimeError("the bus stopped answering")

    said = _listing(_Broken(ROWS))
    assert "no verbs" not in said, said
    assert "verbs unknown" not in said, (
        "a failed probe is being reported as a fact about the window: " + said)


def test_withheld_windows_are_never_probed(monkeypatch):
    """Probing runs after the cap. Each `controllable()` walks `_frames()`, so
    probing rows that are then withheld spends bus round-trips on output nobody
    sees."""
    monkeypatch.setattr(LW, "_MAX_WINDOWS", 1)
    asked = []

    class _Watched(_Atspi):
        def controllable(self, wid):
            asked.append(wid)
            return ["close"]

    said = _listing(_Watched(ROWS))

    assert asked == ["1"], f"probed {asked}, but only row 1 is shown in:\n{said}"
    assert "Text Editor" in said, said
    # Chromium is **withheld**, which is the whole point: a row that is about to be
    # dropped must not cost a bus enumeration. Asserting it was present would be
    # asserting the cap failed.
    assert "Chromium" not in said, said
    assert "1 more" in said or "withheld" in said or "showing 1 of" in said, (
        f"a row was dropped without saying so, which this codebase calls a quiet "
        f"wrongness:\n{said}")


def test_a_short_probe_budget_is_reported(monkeypatch):
    """An unbounded probe would enumerate the whole bus once per window. The
    budget exists for that, so the tail must be described rather than left to
    look unprobed."""
    monkeypatch.setattr(LW, "_PROBED", 1)
    said = _listing(_Atspi(ROWS))

    assert "were not checked" in said, said
    assert "title_contains" in said, said


def test_controllable_now_has_a_caller():
    """The smallest claim that was false, and the reason this file exists."""
    root = Path(__file__).resolve().parents[1] / "usr/lib/shani-chronoa"
    callers = [p for p in root.rglob("*.py")
               if "controllable(" in p.read_text(encoding="utf-8", errors="replace")
               and p.name != "atspi.py"]
    assert callers, "AtspiBackend.controllable() still has no caller"
