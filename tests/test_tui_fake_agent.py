"""The stand-in serves the real contract and real event shapes."""

import json

import pytest

from tests.tui_bus import FakeBus


@pytest.fixture(scope="module")
def bus():
    instance = FakeBus()
    try:
        yield instance
    finally:
        instance.close()


def test_turn_streams_events_waits_for_the_label_then_finishes(bus):
    turn = bus.call("Turn", "(s)", ("hello",), "(s)")[0]
    approvals = bus.wait_for(
        lambda e: e.get("event") == "approval" and e.get("turn") == turn
    )
    approval = approvals[0]
    assert approval["text"] == "Run the command?"
    assert approval["options"] == ["Allow this once", "Deny"]
    assert approval["detail"] == "line one\nline two"
    kinds = [e["event"] for e in bus.events if e.get("turn") == turn]
    assert kinds == [
        "state",
        "tool",
        "tool_done",
        "reply",
        "reply",
        "reply",
        "approval",
    ]

    ok = bus.call("Approve", "(ss)", (turn, "Allow this once"), "(b)")[0]
    assert ok is True
    bus.wait_for(lambda e: e.get("event") == "done" and e.get("turn") == turn)
    replies = "".join(
        e.get("text", "")
        for e in bus.events
        if e.get("event") == "reply" and e.get("turn") == turn
    )
    assert "from the stand-in agent" in replies
    assert "Approved with Allow this once." in replies, (
        "the label must travel unchanged"
    )
    assert bus.call("Approve", "(ss)", (turn, "Deny"), "(b)")[0] is False


def test_turnwith_reports_its_options_and_inventory_is_bounded(bus):
    turn = bus.call(
        "TurnWith",
        "(ss)",
        ("hi", json.dumps({"conversation": "new", "paths": ["/tmp/a.txt"]})),
        "(s)",
    )[0]
    bus.wait_for(lambda e: e.get("event") == "approval" and e.get("turn") == turn)
    assert any(
        "options:" in e.get("text", "")
        for e in bus.events
        if e.get("event") == "reply" and e.get("turn") == turn
    )
    bus.call("Approve", "(ss)", (turn, "Deny"), "(b)")

    models = json.loads(bus.call("Inventory", "(s)", ("models",), "(s)")[0])
    assert models["section"] == "models"
    assert models["items"][0]["model"] == "qwen3:4b"
    unknown = json.loads(bus.call("Inventory", "(s)", ("nonsense",), "(s)")[0])
    assert unknown["error"] == "unknown section"
    assert "models" in unknown["allowed"]


def test_cancel_stops_a_waiting_turn(bus):
    turn = bus.call("Turn", "(s)", ("please wait",), "(s)")[0]
    bus.wait_for(lambda e: e.get("event") == "approval" and e.get("turn") == turn)
    assert bus.call("Cancel", "(s)", (turn,), "(b)")[0] is True
    bus.wait_for(lambda e: e.get("event") == "done" and e.get("turn") == turn)
    assert bus.call("Cancel", "(s)", (turn,), "(b)")[0] is False
