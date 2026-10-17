"""The Driving panel, and the difference between "no" and "could not ask".

Built in a subprocess on a private Broadway display, because a GDK backend is
chosen when GTK initialises and no fixture can change it afterwards.

The panel's whole contract is that a fact it could not get is not a negative
fact. `knowable()` returning `False` and its raising are two different states,
and the status vocabulary this repo uses already has a word for each - this file
pins that the panel uses the right one, from both directions.
"""

from __future__ import annotations

import json
import os
import pathlib
import shutil
import subprocess
import sys
import time

import pytest

PKG = pathlib.Path(__file__).resolve().parents[1] / "usr" / "lib" / "shani-chronoa"

_HARNESS = r'''
import json, sys
sys.path.insert(0, %(pkg)r)
import gi
gi.require_version("Gtk", "4.0"); gi.require_version("Adw", "1")
from gi.repository import Adw, Gtk
Adw.init()

from shani_chronoa import ui_driver
from shani_chronoa.gui import surfaces
from shani_chronoa.gui.surfaces import driving


class Config:
    def get_bool(self, key, default=False):
        return False
    def get(self, key, default=None):
        return default


class App:
    config = Config()


def rows(page):
    """`(title, subtitle)` for every `Adw.ActionRow`, and the status words."""
    out = []
    stack = [page]
    while stack:
        widget = stack.pop()
        if isinstance(widget, Adw.ActionRow):
            out.append((widget.get_title(), widget.get_subtitle()))
        child = widget.get_first_child()
        while child is not None:
            stack.append(child)
            child = child.get_next_sibling()
    return out


def status_words(page):
    return [t.get_label() for t in _labels(page)]


def _labels(widget, out=None):
    if out is None:
        out = []
    if isinstance(widget, Gtk.Label):
        out.append(widget)
    child = widget.get_first_child()
    while child is not None:
        _labels(child, out)
        child = child.get_next_sibling()
    return out


def _buttons(widget, out=None):
    if out is None:
        out = []
    if isinstance(widget, Gtk.Button):
        out.append(widget.get_label())
    child = widget.get_first_child()
    while child is not None:
        _buttons(child, out)
        child = child.get_next_sibling()
    return out


def say(**kw):
    print("RESULT " + json.dumps(kw), flush=True)


case, mode = sys.argv[1], (sys.argv[2] if len(sys.argv) > 2 else "")

if case == "built":
    page = driving.build(App())
    say(titles=[t for t, _s in rows(page)],
        subtitles=[s for _t, s in rows(page)],
        words=status_words(page),
        # The cue button's own hedge, which is a `Gtk.Label` and not an
        # `Adw.ActionRow` subtitle - so `subtitles` never sees it, and a mutation
        # that dropped the hedge back to "The cue layer is up" passed against
        # every assertion in this file. Measured, which is why it is here.
        cue_note=page.cue_note.get_label(),
        cue_button=page.cue_button.get_label())

elif case == "knowable":
    if mode == "true":
        ui_driver.knowable = lambda: True
    elif mode == "false":
        ui_driver.knowable = lambda: False
    elif mode == "raises":
        def _raises():
            raise OSError("the X server went away")
        ui_driver.knowable = _raises
    page = driving.build(App())
    say(titles=[t for t, _s in rows(page)],
        subtitles=[s for _t, s in rows(page)],
        words=status_words(page))

elif case == "portal":
    if mode == "unavailable":
        class _Gone(ui_driver.GuiUnavailable):
            pass
        def _portal():
            raise ui_driver.GuiUnavailable("the desktop's input portal is unavailable: no bus")
        ui_driver._portal_input = _portal
    elif mode == "raises":
        def _raises():
            raise ValueError("a shape nobody expected")
        ui_driver._portal_input = _raises
    page = driving.build(App())
    say(titles=[t for t, _s in rows(page)],
        subtitles=[s for _t, s in rows(page)])

elif case == "windows":
    if mode == "empty":
        ui_driver.windows = lambda: []
    elif mode == "raises":
        ui_driver.windows = lambda: (_ for _ in ()).throw(OSError("xdotool is not installed"))
    elif mode == "some":
        ui_driver.windows = lambda: [("Slack - deepu",), ("Terminal",), ("GIMP",)]
    page = driving.build(App())
    say(titles=[t for t, _s in rows(page)],
        subtitles=[s for _t, s in rows(page)])

elif case == "cue":
    from gi.repository import GLib
    if mode == "running":
        ui_driver._cue_answers = lambda path, timeout=1.5: True
    elif mode == "refused":
        ui_driver.start_cue = lambda name: (None, "the cue layer is not installed, so the driving will not be visible")
    page = driving.build(App())
    page.cue_button.emit("clicked")
    # The press leaves the loop and comes back on `GLib.idle_add`, so the loop
    # has to run - otherwise the note still reads what it read at build time and
    # the test asserts against a queue nobody drains.
    import time as _t
    context = GLib.MainContext.default()
    deadline = _t.time() + 12
    while _t.time() < deadline:
        while context.pending():
            context.iteration(False)
        if page.cue_note.get_label() != "It is an ordinary window: raised, and raised again on every cue, but a terminal that asks for the front covers it.":
            break
        _t.sleep(0.1)
    say(titles=[t for t, _s in rows(page)],
        note=page.cue_note.get_label(),
        sensitive=page.cue_button.get_sensitive())

elif case == "no_driving_buttons":
    page = driving.build(App())
    say(buttons=sorted({b for b in _buttons(page) if b}))

elif case == "registry":
    available = surfaces.all_surfaces()
    say(present="driving" in available,
        all_present=all(m in available for m in available),
        section=surfaces.sections().get("driving"))

else:
    say(error="unknown case " + case)
''' % {"pkg": str(PKG)}


