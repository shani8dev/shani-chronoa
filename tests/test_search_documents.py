"""search_documents: the desktop's own content index, never a guess.

Measured on the images (2026-10-01): with no session, `localsearch search`
fails to connect (GNOME), and `baloosearch6` exits 0 with no output while
`balooctl6 status` says "Baloo Index could not be opened" (Plasma). Both must
come back as "not available", never as "nothing found".
"""

import stat

import pytest

from shani_chronoa.skills import search_documents as sd


def _stub(bindir, name, script):
    p = bindir / name
    p.write_text("#!/bin/sh\n" + script)
    p.chmod(p.stat().st_mode | stat.S_IXUSR)


@pytest.fixture
def bindir(tmp_path, monkeypatch):
    d = tmp_path / "bin"
    d.mkdir()
    monkeypatch.setenv("PATH", f"{d}:/usr/bin:/bin")
    monkeypatch.setattr(sd, "_consent", lambda config: (True, ""))
    return d


def test_it_is_gated_off_by_default(monkeypatch):
    class Off:
        def get_bool(self, key, default=False):
            return False
    monkeypatch.setattr(sd, "ChronoaConfig", Off)
    assert "document-search-enabled" in sd._run({"query": "lease"})


def test_localsearch_results_are_parsed_through_colour_codes(bindir):
    _stub(bindir, "localsearch", 'printf "Results:\\n  \\033[32mfile:///home/u/My%%20Lease.pdf\\033[0m\\n  …the lease…\\n"\n')
    out = sd._run({"query": "lease"})
    assert "/home/u/My Lease.pdf" in out and out.startswith("1 file")


def test_localsearch_that_cannot_connect_is_not_nothing_found(bindir):
    _stub(bindir, "localsearch", 'echo "Could not connect to LocalSearch: no session" >&2; exit 1\n')
    out = sd._run({"query": "lease"})
    assert "not available" in out and "not the same as finding nothing" in out


def test_baloo_with_no_index_is_not_nothing_found(bindir, monkeypatch):
    monkeypatch.setenv("XDG_CURRENT_DESKTOP", "KDE")
    _stub(bindir, "baloosearch6", "exit 0\n")
    _stub(bindir, "balooctl6", 'echo "Baloo Index could not be opened"; exit 1\n')
    assert "not available" in sd._run({"query": "lease"})
    _stub(bindir, "balooctl6", 'echo "Baloo File Indexer is running"; exit 0\n')
    assert "has nothing matching" in sd._run({"query": "lease"}), "a real, running index may find nothing"


def test_the_query_cannot_become_an_option(bindir, tmp_path):
    args = tmp_path / "args"
    _stub(bindir, "localsearch", f'printf "%s\\n" "$@" > {args}\n')
    assert "looks like an option" in sd._run({"query": "--help"})
    sd._run({"query": "lease agreement", "kind": "documents", "limit": 5})
    assert args.read_text().splitlines() == ["search", "-t", "-l", "5", "--", "lease agreement"]


# --- calendar (skill + trigger), with the calendar service faked at its Python seam ---------

from shani_chronoa import eds_calendar as cal_mod
from shani_chronoa import triggers as trig
from shani_chronoa.skills import calendar_events as ce


def _ev(start, summary="Standup", location="Room 4", all_day=False, uid="u1"):
    return cal_mod.Event(start=start, end=start + 1800, summary=summary, location=location,
                         calendar="Personal", uid=uid, all_day=all_day)


def test_calendar_lists_events_and_says_when_it_cannot_read(monkeypatch):
    monkeypatch.setattr(ce, "_consent", lambda c: (True, ""))
    now = 1_790_000_000.0
    monkeypatch.setattr(cal_mod, "events_between", lambda a, b, timeout=10: [_ev(now + 600)])
    out = ce._run({"range": "next_hours", "hours": 2}, now=now)
    assert out.startswith("1 event(s) in the next 2 hour(s)") and "Standup @ Room 4 (Personal)" in out

    def broken(a, b, timeout=10):
        raise cal_mod.CalendarUnavailable("the calendar registry did not answer")
    monkeypatch.setattr(cal_mod, "events_between", broken)
    assert "not the same as having nothing on" in ce._run({}, now=now)


def test_the_calendar_trigger_fires_once_per_event(monkeypatch):
    now = 1_790_000_000.0
    events = {"list": []}
    monkeypatch.setattr(cal_mod, "events_between", lambda a, b, timeout=10: events["list"])
    quiet = trig.read_calendar("starts-in:10", now=now)
    assert quiet.event is None
    events["list"] = [_ev(now + 300)]
    fire = trig.read_calendar("starts-in:10", now=now)
    assert fire.event is not None and "Standup starts in 5 min" in fire.event.summary
    assert trig.read_calendar("starts-in:10", now=now + 60).fingerprint == fire.fingerprint, "same event, no refire"
    events["list"] = [_ev(now + 300, all_day=True)]
    assert trig.read_calendar("starts-in:10", now=now).event is None, "an all-day event does not 'start in 5 min'"
    assert trig.read_calendar("in:10").status == trig.SIGNAL_UNAVAILABLE
