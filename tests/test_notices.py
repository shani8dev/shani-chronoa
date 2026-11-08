"""One queue for everything Chronoa notices, instead of one popup each.

Reflexes notify, an armed rule asks, a meeting app offers. Each was its own
`notify-send`, so the person could not see what Chronoa had been saying,
could not dismiss a subject once for all, and the assistant could not say why
it had spoken. These tests pin the store's three properties: it dedupes (a
condition that stays true is one notice, not one per tick), it expires (stale
urgency is not news), and it is bounded (a queue can nag too).
"""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "usr/lib/shani-chronoa"))

from shani_chronoa import notices  # noqa: E402


@pytest.fixture(autouse=True)
def state(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state"))


def test_a_notice_is_queued_with_its_reason():
    notice_id = notices.record("reflex", "Battery", "8% left", "reflex: battery")
    assert notice_id
    [notice] = notices.list_open()
    assert notice["title"] == "Battery"
    assert notice["why"] == "reflex: battery"
    assert notice["kind"] == "reflex"


def test_the_same_notice_is_not_queued_twice():
    first = notices.record("reflex", "Battery", "8% left", "reflex: battery")
    second = notices.record("reflex", "Battery", "8% left", "reflex: battery")
    assert first and second is None
    assert len(notices.list_open()) == 1


def test_a_dismissed_notice_can_be_queued_again():
    first = notices.record("reflex", "Battery", "8% left", "reflex: battery")
    assert notices.dismiss(first) is True
    assert notices.list_open() == []
    assert notices.record("reflex", "Battery", "8% left", "reflex: battery")


def test_dismissing_twice_reports_the_second():
    notice_id = notices.record("reflex", "Battery", "8% left", "")
    assert notices.dismiss(notice_id) is True
    assert notices.dismiss(notice_id) is False
    assert notices.dismiss("no-such-notice") is False


def test_a_different_kind_is_a_different_notice():
    notices.record("reflex", "Battery", "8% left", "")
    notices.record("approval", "Battery", "8% left", "")
    assert len(notices.list_open()) == 2


def test_stale_notices_stop_being_listed(monkeypatch):
    notices.record("reflex", "Battery", "8% left", "")
    entry = notices.list_all()[0]
    entry["at"] -= notices.MAX_AGE_SECONDS + 1
    notices._save([entry])
    assert notices.list_open() == []
    assert len(notices.list_all()) == 1          # kept, just not shown as news


def test_the_store_is_bounded():
    for i in range(notices.MAX_NOTICES + 10):
        notices.record("reflex", f"Notice {i}", "body", "")
    assert len(notices.list_all()) == notices.MAX_NOTICES


def test_an_approval_records_its_answer():
    notice_id = notices.record("approval", "Delete?", "body", "an armed rule asked")
    assert notices.set_outcome(notice_id, "allow") is True
    [notice] = notices.list_open()
    assert notice["outcome"] == "allow"


def test_set_outcome_on_a_missing_notice_is_false():
    assert notices.set_outcome(None, "allow") is False
    assert notices.set_outcome("nope", "allow") is False


def test_the_store_file_is_private():
    notices.record("reflex", "Battery", "8% left", "")
    path = notices._path()
    assert oct(path.stat().st_mode & 0o777) == "0o600"
    assert oct(path.parent.stat().st_mode & 0o777) == "0o700"


def test_a_corrupt_store_is_empty_not_a_crash():
    path = notices._path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("not json", encoding="utf-8")
    assert notices.list_open() == []
    assert notices.list_all() == []


def test_a_reflex_notice_reaches_the_queue(monkeypatch):
    """`reflex.notify` is the producer; the queue is the record of it."""
    from gi.repository import GLib  # noqa: F401  (import guard parity with the suite)
    from shani_chronoa import reflex

    monkeypatch.setattr(reflex.shutil, "which", lambda name: "/usr/bin/notify-send")
    monkeypatch.setattr(reflex.subprocess, "run", lambda *a, **k: None)
    urge = reflex.Urgence(subject="Battery", detail="8% left", urgency="critical",
                          speak=True)
    assert reflex.notify([urge])
    assert [n["title"] for n in notices.list_open()] == ["Battery"]