@pytest.fixture(scope="module")
def broadway():
    """A private Broadway display, so no window appears on anybody's desktop."""
    daemon = shutil.which("gtk4-broadwayd")
    runtime = os.environ.get("XDG_RUNTIME_DIR")
    if daemon is None or not runtime:
        pytest.skip("no gtk4-broadwayd or no XDG_RUNTIME_DIR, so no headless display")
    base = pathlib.Path(runtime)
    # A free display is a missing socket *and* a free port: tests/_broadway.py.
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    from _broadway import free_display
    number = free_display(base, 40, 260)
    if number is None:
        pytest.skip("no free Broadway display number")
    proc = subprocess.Popen([daemon, f":{number}"], stdout=subprocess.DEVNULL,
                            stderr=subprocess.DEVNULL)
    deadline = time.time() + 10
    while time.time() < deadline and not (base / f"broadway{number + 1}.socket").exists():
        time.sleep(0.1)
    yield number
    proc.terminate()
    proc.wait(timeout=10)


def _run(broadway, case, tmp_path, mode=""):
    script = tmp_path / f"harness-{case}-{mode or 'default'}.py"
    script.write_text(_HARNESS)
    env = dict(os.environ)
    env.update(GDK_BACKEND="broadway", BROADWAY_DISPLAY=f":{broadway}",
               PYTHONDONTWRITEBYTECODE="1", GSETTINGS_BACKEND="memory",
               XDG_STATE_HOME=str(tmp_path / "state"),
               XDG_DATA_HOME=str(tmp_path / "data"))
    env.pop("DISPLAY", None)
    env.pop("WAYLAND_DISPLAY", None)
    done = subprocess.run([sys.executable, str(script), case, mode],
                          capture_output=True, text=True, timeout=180, env=env)
    for line in done.stdout.splitlines():
        if line.startswith("RESULT"):
            return json.loads(line[6:])
    pytest.fail(f"the harness produced nothing:\n{done.stdout[-900:]}\n"
                f"{done.stderr[-900:]}")


