"""Automation engine rows: routines expand, triggers list, notices dismiss."""

import json
from types import SimpleNamespace

from shani_chronoa import notices as notices_mod
from shani_chronoa import routines as routines_mod
from shani_chronoa.engine_ops import automation
from shani_chronoa.triggers import rules as rules_mod


def test_routines_rows_from_the_store(tmp_path, monkeypatch):
    path = tmp_path / "routines.json"
    path.write_text(
        json.dumps(
            {
                "good morning": {
                    "request": "weather and calendar",
                    "phrases": ["morning"],
                },
                "wind down": {"request": "lights off"},
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(routines_mod, "_path", lambda: path)

    rows = automation.routines()

    assert rows == [
        {
            "name": "good morning",
            "request": "weather and calendar",
            "phrases": ["good morning", "morning"],
        },
        {"name": "wind down", "request": "lights off", "phrases": ["wind down"]},
    ]


def test_routines_store_that_raises_is_one_error_row(monkeypatch):
    def broken():
        raise OSError("the store is gone")

    monkeypatch.setattr(routines_mod, "load", broken)
    rows = automation.routines()

    assert len(rows) == 1
    assert rows[0]["error"].startswith("the routines could not be read")


def test_run_routine_returns_the_expanded_request_without_running_it(monkeypatch):
    calls = []

    def fake_expand(utterance):
        calls.append(utterance)
        return "good morning", "weather and calendar"

    monkeypatch.setattr(routines_mod, "expand", fake_expand)
    assert automation.run_routine("good morning") == {
        "name": "good morning",
        "request": "weather and calendar",
    }
    assert calls == ["good morning"]


def test_run_routine_unknown_or_broken_is_an_error(monkeypatch):
    monkeypatch.setattr(routines_mod, "expand", lambda _s: None)
    assert "error" in automation.run_routine("nothing saved")

    def broken(_s):
        raise OSError("bad store")

    monkeypatch.setattr(routines_mod, "expand", broken)
    assert "error" in automation.run_routine("good morning")


def test_triggers_rows_come_from_rulestore_all(monkeypatch):
    class FakeStore:
        def all(self):
            return [
                SimpleNamespace(
                    name="battery",
                    sense="battery",
                    match_mode="any",
                    substring="",
                    keywords=["low"],
                    actuator="notify",
                    arguments={"level": 10},
                    cooldown_seconds=60,
                    enabled=True,
                    allow_destructive=False,
                    created_at=1.0,
                    last_fired_at=None,
                )
            ]

    monkeypatch.setattr(rules_mod, "RuleStore", FakeStore)
    rows = automation.triggers()

    assert len(rows) == 1
    assert rows[0]["name"] == "battery"
    assert rows[0]["actuator"] == "notify"
    assert rows[0]["enabled"] is True
    assert rows[0]["keywords"] == ["low"]


def test_triggers_corrupt_store_is_an_error_row(monkeypatch):
    class FakeStore:
        def __init__(self):
            raise ValueError("rules.json is corrupt")

    monkeypatch.setattr(rules_mod, "RuleStore", FakeStore)
    rows = automation.triggers()

    assert len(rows) == 1
    assert "trigger rules could not be read" in rows[0]["error"]


def test_notices_copies_every_record_and_dismisses_one(monkeypatch):
    entries = [
        {
            "id": "a",
            "kind": "reflex",
            "title": "Battery low",
            "body": "plug in",
            "why": "below 10%",
            "at": 2,
            "dismissed": False,
        },
        {
            "id": "b",
            "kind": "rule",
            "title": "Meeting",
            "body": "soon",
            "why": "calendar",
            "at": 1,
            "dismissed": True,
        },
    ]
    monkeypatch.setattr(notices_mod, "list_all", lambda: entries)
    monkeypatch.setattr(notices_mod, "dismiss", lambda nid: nid == "a")

    rows = automation.notices()

    assert [row["id"] for row in rows] == ["a", "b"]
    assert rows[0] is not entries[0]  # a caller cannot mutate the stored record
    assert automation.dismiss_notice("a") == {"dismissed": "a"}
    assert "error" in automation.dismiss_notice("b")
    assert "error" in automation.dismiss_notice("")
