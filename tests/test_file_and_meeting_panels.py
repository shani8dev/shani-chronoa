"""The two panels that gave `search_my_files` and `meeting` a surface.

**Each panel is built in a subprocess on a private Broadway display**, because a
GDK backend is chosen when GTK initialises and no fixture can change it
afterwards — so a test that builds a widget in-process measures whichever
backend the rest of the suite happened to pick. The subprocess emits one
`RESULT <json>` line, which is what the assertions read.

**The assertions are on the words, not on the widgets.** A panel that
constructs cleanly and says nothing has still failed, and the specific claim
these two panels have to keep is the one this repo keeps paying for: *"nothing
found" is not the same sentence as "nothing was searched"*, and *"off on
purpose" is not the same sentence as "broken"*.

Nothing is stubbed that could make the panel agree with itself: `file_index` and
`meeting` are the real modules against a real (temporary) index and a real
(permissions-refusing) WAV.
"""

from __future__ import annotations

import json
import os
import pathlib
import shutil
import subprocess
import sys
import tempfile
import time

import pytest

PKG = pathlib.Path(__file__).resolve().parents[1] / "usr" / "lib" / "shani-chronoa"

_HARNESS = r'''
import json, os, pathlib, sys, tempfile
sys.path.insert(0, %(pkg)r)
import gi
gi.require_version("Gtk", "4.0"); gi.require_version("Adw", "1")
from gi.repository import Adw, GLib, Gtk
Adw.init()

from shani_chronoa import file_index, meeting
from shani_chronoa.gui.surfaces import (meetings, my_files,  # noqa: E402
                                     routines, screen_share)


class Config:
    def __init__(self, **flags):
        self._flags = flags
    def get_bool(self, key, default=False):
        return bool(self._flags.get(key, default))
    def get(self, key, default=None):
        return default


class App:
    def __init__(self, **flags):
        self.config = Config(**flags)


def texts(widget, out=None):
    """Every label in a tree, as strings. What a person would read."""
    if out is None:
        out = []
    if isinstance(widget, Gtk.Label):
        out.append(widget.get_label())
    child = widget.get_first_child()
    while child is not None:
        texts(child, out)
        child = child.get_next_sibling()
    return out


def buttons(widget, out=None):
    if out is None:
        out = []
    if isinstance(widget, Gtk.Button):
        out.append(widget.get_label())
    child = widget.get_first_child()
    while child is not None:
        buttons(child, out)
        child = child.get_next_sibling()
    return out


def _descend(widget, out=None):
    """The tree as widgets, so a case can press what a person would press."""
    if out is None:
        out = []
    out.append(widget)
    child = widget.get_first_child()
    while child is not None:
        _descend(child, out)
        child = child.get_next_sibling()
    return out


def say(**kw):
    print("RESULT " + json.dumps(kw), flush=True)


case = sys.argv[1]

if case == "my_files_indexed":
    root = pathlib.Path(sys.argv[2])
    (root / "sub").mkdir(parents=True, exist_ok=True)
    (root / "wifi.txt").write_text("the wifi box drops the link in the hall\n")
    (root / "sub" / "btrfs.md").write_text("rollback needs two boots\n")
    file_index.index_directory(root, embed=False)
    page = my_files.build(App(**{"file-index-enabled": True}))
    # **Point the panel at the directory it indexed.** `_directory()` defaults
    # to `$HOME`, and `status(root=)` is confined to the directory it is given,
    # so refreshing first would ask about `$HOME` and find nothing - the panel
    # would answer "nothing is indexed" over an index this very case just built.
    # That is also what the panel *should* do, and it is asserted elsewhere.
    page.path_entry.set_text(str(root))
    page._refresh_status()
    hits, how = file_index.search(str(root), "wifi box", limit=8, embed=False)
    page._show_hits(hits, how, "wifi box")
    say(texts=texts(page), buttons=buttons(page), hits=len(hits), how=how)

elif case == "my_files_no_hits":
    root = pathlib.Path(sys.argv[2])
    root.mkdir(parents=True, exist_ok=True)
    (root / "a.txt").write_text("the wifi box drops the link\n")
    file_index.index_directory(root, embed=False)
    page = my_files.build(App(**{"file-index-enabled": True}))
    page.path_entry.set_text(str(root))
    page._refresh_status()
    page._show_no_hits("a phrase nobody wrote", "keyword only")
    say(texts=texts(page))

elif case == "my_files_nothing_indexed":
    page = my_files.build(App(**{"file-index-enabled": True}))
    page._refresh_status()
    say(texts=texts(page), buttons=buttons(page))
    # no path here: this case is the *empty* answer, and `$HOME` is not indexed

elif case == "my_files_two_directories":
    base = pathlib.Path(sys.argv[2])
    notes, work = base / "notes", base / "work"
    for directory, body in ((notes, "the wifi box drops the link"),
                            (work, "the wifi box is in the office")):
        directory.mkdir(parents=True, exist_ok=True)
        (directory / "note.txt").write_text(body)
    db = file_index._connect()
    file_index.index_directory(notes, embed=False, db=db)
    file_index.index_directory(work, embed=False, db=db)
    page = my_files.build(App(**{"file-index-enabled": True}))
    page.path_entry.set_text(str(notes))
    page._refresh_status()
    hits, how = file_index.search(str(notes), "wifi box", embed=False, db=db)
    say(texts=texts(page), hits=len(hits),
        paths=[h["path"].replace(str(base) + "/", "") for h in hits])

elif case == "my_files_consent_off":
    page = my_files.build(App(**{"file-index-enabled": False}))
    page._refresh_status()
    say(texts=texts(page), buttons=buttons(page))

elif case == "my_files_search_by_a_path":
    root = pathlib.Path(sys.argv[2])
    root.mkdir(parents=True, exist_ok=True)
    (root / "deep").mkdir(exist_ok=True)
    (root / "deep" / "note.md").write_text("the btrfs slot swap needs two boots\n")
    file_index.index_directory(root, embed=False)
    page = my_files.build(App(**{"file-index-enabled": True}))
    page._refresh_status()
    hits, how = file_index.search(str(root), "btrfs slot", limit=8, embed=False)
    page._show_hits(hits, how, "btrfs slot")
    say(texts=texts(page), hits=len(hits))

elif case == "meetings_listed":
    store = meeting.recordings_dir()
    (store / "meeting-20261011-090000.wav").write_bytes(b"RIFF" + b"\0" * 4096)
    (store / "meeting-20261009-140000.wav").write_bytes(b"RIFF" + b"\0" * 9000)
    page = meetings.build(App(**{"meeting-enabled": True}))
    page._refresh_status()
    page._refresh_list()
    say(texts=texts(page), buttons=buttons(page))

elif case == "meetings_consent_off":
    store = meeting.recordings_dir()
    (store / "meeting-20261011-090000.wav").write_bytes(b"RIFF" + b"\0" * 4096)
    page = meetings.build(App(**{"meeting-enabled": False}))
    page._refresh_status()
    page._refresh_list()
    say(texts=texts(page), buttons=buttons(page))

elif case == "meetings_empty":
    page = meetings.build(App(**{"meeting-enabled": True}))
    page._refresh_status()
    page._refresh_list()
    say(texts=texts(page), buttons=buttons(page))

elif case == "meetings_clock":
    import threading, time
    page = meetings.build(App(**{"meeting-enabled": True}))
    toggle = [w for w in _descend(page)
              if isinstance(w, Gtk.ToggleButton)][0]
    clock = [w for w in _descend(page)
             if isinstance(w, Gtk.Label) and w.get_label() == ""][0]

    def stubbed_capture(timeout=7200.0, recorder=None, on_level=None):
        time.sleep(2.6)
        return meeting.MeetingResult(wav=None, seconds=12.0,
                                     why=meeting.END_SILENCE)

    real = meeting.capture
    meeting.capture = stubbed_capture
    try:
        toggle.set_active(True)
        ctx = GLib.MainContext.default()
        deadline = time.time() + 5
        seen, started = [], time.time()
        while time.time() < deadline:
            while ctx.pending():
                ctx.iteration(False)
            now = clock.get_label()
            if now and (not seen or seen[-1] != now):
                seen.append(now)
            time.sleep(0.05)
        # once the capture ended, the clock must stop rather than naming
        # times on a panel that is no longer recording
        say(values=seen, advanced=len(set(seen)) > 1,
            first=seen[0] if seen else "", stopped=not clock.get_label(),
            elapsed=round(time.time() - started, 2))
    finally:
        meeting.capture = real
        toggle.set_active(False)

elif case == "meetings_record_gate_off":
    store = meeting.recordings_dir()
    (store / "meeting-20261011-090000.wav").write_bytes(b"RIFF" + b"\0" * 4096)
    page = meetings.build(App(**{"meeting-enabled": False}))
    toggle = [w for w in _descend(page)
              if isinstance(w, Gtk.ToggleButton)][0]
    rows = [w for w in _descend(page) if isinstance(w, Gtk.Button)]
    transcribe = [b for b in rows if b.get_label() == "Transcribe"][0]
    notes = [b for b in rows if b.get_label() == "Notes"][0]
    say(record_sensitive=toggle.get_sensitive(),
        record_tooltip=toggle.get_tooltip_text(),
        buttons=[b.get_label() or "" for b in rows],
        transcribe_sensitive=transcribe.get_sensitive(),
        notes_sensitive=notes.get_sensitive())

elif case == "meetings_record_gate_on":
    store = meeting.recordings_dir()
    (store / "meeting-20261011-090000.wav").write_bytes(b"RIFF" + b"\0" * 4096)
    page = meetings.build(App(**{"meeting-enabled": True}))
    toggle = [w for w in _descend(page)
              if isinstance(w, Gtk.ToggleButton)][0]
    say(record_sensitive=toggle.get_sensitive(),
        record_tooltip=toggle.get_tooltip_text())

elif case == "routines_listed":
    # Three routines, one of them named with a bare ampersand - which is how
    # `common.row`'s escaping was found: `Adw.ActionRow` parses its title as
    # Pango markup, so "weather & news" was a parse error and an empty label.
    # **Seed where the app actually reads, not beside the work directory.**
    # `_run` points `XDG_DATA_HOME` at `<tmp>/data`, and `routines._path()`
    # resolves through `files.data_home()` from that variable. My first version
    # wrote beside `sys.argv[2]` and the panel saw an empty store - so the test
    # asserted the empty state while believing it had listed three routines.
    root = pathlib.Path(os.environ["XDG_DATA_HOME"])
    store = root / "shani-chronoa"
    store.mkdir(parents=True, exist_ok=True)
    import json as _json
    (store / "routines.json").write_text(_json.dumps({
        "good morning": {"request": "weather and my calendar", "phrases": []},
        "weather & news": {"request": "forecast and headlines", "phrases": []},
        "wrap up": {"request": "summarise today", "phrases": ["end the day"]},
    }))
    page = routines.build(App())
    say(words=texts(page), buttons=buttons(page),
        has_three_runs=buttons(page).count("Run") == 3)

elif case == "routines_empty":
    page = routines.build(App())
    say(words=texts(page), buttons=buttons(page),
        says_no_routines=any("No routines yet" in t for t in texts(page)))

elif case == "screen_share_controls":
    page = screen_share.build(App())
    say(words=texts(page), buttons=buttons(page),
        has_check_and_stop=all(b in buttons(page) for b in ("Check", "Start", "Stop")))

elif case == "registry":
    from shani_chronoa.gui import surfaces
    available = surfaces.available_surfaces()
    say(present=[n for n in ("my_files", "meetings") if n in available],
        sections={n: surfaces.sections().get(n) for n in ("my_files", "meetings")},
        targets={n: surfaces.settings_target(n) for n in ("my_files", "meetings")},
        all_present=all(m in available for m in available))
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
    number = free_display(base, 40, 2620)
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


def _run(broadway, case, tmp_path, *extra):
    """Build the panel in a subprocess and return the words it put on screen."""
    script = tmp_path / f"harness-{case}.py"
    script.write_text(_HARNESS)
    work = tmp_path / case
    env = dict(os.environ)
    env.update(GDK_BACKEND="broadway", BROADWAY_DISPLAY=f":{broadway}",
               PYTHONDONTWRITEBYTECODE="1", GSETTINGS_BACKEND="memory",
               XDG_STATE_HOME=str(tmp_path / "state"),
               XDG_DATA_HOME=str(tmp_path / "data"))
    env.pop("DISPLAY", None)
    env.pop("WAYLAND_DISPLAY", None)
    done = subprocess.run([sys.executable, str(script), case, str(work), *extra],
                          capture_output=True, text=True, timeout=180, env=env)
    for line in done.stdout.splitlines():
        if line.startswith("RESULT"):
            return json.loads(line[6:])
    pytest.fail(f"the harness produced nothing:\n{done.stdout[-900:]}\n"
                f"{done.stderr[-900:]}")


def shown(out) -> str:
    return " | ".join(t for t in out.get("texts", []) if t)


def clicked(out) -> str:
    return " | ".join(b for b in out.get("buttons", []) if b)


def squashed_text(out) -> str:
    """Every label with markup and whitespace removed, so a path is one path.

    Two deliberate things get in the way of asserting on a path as it reads.
    `paths_in()` puts the path in `<tt>` markup, and `wrap_label()` caps the
    label's width - which on a long path lands *inside* the file name, so
    `wifi.txt` renders as `wi\nfi.txt` on a narrow panel. Both are the layout
    working; neither is worth a red test. This asks "is this path on the page",
    not "did it fit on one line".

    Removing the markup is what makes it safe to strip *all* whitespace rather
    than collapsing it: with `<tt>` left in, a path and its neighbours are
    separated by tag text and collapsing spaces would not join them.
    """
    import re
    joined = " ".join(t for t in out.get("texts", []) if t)
    return re.sub(r"\s+", "", re.sub(r"<[^>]*>", "", joined))


class TestTheSearchPanel:
    def test_it_says_how_many_files_are_indexed_and_where(self, broadway, tmp_path):
        out = _run(broadway, "my_files_indexed", tmp_path)
        assert "error" not in out, out.get("error")
        assert "2 file(s)" in shown(out), shown(out)
        assert "none with an embedding" in shown(out), (
            "the panel must say the search is keyword-only, because a box of "
            "results with no such line reads as though it searched by meaning")

    def test_it_says_keyword_only_rather_than_pretending(self, broadway, tmp_path):
        out = _run(broadway, "my_files_indexed", tmp_path)
        assert "keyword only" in shown(out), shown(out)

    def test_a_hit_shows_the_path_and_its_own_text(self, broadway, tmp_path):
        out = _run(broadway, "my_files_indexed", tmp_path)
        assert out["hits"] == 1, out
        # Squashed: `paths_in()` wraps a long path at its separators, which is
        # exactly why it is used - a 140-character title does not wrap and widens
        # the page. A substring test would be asserting against the wrapping.
        squashed = squashed_text(out)
        assert "/wifi.txt" in squashed, squashed
        assert "thewifiboxdropsthelinkinthehall" in squashed, (
            "a search result with no excerpt is a filename and a promise")

    def test_a_deep_path_does_not_become_one_long_line(self, broadway, tmp_path):
        """The hit row carries a `paths_in()` label, not a 140-character title.

        `Adw.ActionRow`'s title does not wrap, so a full path as one is the
        layout defect this repo has measured on every surface it has checked -
        and `common.row()` raises `TypeError: Must be string, not Label` for it
        anyway, which is how the first version of this row was found broken.
        """
        out = _run(broadway, "my_files_search_by_a_path", tmp_path)
        assert out["hits"] >= 1, out
        assert "/deep/note.md" in squashed_text(out), squashed_text(out)
        assert "btrfs" in squashed_text(out), squashed_text(out)

    def test_nothing_matching_is_not_the_same_as_nothing_indexed(self, broadway, tmp_path):
        out = _run(broadway, "my_files_no_hits", tmp_path)
        assert "Nothing in the index matches" in shown(out), shown(out)
        assert "Nothing is indexed" not in shown(out), (
            "an indexed directory with no match must not be reported as empty - "
            "that sends the person to reindex over a query that was too specific")
        assert "1 file(s)" in shown(out), (
            "the sentence should carry how much was actually searched")

    def test_an_unindexed_directory_says_so_and_offers_the_button(self, broadway, tmp_path):
        out = _run(broadway, "my_files_nothing_indexed", tmp_path)
        assert "Nothing is indexed yet" in shown(out), shown(out)
        assert "Index this directory" in clicked(out), clicked(out)

    def test_consent_off_names_the_switch_and_refuses(self, broadway, tmp_path):
        out = _run(broadway, "my_files_consent_off", tmp_path)
        # Named as Settings names it, and turned on from here (2026-10-14):
        # the raw key and a trip to Settings were the dead end.
        assert "Let Chronoa index and search your own files" in shown(out), shown(out)
        assert "file-index-enabled" not in shown(out), shown(out)
        assert "Nothing has been read" in shown(out), shown(out)
        assert "Turn on" in clicked(out), (
            "a switch nobody can find is the dead-control class; the banner "
            "names it and turns it on in place")

    def test_consent_off_does_not_claim_the_files_are_absent(self, broadway, tmp_path):
        """**The direction that reads as a fact.** "Nothing is indexed" under a
        shut consent key says the person's files are missing when nothing was
        read at all."""
        out = _run(broadway, "my_files_consent_off", tmp_path)
        assert "Nothing is indexed" not in shown(out), shown(out)


class TestTheMeetingsPanel:
    def test_it_lists_the_meetings_that_are_kept(self, broadway, tmp_path):
        out = _run(broadway, "meetings_listed", tmp_path)
        assert "2 meeting(s) kept" in shown(out), shown(out)
        assert "meeting-20261011-090000" in shown(out), shown(out)

    def test_each_meeting_offers_the_two_things_worth_doing(self, broadway, tmp_path):
        out = _run(broadway, "meetings_listed", tmp_path)
        assert "Transcribe" in clicked(out), clicked(out)
        assert "Notes" in clicked(out), clicked(out)

    def test_an_empty_shelf_is_an_empty_state_not_an_error(self, broadway, tmp_path):
        out = _run(broadway, "meetings_empty", tmp_path)
        assert "No meetings recorded yet" in shown(out), shown(out)

    def test_consent_off_names_the_switch(self, broadway, tmp_path):
        out = _run(broadway, "meetings_consent_off", tmp_path)
        assert "Let Chronoa record a whole meeting" in shown(out), shown(out)
        assert "meeting-enabled" not in shown(out), shown(out)
        assert "microphone will not be opened" in shown(out), shown(out)

    def test_consent_off_says_the_shelf_is_still_readable(self, broadway, tmp_path):
        """**The consent key gates the microphone, not the shelf.** `Transcribe`
        and `Notes` read a WAV already on disk and open no capture device, so
        turning the switch off withholds recording and not the recordings.

        So neither hiding the list nor leaving it unexplained is right: hiding it
        claims the person has no meetings when they have one, and showing it
        under a bare "Turned off" is a grey banner over working buttons. The
        panel says both halves, and this is that sentence.
        """
        out = _run(broadway, "meetings_consent_off", tmp_path)
        assert "microphone will not be opened" in shown(out), shown(out)
        assert "still be read and written up" in shown(out), (
            "with the switch off and meetings on disk, the page must say the "
            "existing ones are still readable - Transcribe opens no microphone")

    def test_consent_off_still_offers_the_existing_meeting(self, broadway, tmp_path):
        """The other half of the same fact: refusing to read back a meeting
        somebody recorded before they turned the switch off is not what "let
        Chronoa record a whole meeting" grants or withholds."""
        out = _run(broadway, "meetings_consent_off", tmp_path)
        assert "meeting-20261011-090000" in shown(out), shown(out)
        assert "Transcribe" in clicked(out), clicked(out)


class TestTheCountAndTheResultsAgree:
    """The defect these two tests exist for: the panel's count and its search
    described different directories, so neither could be checked against the
    other.

    Measured before the fix, with two directories indexed: the status row read
    `2 file(s) under <the shared parent>` over a search that returned one file
    from `notes` - including the file in `work`, which was never asked about.
    """

    def test_the_count_names_the_directory_the_search_answers_from(self, broadway, tmp_path):
        out = _run(broadway, "my_files_two_directories", tmp_path)
        assert "error" not in out, out.get("error")
        joined = squashed_text(out)
        assert "1file(s)under" in joined, joined
        assert "/notes" in joined, joined
        # **and the results are the same directory's**
        assert out["paths"] == ["notes/note.txt"], out["paths"]

    def test_a_hit_from_the_other_directory_is_not_returned(self, broadway, tmp_path):
        out = _run(broadway, "my_files_two_directories", tmp_path)
        assert "work/note.txt" not in squashed_text(out), squashed_text(out)
        assert "work/note.txt" not in out["paths"], out["paths"]


class TestBothAreReachable:
    def test_the_registry_carries_them(self, broadway, tmp_path):
        out = _run(broadway, "registry", tmp_path)
        assert out["present"] == ["my_files", "meetings"], out
        assert out["all_present"], (
            "a surface whose module fails to import is a hole in the sidebar, "
            "not a window that will not open")

    def test_each_lands_in_a_section_and_a_settings_target(self, broadway, tmp_path):
        out = _run(broadway, "registry", tmp_path)
        for name in ("my_files", "meetings"):
            assert out["sections"][name] == "Remembering", (
                f"{name} is under {out['sections'][name]!r}; the sidebar groups "
                "by organ and this is what the thing remembers")
            assert out["targets"][name] == "settings:privacy", (
                f"{name} -> {out['targets'][name]!r}; both switches are Privacy "
                "rows, so a gear anywhere else opens a page of unrelated things")

class TestTheNewSurfacesAreReadable:
    """**Looked at, not merely built.**

    Both panels already have unit tests that assert what the code returns. These
    build them in a subprocess on a private Broadway display and read the words a
    person would see - the difference between "the widget was constructed" and
    "the panel says something". The repo has recorded that distinction more than
    once: a width test passed while the organ strip rendered as one run-on
    string.
    """

    def test_a_listed_routine_says_what_it_asks_for(self, broadway, tmp_path):
        out = _run(broadway, "routines_listed", tmp_path)
        assert out["has_three_runs"], out["buttons"]
        words = "\n".join(out["words"])
        assert "good morning" in words
        assert "weather and my calendar" in words, (
            "a row that shows the phrase and not the request reads as one that works")
        assert "say:" in words

    def test_a_routine_named_with_an_ampersand_renders(self, broadway, tmp_path):
        """The node of the matter: `Adw.ActionRow` parses its title as Pango.

        Measured - `set_title("weather & news")` fails to parse and empties the
        label, which is how `common.row`'s escaping was found. A panel rendering
        `weather &amp; news` in the source is libadwaita showing `weather & news`.
        """
        out = _run(broadway, "routines_listed", tmp_path)
        words = " ".join(out["words"])
        assert "weather" in words and "news" in words, words
        assert "forecast and headlines" in words

    def test_an_empty_store_says_there_is_nothing(self, broadway, tmp_path):
        out = _run(broadway, "routines_empty", tmp_path)
        assert out["says_no_routines"], out["words"]

    def test_screen_share_has_all_three_controls(self, broadway, tmp_path):
        out = _run(broadway, "screen_share_controls", tmp_path)
        assert out["has_check_and_stop"], out["buttons"]
        words = " ".join(out["words"])
        assert "Can this machine share?" in words
        # The 90-second probe is the reason this panel exists in the shape it does.
        assert "takes a while" in words, words

    def test_the_recording_clock_actually_moves(self, broadway, tmp_path):
        """**The timer was never scheduled.** `_tick` was written with a docstring
        about not looking hung, and nothing ever called it.

        Measured before this: the label read "Recording — up to 15 minutes" and
        then held that text for the whole time a stub capture held the panel
        open — one distinct value, for a page whose only live feedback is that
        clock. A person recording a two-hour meeting got no feedback at all for
        two hours, which is exactly the failure the docstring says it prevents.

        This presses the real toggle, runs the real main loop, and reads the
        label as it changes.
        """
        out = _run(broadway, "meetings_clock", tmp_path)
        assert out["advanced"], (
            "the elapsed clock never moved: " + " | ".join(out["values"]))

    def test_the_clock_names_seconds_and_not_just_that_it_is_running(self, broadway, tmp_path):
        """The first version of the label said "Recording — up to 15 minutes",
        which is the ceiling, not the elapsed time. A clock that shows only the
        ceiling is indistinguishable from a hung panel, which is the other half
        of the same defect.
        """
        out = _run(broadway, "meetings_clock", tmp_path)
        for value in out["values"][1:]:
            assert "up to" not in value, value
            # some form of elapsed time is on screen
            assert "m " in value or "s" in value, value

    def test_the_clock_stops_when_the_recording_ends(self, broadway, tmp_path):
        """The other direction: a panel that names times on nothing is a wrong
        answer too. **What this actually measures is the label clearing**, and that
        much is a live control - mutating the clear to leave a frozen value on the
        panel fails it.

        **And a documented survivor:** removing the `_stop_tick()` call from
        `_reset_controls` leaves this green in every run, because `_tick` retires
        itself the moment `_recording` goes false. The call is defence-in-depth
        on the source lifetime, not something this test protects - so it is
        recorded as a survivor rather than claimed as covered.
        """
        out = _run(broadway, "meetings_clock", tmp_path)
        assert out["stopped"], (
            "the label was still alive after the recording ended: "
            + " | ".join(out["values"]))


class TestTheRecordButtonSaysBeforeItRefuses:
    """`_recording_possible` was written to gate the Record button and never
    called — a method with a docstring about the gate, beside a button that stayed
    live and refused only on click.

    **Measured before:** `meeting-enabled=False` built a panel whose Record button
    was `sensitive=True` with the tooltip "Start recording a meeting". A person
    pressed it, and the panel told them no. A greyed button naming the setting is
    a different sentence from an invitation followed by a refusal.

    And the asymmetry is the point: `_recording_possible`'s docstring is explicit
    that `Transcribe` and `Notes` do **not** consult it, because reading back a
    meeting recorded before the switch went off needs no microphone. So the same
    shelf buttons have to stay live.
    """

    def test_record_is_greyed_and_says_why_with_consent_off(self, broadway, tmp_path):
        out = _run(broadway, "meetings_record_gate_off", tmp_path)
        assert not out["record_sensitive"], (
            "Record was live with meeting-enabled off; it refuses only on click")
        # Names the switch as Settings shows it, not its config key.
        assert "Let Chronoa record a whole meeting" in out["record_tooltip"], out["record_tooltip"]

    def test_the_shelf_still_works_with_consent_off(self, broadway, tmp_path):
        """**The trap in fixing the first one.** Gating everything that touches a
        meeting refuses to read back a meeting the person recorded before turning
        the switch off — which is not what "let Chronoa record" means."""
        out = _run(broadway, "meetings_record_gate_off", tmp_path)
        words = " ".join(out["buttons"])
        assert "Transcribe" in words and "Notes" in words, words
        assert out["transcribe_sensitive"] and out["notes_sensitive"], words

    def test_a_granted_switch_means_a_live_button(self, broadway, tmp_path):
        """The control on the other side: a gate that is always closed is a gate
        nobody can tell from a broken one."""
        out = _run(broadway, "meetings_record_gate_on", tmp_path)
        assert out["record_sensitive"], out["record_tooltip"]
        assert "Settings" not in out["record_tooltip"], out["record_tooltip"]