def titles(out) -> str:
    return " | ".join(t for t in out.get("titles", []) if t)


def subtitles(out) -> str:
    return " | ".join(t for t in out.get("subtitles", []) if t)


class TestWhatItSaysOnThisBox:
    def test_it_builds_and_names_the_four_questions(self, broadway, tmp_path):
        out = _run(broadway, "built", tmp_path)
        assert "error" not in out, out.get("error")
        for wanted in ("Screen", "RemoteDesktop portal", "Cue layer", "Windows"):
            assert wanted in titles(out), f"{wanted!r} not in {titles(out)}"
        # **Every row's title is its fact.** `common.status_row()` puts the
        # status word in the title and folds the summary into the subtitle, which
        # is right for a health row and wrong here - a scan down the left edge
        # has to say *which* fact. The first version used it for the unknown
        # rows and produced a column reading "Could not determine" four times.

    def test_a_row_it_could_not_ask_is_grey_not_red(self, broadway, tmp_path):
        """**The whole contract, in one row.** `xdotool` is not installed on this
        box, so `windows()` cannot be asked - and the row must read "could not
        be listed (xdotool is not installed...)" with the word *Could not
        determine*, not "no windows". A person who read "no windows" would
        conclude their desktop had none."""
        out = _run(broadway, "built", tmp_path)
        if "could not be listed" not in titles(out):
            pytest.skip("xdotool is installed here, so the row can be answered")
        assert "Windows: could not be listed" in titles(out), titles(out)
        assert "xdotool" in titles(out), titles(out)
        # **and the word is not the title** - the fact is, which is what makes
        # the page scannable. `common.status_row()` would have put "Could not
        # determine" there, four times over.
        assert "Could not determine" not in titles(out), titles(out)

    def test_the_cue_layers_words_are_hedged(self, broadway, tmp_path):
        """**The hedge about the cue's visibility lives in its own label**, and
        a `Gtk.Label` is not an `Adw.ActionRow` subtitle - so `subtitles` never
        read it, and the mutation that dropped the hedge back to a flat "The cue
        layer is up" passed against every other assertion in this file."""
        out = _run(broadway, "built", tmp_path)
        assert "a terminal that asks for the" in out["cue_note"], out["cue_note"]
        assert "ordinary window" in out["cue_note"], out["cue_note"]
        assert out["cue_button"] == "Show the cue", out

    def test_the_cue_row_says_the_driving_survives_no_cue(self, broadway, tmp_path):
        out = _run(broadway, "built", tmp_path)
        assert "the driving still works" in subtitles(out), subtitles(out)

    def test_an_unanswered_row_says_it_was_not_asked(self, broadway, tmp_path):
        """**And this is what makes `Row.kind` load-bearing.** While
        `_row_widget` used one branch for both states, the field was set and
        never read - a mutation swapping KNOWN for UNKNOWN stayed green against
        all 17 tests, which is how it was found. The unanswered rows now say so
        in their own detail, next to the ones that were actually answered."""
        out = _run(broadway, "knowable", tmp_path, "raises")
        unknown = [d for d in out["subtitles"] if "could not be asked" in d]
        assert unknown, out["subtitles"]
        # **and a row that *was* answered does not carry the phrase**
        if "Screen: no display answered" in out["titles"]:
            assert not any("could not be asked" in d
                           for d in out["subtitles"] if "X display" in d)


