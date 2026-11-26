"""The terminal client's transport against the real stand-in Agent."""

import pytest

from shani_chronoa.tui.agent import AgentClient, EngineUnavailable
from shani_chronoa.tui.app import Session, extract_paths
from tests.tui_bus import FakeBus


@pytest.fixture(scope="module")
def bus():
    instance = FakeBus()
    try:
        yield instance
    finally:
        instance.close()


def _client(bus):
    client = AgentClient(connection=bus.connection)
    client.ensure_started()
    return client


def test_client_starts_no_engine_when_the_agent_is_already_there(bus, monkeypatch):
    client = _client(bus)
    monkeypatch.setattr(
        "shani_chronoa.tui.agent.subprocess.run",
        lambda *a, **k: pytest.fail("should not start an engine"),
    )
    client.ensure_started()
    assert client.available() is True
    client.close()


def test_client_calls_turn_receives_events_and_approves(bus):
    client = _client(bus)
    try:
        turn = client.turn("hello")
        assert turn.startswith("turn-")
        deadline_events = []
        while not any(e.get("event") == "approval" for e in deadline_events):
            client.tick()
            deadline_events = client.drain()
        approval = next(e for e in deadline_events if e.get("event") == "approval")
        assert approval["options"] == ["Allow this once", "Deny"]
        assert client.approve(approval["id"], "Allow this once") is True
        while not any(e.get("event") == "done" for e in deadline_events):
            client.tick()
            deadline_events.extend(client.drain())
        assert "Approved with Allow this once." in "".join(
            e.get("text", "") for e in deadline_events if e.get("event") == "reply"
        )
    finally:
        client.close()


def test_client_turnwith_inventory_and_cancel(bus):
    client = _client(bus)
    try:
        turn = client.turn_with("hi", {"conversation": "new", "paths": ["/tmp/a"]})
        assert turn.startswith("turn-")
        models = client.inventory("models")
        assert models["items"][0]["task"] == "conversion"
        assert client.cancel(turn) is True
        assert client.cancel(turn) is False
    finally:
        client.close()


def test_session_ignores_another_turns_events(bus):
    client = _client(bus)
    seen = []
    session = Session(client, seen.append)
    # An event for some other turn is queued before the session starts.
    client._events.append({"turn": "turn-other", "event": "reply", "text": "NOT MINE"})
    result = session.run_turn("no-approval")
    assert "NOT MINE" not in result["reply"]
    assert all(event.get("turn") != "turn-other" for event in seen)
    client.close()


def test_extract_paths_only_attaches_real_files(tmp_path):
    note = tmp_path / "note.txt"
    note.write_text("hi", encoding="utf-8")
    text, paths = extract_paths(f"read @{note} and @missing.txt please")
    assert "and @missing.txt" in text
    assert paths == [str(note)]


def test_engine_unavailable_explains_how_to_start(monkeypatch):
    from gi.repository import GLib

    class DeadConnection:
        def signal_subscribe(self, *args, **kwargs):
            return object()

        def call_sync(self, *args, **kwargs):
            raise GLib.Error("no activation service")

        def signal_unsubscribe(self, *args):
            pass

    monkeypatch.setattr("shani_chronoa.tui.agent._has_owner", lambda c: False)
    monkeypatch.setattr(
        "shani_chronoa.tui.agent.subprocess.run",
        lambda *a, **k: (_ for _ in ()).throw(OSError("no systemctl")),
    )
    client = AgentClient(connection=DeadConnection())
    client.tick = lambda: None
    with pytest.raises(EngineUnavailable) as raised:
        client.ensure_started(timeout=0.01)
    assert "systemctl --user start shani-chronoa-engine.service" in str(raised.value)
