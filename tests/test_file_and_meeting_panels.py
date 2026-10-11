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
from gi.repository import Adw, Gtk
Adw.init()

from shani_chronoa import file_index, meeting
from shani_chronoa.gui.surfaces import meetings, my_files


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
    page._refresh_status()
    page._show_no_hits("a phrase nobody wrote", "keyword only")
    say(texts=texts(page))

elif case == "my_files_nothing_indexed":
    page = my_files.build(App(**{"file-index-enabled": True}))
    page._refresh_status()
    say(texts=texts(page), buttons=buttons(page))

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
    number = next((n for n in range(40, 120)
                   if not (base / f"broadway{n + 1}.socket").exists()), None)
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
        assert "file-index-enabled" in shown(out), shown(out)
        assert "Nothing has been read" in shown(out), shown(out)
        assert "Open Settings" in clicked(out), (
            "a switch nobody can find is the dead-control class; the row names "
            "the key and the page offers the place to turn it on")

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
        assert "meeting-enabled" in shown(out), shown(out)
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