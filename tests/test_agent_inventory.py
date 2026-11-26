"""The shell's read-only Inventory op on the Agent interface.

A shell that draws Chronoa's panels needs the facts the GTK panels used to
read by importing the stores. `Inventory(section) -> json` is the engine side
of that: one bounded section per call, no writes and no new authority.
"""

import json
from pathlib import Path
import sys

_REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_REPO / "usr" / "lib" / "shani-chronoa"))

from gi.repository import GLib  # noqa: E402

from shani_chronoa import agent_contract, inventory  # noqa: E402
from shani_chronoa.agent_service import AgentService  # noqa: E402


class _Invocation:
    def __init__(self):
        self.value = None
        self.error = None

    def return_value(self, value):
        self.value = value

    def return_dbus_error(self, name, message):
        self.error = (name, message)


def test_contract_advertises_inventory():
    assert '<method name="Inventory">' in agent_contract.XML
    assert '<arg name="section" type="s" direction="in"/>' in agent_contract.XML


def test_every_documented_section_returns_bounded_json():
    wanted = {
        "conversations",
        "models",
        "skills",
        "routines",
        "triggers",
        "privacy",
        "memory",
        "senses",
        "capabilities",
        "status",
    }
    assert wanted <= set(inventory.SECTIONS)
    for section in inventory.SECTIONS:
        payload = json.loads(inventory.to_json(section, limit=3))
        assert payload["section"] == section
        assert "error" not in payload, payload
        assert "items" in payload
        items = payload["items"]
        if isinstance(items, list):
            assert len(items) <= 3


def test_unknown_section_is_an_error_not_an_empty_panel():
    payload = json.loads(inventory.to_json("not-a-section"))
    assert payload["error"] == "unknown section"
    assert "conversations" in payload["allowed"]


def test_a_broken_store_fails_only_its_own_section(monkeypatch):
    def broken(_limit):
        raise RuntimeError("store unreadable")

    monkeypatch.setitem(inventory._SECTIONS, "routines", broken)
    payload = inventory.describe("routines")
    assert "store unreadable" in payload["error"]
    assert json.loads(inventory.to_json("models"))["section"] == "models"


def test_agent_inventory_returns_a_json_variant():
    service = AgentService.__new__(AgentService)
    invocation = _Invocation()
    service.Inventory(
        None,
        None,
        None,
        None,
        "Inventory",
        GLib.Variant("(s)", ("models",)),
        invocation,
    )

    assert invocation.error is None
    payload = json.loads(invocation.value.unpack()[0])
    assert payload["section"] == "models"
    assert payload["items"]