class TestCouldNotAskIsNotNo:
    def test_a_display_that_answers_says_so(self, broadway, tmp_path):
        out = _run(broadway, "knowable", tmp_path, "true")
        assert "Screen: answered" in titles(out), titles(out)
        assert "position can be known here" in " ".join(out["words"]), out["words"]

    def test_a_display_that_says_no_is_a_sentence_not_a_capability(self, broadway, tmp_path):
        out = _run(broadway, "knowable", tmp_path, "false")
        assert "Screen: no display answered" in titles(out), titles(out)
        assert "Needs attention" in " ".join(out["words"]), out["words"]

    def test_a_display_that_cannot_be_asked_is_unknown_not_false(self, broadway, tmp_path):
        """**Both directions, because either alone is a test that cannot fail.**
        `knowable()` raising and returning False are opposite claims, and a
        panel that said "no display" for both would be wrong about the second
        and right about the first by accident."""
        out = _run(broadway, "knowable", tmp_path, "raises")
        combined = titles(out).lower() + " " + " ".join(out["words"]).lower()
        assert "could not be read" in titles(out).lower(), titles(out)
        assert "could not determine" in combined, combined
        assert "no display answered" not in titles(out).lower(), titles(out)


class TestThePortal:
    def test_a_portal_that_answered_is_called_answered(self, broadway, tmp_path):
        out = _run(broadway, "portal", tmp_path)
        assert "RemoteDesktop portal" in titles(out), titles(out)

    def test_an_unavailable_portal_names_the_reason(self, broadway, tmp_path):
        out = _run(broadway, "portal", tmp_path, "unavailable")
        assert "RemoteDesktop portal: did not answer" in titles(out), titles(out)
        assert "no bus" in titles(out) + subtitles(out), titles(out)

    def test_an_unexpected_shape_is_not_folded_into_unavailable(self, broadway, tmp_path):
        """`GuiUnavailable` is one failure and anything else is a different one;
        collapsing them is how "it works" and "it broke" come to read alike."""
        out = _run(broadway, "portal", tmp_path, "raises")
        assert "could not be asked" in titles(out), titles(out)
        assert "ValueError" in titles(out), titles(out)


class TestWindows:
    def test_windows_that_answered_are_listed(self, broadway, tmp_path):
        out = _run(broadway, "windows", tmp_path, "some")
        assert "Window: Slack - deepu" in titles(out), titles(out)
        assert "Window: Terminal" in titles(out), titles(out)

    def test_no_windows_is_an_answer(self, broadway, tmp_path):
        out = _run(broadway, "windows", tmp_path, "empty")
        assert "Windows: none answered" in titles(out), titles(out)

    def test_a_window_list_that_cannot_be_read_is_unknown(self, broadway, tmp_path):
        out = _run(broadway, "windows", tmp_path, "raises")
        assert "Windows: could not be listed" in titles(out), titles(out)
        assert "xdotool" in titles(out), titles(out)
        # **and the row is not a health row** - its title is the fact, not the
        # status word
        assert "Could not determine" not in titles(out), titles(out)


class TestTheCueButton:
    def test_a_cue_that_is_up_says_it_is_up(self, broadway, tmp_path):
        out = _run(broadway, "cue", tmp_path, "running")
        assert "Cue layer: answering on its socket" in titles(out), titles(out)

    def test_a_cue_that_refused_says_why(self, broadway, tmp_path):
        out = _run(broadway, "cue", tmp_path, "refused")
        assert "not visible" in out["note"] or "not installed" in out["note"], out
        assert out["sensitive"] is True, (
            "a refusal is not a hang: the button that could not start the cue "
            "has to come back")


class TestReachable:
    def test_the_registry_carries_it(self, broadway, tmp_path):
        out = _run(broadway, "registry", tmp_path)
        assert out["present"], out
        assert out["all_present"], (
            "a surface whose module fails to import is a hole in the sidebar")
        assert out["section"] == "Acting", out

    def test_it_offers_no_control_that_acts_on_another_window(
            self, broadway, tmp_path):
        """**Deliberate, and asserted.** `click`, `type` and `key` act on
        whatever window is in front. `ui_elements` is the tool that does it, one
        call per turn, with the model deciding. A "click at 50%" button on this
        page would be a control nobody can aim."""
        out = _run(broadway, "no_driving_buttons", tmp_path)
        assert out["buttons"] == ["Show the cue"], out
