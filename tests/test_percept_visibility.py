"""A skill must be able to answer "what is being perceived right now?" truthfully.

`list_percepts` shipped claiming to show every fact held right now, and it could
not. A skill runs in a subprocess, so it built its own `PerceptStore` and read
the durable file - which only ever holds the memory sense's facts. Every
transient percept, which is what a running app is mostly holding, was invisible
to it. It would have answered "3 percepts" while the app actually had dozens in
context, and said so confidently.

The fix is a live view the store publishes. The rule pinned here is narrower
than "it works": a stale, absent, or corrupt view must be *named*, never passed
off as current. A smaller honest answer is the whole point.
"""

from __future__ import annotations

import json
import sys
import time
from pathlib import Path

import pytest

_REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_REPO / "usr" / "lib" / "shani-chronoa"))

from shani_chronoa.senses import store as store_mod  # noqa: E402
from shani_chronoa.senses.context import Percept  # noqa: E402
from shani_chronoa.senses.store import PerceptStore  # noqa: E402


@pytest.fixture
def live_path(tmp_path, monkeypatch):
    """A temp live view, also patched in as the module default.

    The store falls back to the module global and the skill reads it, so both
    have to be redirected. Patching is not optional: without it this file writes
    into the real ~/.local/share/shani-chronoa/percepts, which happened once and
    left a fabricated snapshot in a real user's data directory.
    """
    path = tmp_path / "live.json"
    monkeypatch.setattr(store_mod, "LIVE_FILE", path)
    return path


def _store(tmp_path):
    """A store with both files pinned, so nothing can fall back to $HOME."""
    return PerceptStore(durable_path=tmp_path / "memory.jsonl",
                        live_path=tmp_path / "live.json")


def _with_transient(tmp_path):
    store = _store(tmp_path)
    store.add(Percept(sense="power", kind="state", created_at=time.time(),
                      content="battery at 100", sensitivity="public",
                      ttl_seconds=600.0))
    return store


def _answer():
    from shani_chronoa.skills.list_percepts import _run
    return _run({})


class TestTheLiveViewIsPublished:
    def test_adding_a_transient_percept_writes_it(self, tmp_path, live_path):
        _with_transient(tmp_path)
        assert live_path.is_file(), "a transient percept was not published"
        payload = json.loads(live_path.read_text())
        assert any("battery" in t["content"] for t in payload["transient"])

    def test_the_write_leaves_no_partial_file(self, tmp_path, live_path):
        """A reader running concurrently with a poll must not see half a file."""
        _with_transient(tmp_path)
        leftovers = [p.name for p in live_path.parent.iterdir() if p.name.endswith(".tmp")]
        assert not leftovers, f"a partial snapshot was left behind: {leftovers}"

    def test_durable_facts_are_not_duplicated_into_the_live_view(self, tmp_path, live_path):
        store = _store(tmp_path)
        store.add(Percept(sense="memory", kind="fact", created_at=time.time(),
                          content="a remembered fact", sensitivity="private"))
        payload = json.loads(live_path.read_text())
        assert not any("remembered" in t["content"] for t in payload["transient"]), (
            "a durable fact appeared in the live view, so it would be listed twice")

    def test_clearing_transients_does_not_republish_them(self, tmp_path, live_path):
        """Publish-after-clear. Publishing before it would restate the facts
        the caller just asked to have discarded."""
        store = _with_transient(tmp_path)
        store.clear_transient()
        payload = json.loads(live_path.read_text())
        assert not any("battery" in t["content"] for t in payload["transient"]), (
            "cleared percepts are still advertised as being held")


class TestTheAnswerNamesItsSource:
    def test_a_live_view_is_reported_as_live(self, tmp_path, live_path):
        _with_transient(tmp_path)
        out = _answer()
        assert "battery at 100" in out, "the transient percept is invisible again"
        assert "live" in out.splitlines()[1].lower()

    def test_a_stale_view_is_called_a_leftover(self, tmp_path, live_path):
        live_path.parent.mkdir(parents=True, exist_ok=True)
        live_path.write_text(json.dumps({
            "written_at": time.time() - 7200, "pid": 424242,
            "transient": [{"sense": "power", "kind": "state",
                           "content": "an old reading", "created_at": time.time() - 7200,
                           "ttl_seconds": 99999, "source": "",
                           "sensitivity": "public", "metadata": {}}],
            "durable_count": 0}))
        out = _answer()
        assert "leftover" in out, (
            "a snapshot from a process that is gone is being described as what "
            "is being perceived right now")
        assert "no longer running" in out

    def test_no_view_at_all_says_so(self, tmp_path, live_path):
        out = _answer()
        assert "no running app" in out or "Nothing is being perceived" in out

    def test_a_corrupt_view_does_not_crash_or_lie(self, tmp_path, live_path):
        live_path.parent.mkdir(parents=True, exist_ok=True)
        live_path.write_text("{not json at all")
        out = _answer()
        assert out, "a corrupt snapshot produced no answer at all"
        assert "no running app" in out or "Nothing is being perceived" in out
