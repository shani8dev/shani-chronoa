"""The Tool activity panel: what Chronoa ran, and whether it took effect.

This is the only place a user can see the audit trail `tool_tracking.py` writes,
so the things that could make it lie are the things tested here:

- **The verdict leads, not the result.** `result` is the action's own account of
  what it did, so for an action that did not take effect it reads "All done
  successfully." beside `verdict="failed"` -
  `test_tool_tracking.py` pins two records with a byte-identical result and
  opposite verdicts. A panel that led with the result would draw those two rows
  identically, which is the one thing an audit view must not do.
- **`None` is not failure.** It means the call never reached verification at all
  (a non-zero exit, or an exception). Asserted here against the *rendered title*,
  because a `None` shown as a failure is a confident wrong answer a reader cannot
  detect.
- **Absent is not empty is not unreadable.** Four different problems, and the
  copy has to tell them apart; a panel that says "no tool calls recorded" for all
  of them is a plausible wrong answer, which `AGENTS.md` names as worse than
  failing.
- **Rows are searchable.** `_on_search` only sees rows registered in
  `_needle_extra`, so a row built by any other route would silently never be
  findable - and no structural assertion would notice.

No assertion here pins rendered prose wholesale, and none checks a CSS value or a
pixel: the visual regression suite in this repo was deleted precisely because it
could not be made to fail (see `AGENTS.md`). What is asserted is structure, css
classes, and the four verdicts' *distinction* from each other.

Runs in a child process, like `test_adw_initialisation.py` and
`test_approval_settings_surface.py`: libadwaita holds global state that cannot be
set up and torn down per-test in one interpreter, and `tools._TRACKER` is a
module-level singleton whose ring buffer cannot be reset between tests either.
One child runs every scenario in sequence, each against a freshly built real
`SettingsWindow`.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

_REPO = Path(__file__).resolve().parents[1]
_PKG = _REPO / "usr" / "lib" / "shani-chronoa"

_CHILD = r'''
import json, os, pathlib, sys
import gi
gi.require_version("Gtk", "4.0"); gi.require_version("Adw", "1")
from gi.repository import Gtk, Adw, GLib

# Module scope, before any Adw widget: a tree built before this renders nothing.
Adw.init()

sys.path.insert(0, os.environ["CHRON_PKG"])
from shani_chronoa.config import ChronoaConfig
from shani_chronoa import tool_tracking
from shani_chronoa.settings_window import SettingsWindow

OUT = {}
APP = None


class App(Gtk.Application):
    def __init__(self):
        super().__init__(application_id="test.chronoa.toolactivity")
        self.config = ChronoaConfig()
        self.window = None
        self._wake_word_active = False

    def activate_action(self, name, arg):
        pass


def walk(node, out):
    out.append(node)
    c = node.get_first_child()
    while c is not None:
        walk(c, out)
        c = c.get_next_sibling()
    return out


def panel(window):
    """(group, rows, notes) found by css class, exactly as the sibling tests do."""
    nodes = walk(window, [])
    group = next((n for n in nodes if "tool-activity-group" in n.get_css_classes()), None)
    if group is None:
        return None, [], []
    rows = [n for n in walk(group, [])
            if "tool-call-row" in n.get_css_classes()]
    notes = [n for n in walk(group, [])
             if isinstance(n, Adw.PreferencesRow)
             and "tool-call-row" not in n.get_css_classes()]
    return group, rows, notes


def reset(ring=False, log="absent"):
    """Put the two sources into a known state before building a window."""
    if ring:
        from shani_chronoa import tools
        tools._TRACKER._calls.clear()
    path = tool_tracking.LOG_FILE
    path.parent.mkdir(parents=True, exist_ok=True)
    if log == "absent":
        path.unlink(missing_ok=True)
    elif log == "empty":
        path.write_text("")
    elif log == "unreadable":
        path.write_text('{"tool_name": "x", "verdict": "verified"}\n')
        path.chmod(0o000)
    elif log == "garbage":
        path.write_text('not json\n{}\n{"tool_name": 3}\n[1, 2]\n')
    elif log == "hostile":
        path.write_text(json.dumps({
            "timestamp": "2026-09-30T08:00:00+00:00",
            "tool_name": "run <script> & more",
            "args": {"body": "line1\nline2\tindented",
                     "huge": "x" * 9000,
                     "nested": {"a": [1, 2, {"b": None}]}},
            "result": "All done successfully.\n\n  Traceback:\n    boom & <b>",
            "duration_ms": "not a number",
            "origin": {"weird": True},
            "verdict": "failed",
            "evidence": "A & B <b>bold</b> " + "very long. " * 40,
        }) + "\n")
    else:
        raise AssertionError(log)
    if log != "unreadable" and path.exists():
        path.chmod(0o600)
    return path


def seed_all_verdicts():
    """One call per verdict, all of which the log will also hold."""
    from shani_chronoa import tools
    tools._TRACKER.record_call("set_brightness", {"level": 40}, "Set to 40%", 12.0,
                               verdict="verified", evidence="the backlight reads 40%")
    # Byte-identical result to the one above, opposite verdict. If the panel led
    # with the result these two rows would be indistinguishable.
    tools._TRACKER.record_call("set_brightness", {"level": 40}, "Set to 40%", 12.0,
                               verdict="failed", evidence="the backlight never moved")
    tools._TRACKER.record_call("get_datetime", {}, "2026-10-01T16:40:00", 3.0,
                               verdict="unverified")
    tools._TRACKER.record_call("notify", {"summary": "hi"}, "", 1.0, verdict=None)
    tools._TRACKER.record_call("move_pointer", {"x": 10}, "moved", 4.0,
                               origin="unattended", verdict="failed",
                               evidence="pointer did not move")
    # A prior run, on disk only. The ring cannot know about it, so it must not be
    # de-duplicated away.
    with tool_tracking.LOG_FILE.open("a") as fh:
        fh.write(json.dumps({
            "timestamp": "2026-09-30T08:00:00+00:00",
            "tool_name": "get_battery_status", "args": {}, "result": "82%",
            "duration_ms": 210.5, "origin": "user", "verdict": "unverified",
            "evidence": "",
        }) + "\n")


def snapshot(window, label):
    group, rows, notes = panel(window)
    OUT[label] = {
        "group_found": group is not None,
        "title": group.get_title() if group is not None else None,
        "group_classes": list(group.get_css_classes()) if group is not None else [],
        "row_classes": sorted({c for r in rows for c in r.get_css_classes()}),
        "rows": [{"title": r.get_title(), "subtitle": r.get_subtitle(),
                  "tooltip": r.get_tooltip_text()} for r in rows],
        "notes": [{"title": n.get_title(), "subtitle": n.get_subtitle()} for n in notes],
        "needle": len(group._needle_extra) if group is not None else 0,
    }
    return group, rows


def on_activate(a):
    global APP
    APP = a

    # 1. Every verdict, plus one call only the log knows about.
    reset()
    seed_all_verdicts()
    w = SettingsWindow(a)
    g, _ = snapshot(w, "seeded")

    # 2. Negative control: nothing recorded and nothing to read.
    reset(ring=True)
    snapshot(SettingsWindow(a), "empty")

    # 3. A log that exists and holds nothing.
    reset(ring=True, log="empty")
    snapshot(SettingsWindow(a), "zero_byte")

    # 4. A log full of lines that are not records.
    reset(ring=True, log="garbage")
    snapshot(SettingsWindow(a), "garbage")

    # 5. A log that cannot be read.
    reset(ring=True, log="unreadable")
    snapshot(SettingsWindow(a), "unreadable")
    tool_tracking.LOG_FILE.chmod(0o600)

    # 6. A record shaped to break the row: markup characters, newlines, a
    #    duration that is not a number, an origin that is not a string.
    reset(ring=True, log="hostile")
    snapshot(SettingsWindow(a), "hostile")

    # 7 + 8. Search, and the visibility-driven re-render, need settled widgets.
    reset()
    seed_all_verdicts()
    search_window = SettingsWindow(a)

    def search_step():
        group, _rows, _notes = panel(search_window)
        search_window._search.insert_text("verified", 0)

        def read_search():
            OUT["search"] = {
                "group_visible": group.get_visible(),
                "visible": [r.get_title() for r, _t in group._needle_extra
                            if r.get_visible()],
                "registered": [r.get_title() for r, _t in group._needle_extra],
            }
            return False

        GLib.timeout_add(400, read_search)
        return False

    def rerender_step():
        # A real call, recorded while the window already exists, then the
        # visibility signal the window actually connects to.
        from shani_chronoa import tools
        before = len(panel(search_window)[1])
        tools._TRACKER.record_call("download_stt_model", {"key": "base"},
                                   "Fetching", 5.0, verdict="unverified")
        after_record = len(panel(search_window)[1])
        search_window.set_visible(True)
        search_window.set_visible(False)
        search_window.set_visible(True)

        def read_rerender():
            group, rows, _notes = panel(search_window)
            OUT["rerender"] = {
                "before": before,
                "after_record": after_record,
                "after": len(rows),
                "titles": [r.get_title() for r in rows],
            }
            a.quit()
            return False

        GLib.timeout_add(400, read_rerender)
        return False

    GLib.timeout_add(300, search_step)
    GLib.timeout_add(1100, rerender_step)
    return None


APP = App()
APP.connect("activate", on_activate)
GLib.timeout_add(40000, lambda: (APP.quit(), False)[1])
APP.run([])
print("RESULT" + json.dumps(OUT))
'''


@pytest.fixture(scope="module")
def built(tmp_path_factory, compiled_schema_dir):
    """Every scenario, from one child process, against real widgets."""
    import shutil

    work = tmp_path_factory.mktemp("tool-activity")
    runtime = work / "runtime"
    runtime.mkdir(mode=0o700)
    schemas = work / "schemas"
    schemas.mkdir()
    for xml in (_REPO / "usr" / "share" / "glib-2.0" / "schemas").glob("*.xml"):
        shutil.copy2(xml, schemas)
    compiled = subprocess.run(["glib-compile-schemas", str(schemas)], capture_output=True)
    assert compiled.returncode == 0, compiled.stderr
    assert (schemas / "gschemas.compiled").is_file(), "gschemas.compiled not produced"

    env = dict(os.environ)
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    env["CHRON_PKG"] = str(_PKG)
    env["GSETTINGS_SCHEMA_DIR"] = str(schemas)
    env["GSETTINGS_BACKEND"] = "keyfile"
    env["XDG_RUNTIME_DIR"] = str(runtime)
    env["XDG_CONFIG_HOME"] = str(work / "config")
    env["XDG_DATA_HOME"] = str(work / "data")
    env["XDG_STATE_HOME"] = str(work / "state")
    for name in ("config", "data", "state"):
        (work / name).mkdir()

    proc = subprocess.run([sys.executable, "-c", _CHILD], capture_output=True,
                          text=True, timeout=300, env=env, cwd=str(work))
    payload = None
    for line in proc.stdout.splitlines():
        if line.startswith("RESULT"):
            payload = json.loads(line[len("RESULT"):])
    assert payload is not None, (
        f"harness produced no result:\n{proc.stdout}\n{proc.stderr[-3000:]}")
    payload["_stderr"] = proc.stderr
    return payload


class TestThePanelExists:
    def test_the_group_is_built_and_found_by_its_css_class(self, built):
        assert built["seeded"]["group_found"], (
            "no widget carries the 'tool-activity-group' class, so the panel was "
            "not built at all")
        assert built["seeded"]["group_classes"] == ["tool-activity-group"]

    def test_it_is_labelled_and_nested_in_the_page(self, built):
        assert built["seeded"]["title"] == "Tool activity"

    def test_the_call_rows_are_found_by_their_css_class(self, built):
        assert built["seeded"]["row_classes"] == ["tool-call-row"]
        assert built["seeded"]["rows"], "the panel has a group and no rows in it"

    def test_every_row_is_registered_with_the_search_filter(self, built):
        """Rows not in `_needle_extra` are invisible to search, silently.

        `_on_search` walks nothing but that list, so a row built by any other
        route would be unfindable and every structural check would still pass.
        """
        assert built["seeded"]["needle"] == len(built["seeded"]["rows"])

    def test_construction_produced_no_gtk_parenting_or_markup_warning(self, built):
        stderr = built["_stderr"]
        assert "Failed to set text" not in stderr, (
            "a row's title or subtitle was rejected as Pango markup, which leaves "
            f"the label rendering empty rather than raising:\n{stderr[-1500:]}")
        assert "gtk_box_append" not in stderr, stderr[-1500:]


class TestTheFourVerdicts:
    def _titles(self, built):
        return [r["title"] for r in built["seeded"]["rows"]]

    def test_all_four_are_rendered_and_none_raises(self, built):
        assert len(built["seeded"]["rows"]) == 6

    def test_a_verdict_of_none_is_never_shown_as_a_failure(self, built):
        """The crux. `None` means the call never reached verification - a non-zero
        exit, or an exception - which is not the claim that it was checked and did
        not work."""
        rows = [r for r in built["seeded"]["rows"] if "notify" in r["title"]]
        assert len(rows) == 1, f"the None-verdict row is missing: {self._titles(built)}"
        title = rows[0]["title"]
        assert "never reached verification" in title, title
        for wrong in ("not to have taken effect", "failed", "error", "refused"):
            assert wrong not in title.lower(), (
                f"a verdict of None was rendered as {wrong!r}: {title}")

    def test_the_two_records_with_identical_results_stay_distinguishable(self, built):
        """`tests/test_tool_tracking.py` pins byte-identical results with opposite
        verdicts. Rendering by result would draw these two rows the same."""
        rows = [r for r in built["seeded"]["rows"] if "set_brightness" in r["title"]]
        assert len(rows) == 2, [r["title"] for r in built["seeded"]["rows"]]
        assert rows[0]["title"] != rows[1]["title"], (
            "two calls with identical results and opposite verdicts produced "
            "identical rows")
        assert rows[0]["subtitle"] != rows[1]["subtitle"]
        # Order is by timestamp, which the fixture does not control, so the two
        # are matched by which verdict they carry rather than by position.
        pair = {r["title"] for r in rows}
        assert pair == {
            "set_brightness - verified to have taken effect",
            "set_brightness - verified not to have taken effect",
        }, pair

    def test_each_verdict_reads_differently_from_the_others(self, built):
        """Checked against the panel's own vocabulary rather than against the
        four values hardcoded here, so adding a verdict cannot leave this test
        green on a rendering that collapses it onto an existing one."""
        from shani_chronoa.settings_window import _VERDICT_WORDS

        titles = [r["title"] for r in built["seeded"]["rows"]]
        for phrase in _VERDICT_WORDS.values():
            assert any(phrase in t for t in titles), (
                f"no row reads {phrase!r}: {titles}")
        assert len(set(_VERDICT_WORDS.values())) == 4, _VERDICT_WORDS

    def test_an_unattended_call_says_so(self, built):
        """`origin` exists to tell a user-initiated action from an armed rule
        firing unprompted; a row that drops it throws that away."""
        rows = [r for r in built["seeded"]["rows"] if "move_pointer" in r["title"]]
        assert len(rows) == 1
        assert "unattended" in rows[0]["title"], rows[0]["title"]

    def test_a_duration_nobody_recorded_is_not_reported_as_zero(self, built):
        hostile = built["hostile"]["rows"]
        assert len(hostile) == 1
        assert "duration unreadable" in hostile[0]["subtitle"], hostile[0]["subtitle"]
        assert " 0 ms" not in hostile[0]["subtitle"], (
            "an unreadable duration was displayed as 0 ms, which is a "
            "measurement nobody took")


class TestSearchFindsThem:
    def test_a_row_matching_the_needle_is_revealed_and_the_rest_are_hidden(self, built):
        search = built["search"]
        assert search["group_visible"] is True, (
            "the group hid itself even though a row inside it matched")
        assert search["visible"], "a matching row was not revealed"
        for title in search["visible"]:
            assert "verified" in title, title

    def test_a_row_not_matching_the_needle_is_hidden(self, built):
        assert len(built["search"]["visible"]) < len(built["search"]["registered"]), (
            "searching left every row visible, so the needle is not doing anything")


class TestAbsentIsNotEmptyIsNotUnreadable:
    """Four different problems, four different rows.

    A panel that reported one of them for all four is a plausible wrong answer,
    which `AGENTS.md` names as worse than a failure: the user cannot detect it.
    """

    def test_no_log_and_an_empty_ring_say_neither_no_calls_nor_an_error(self, built):
        empty = built["empty"]
        assert empty["rows"] == [], "a call row was invented with nothing recorded"
        assert len(empty["notes"]) == 1, empty["notes"]
        note = empty["notes"][0]
        assert note["title"] == "No log file on disk", note
        assert "error" not in note["subtitle"].lower(), note["subtitle"]
        assert "failed" not in note["subtitle"].lower(), note["subtitle"]

    def test_that_copy_does_not_claim_no_action_has_ever_been_taken(self, built):
        """The negative control.

        `[]` is genuinely ambiguous - the ring is also empty on a machine that ran
        actions in an earlier process - so the copy has to say what it cannot
        know. A test that only asserted the row exists would pass on a confident
        and wrong "No tool calls have been recorded".
        """
        subtitle = built["empty"]["notes"][0]["subtitle"]
        for overclaim in ("no tool calls have been recorded",
                          "no tools have run",
                          "no actions have been",
                          "nothing has been recorded"):
            assert overclaim not in subtitle.lower(), (
                f"the empty state claims {overclaim!r}, which this panel cannot "
                f"know: {subtitle}")

    def test_a_zero_byte_log_is_its_own_state(self, built):
        notes = built["zero_byte"]["notes"]
        assert len(notes) == 1, notes
        assert notes[0]["title"] == "The log is there and empty", notes[0]
        assert "Zero bytes" in notes[0]["subtitle"], notes[0]
        assert built["zero_byte"]["rows"] == []

    def test_a_log_of_lines_that_are_not_records_is_its_own_state(self, built):
        notes = built["garbage"]["notes"]
        assert len(notes) == 1, notes
        assert notes[0]["title"] == "The log holds lines, and none is a record", notes[0]
        assert "4 line(s)" in notes[0]["subtitle"], notes[0]
        # `{}` parses as JSON. Reading its absent `verdict` as None would have
        # displayed it as "never reached verification" - a claim about an action
        # rather than about a line of text.
        assert built["garbage"]["rows"] == []

    def test_an_unreadable_log_is_not_reported_as_an_empty_one(self, built):
        notes = built["unreadable"]["notes"]
        assert len(notes) == 1, notes
        assert notes[0]["title"] == "The log is there, and could not be read", notes[0]
        assert "read failure" in notes[0]["subtitle"], notes[0]
        assert built["unreadable"]["rows"] == []

    def test_the_four_states_name_four_different_titles(self, built):
        titles = {
            built[key]["notes"][0]["title"]
            for key in ("empty", "zero_byte", "garbage", "unreadable")
        }
        assert len(titles) == 4, titles


class TestLiveAndHistoryTogether:
    def test_a_call_in_both_sources_is_shown_once(self, built):
        """`record_call()` appends to the ring *and* to the log, so the newest
        calls arrive twice. A duplicated row is indistinguishable from two actions
        that really happened."""
        titles = [r["title"] for r in built["seeded"]["rows"]]
        # Five live calls plus one prior run that only the log knows about.
        assert len(titles) == 6, titles
        assert len(set(titles)) == len(titles), (
            f"a call appears twice in the panel: {titles}")

    def test_the_prior_run_is_shown_alongside_the_live_calls(self, built):
        titles = [r["title"] for r in built["seeded"]["rows"]]
        assert any("get_battery_status" in t for t in titles), titles

    def test_the_newest_call_is_first(self, built):
        titles = [r["title"] for r in built["seeded"]["rows"]]
        assert "move_pointer" in titles[0], titles


class TestReRendersWhenTheTrailMoves:
    def test_a_call_recorded_after_the_window_was_built_appears_on_reopening(self, built):
        """Driven through `set_visible()`, which is what emits the signal the
        window actually connects to, rather than by calling the handler."""
        rerender = built["rerender"]
        assert rerender["before"] == 6, rerender
        assert any("download_stt_model" in t for t in rerender["titles"]), (
            f"a call recorded while the window existed never reached the panel: "
            f"{rerender['titles']}")

    def test_reopening_does_not_duplicate_rows(self, built):
        """The teardown in `_render_tool_activity` is the only thing stopping the
        rows piling up one set per show/hide."""
        rerender = built["rerender"]
        assert rerender["after"] == rerender["before"] + 1, rerender


class TestHostileRecords:
    """A record is a skill's own prose and a post-condition's own prose. It is
    not text this program wrote, and it is not text this program should be
    parsing as markup."""

    def test_markup_characters_reach_the_row_as_text(self, built):
        row = built["hostile"]["rows"][0]
        assert "<script>" in row["title"] and "& more" in row["title"], row["title"]

    def test_the_row_still_survives_a_record_full_of_markup(self, built):
        row = built["hostile"]["rows"][0]
        assert row["subtitle"], "the subtitle was dropped by a markup parse error"
        assert "<b>bold</b>" in row["subtitle"], row["subtitle"]

    def test_newlines_do_not_reach_the_row(self, built):
        row = built["hostile"]["rows"][0]
        assert "\n" not in row["subtitle"], repr(row["subtitle"])
        assert "\n" not in row["title"], repr(row["title"])
        assert "Traceback: boom" in row["subtitle"], row["subtitle"]

    def test_a_huge_field_is_shortened_and_marked_as_such(self, built):
        row = built["hostile"]["rows"][0]
        assert "..." in row["subtitle"], "a truncated field is not marked"
        assert len(row["subtitle"]) < 500, len(row["subtitle"])

    def test_the_unshortened_text_is_still_reachable(self, built):
        """Truncated here, whole in the tooltip - and the panel says so."""
        row = built["hostile"]["rows"][0]
        assert row["tooltip"], "the full text is nowhere"
        assert len(row["tooltip"]) > len(row["subtitle"])