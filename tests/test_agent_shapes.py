"""The stand-in Agent must answer in the real engine's shapes.

One earlier stand-in answered `/status` with keys the real Inventory never
returned, and the terminal client crashed the first time it met a real engine.
Every read method is compared here with the real GTK-free function: same
top-level keys, same keys in the first row. Writes are compared on their
refusal shape (no confirmation -> {"error"}), which is the shape a client sees
before it asks the person to say yes.
"""

from __future__ import annotations

import json

import pytest

from shani_chronoa import engine_ops, inventory
from tests.tui_fake_agent import CAP_DATA, SECTIONS


@pytest.fixture(scope="module")
def bus():
    from tests.tui_bus import FakeBus

    instance = FakeBus()
    try:
        yield instance
    finally:
        instance.close()


def fake_payload(bus, method, *args):
    from shani_chronoa.agent_capabilities import METHODS

    spec = METHODS.get(method)
    signature = spec.signature_in if spec else "(" + "s" * len(args) + ")"
    kinds = spec.args if spec else tuple(("arg", "s") for _ in args)
    values = [
        bool(value) if kind == "b" else str(value)
        for value, (_, kind) in zip(args, kinds)
    ]
    raw = bus.call(method, signature, values, "(s)")[0]
    return json.loads(raw)


#: Real function -> the payload the Agent returns for it (some Agent methods
#: wrap a list in {"items"}, as agent_service.py does).
READS = {
    "SettingsList": lambda: {"items": engine_ops.settings.rows()},
    "SettingsGet": lambda: engine_ops.settings.get("privacy-mode"),
    "ModelsList": lambda: engine_ops.models.list_models(),
    "SetupState": lambda: engine_ops.setup.state(),
    "Conversations": lambda: {"items": engine_ops.conversations.list_sessions()},
    "MemoryList": lambda: engine_ops.memory.list_memory(),
    "Routines": lambda: {"items": engine_ops.automation.routines()},
    "Triggers": lambda: {"items": engine_ops.automation.triggers()},
    "Notices": lambda: {"items": engine_ops.automation.notices()},
    "VoiceEngines": lambda: engine_ops.voice.engines(),
    "Activity": lambda: engine_ops.health.activity(),
    "DiagnosticsProbe": lambda: engine_ops.health.probe(),
}

#: The argument list a client sends for each read (no writes here).
READ_ARGS = {name: () for name in READS}
READ_ARGS["SettingsGet"] = ("privacy-mode",)


@pytest.mark.parametrize("method", sorted(READS))
def test_read_methods_have_the_real_engine_shape(bus, method):
    real = READS[method]()
    stand_in = fake_payload(bus, method, *READ_ARGS[method])
    if set(real) == {"error"}:
        pytest.skip(f"{method} has no data on this machine: {real['error']}")
    assert set(real) == set(stand_in), (method, sorted(real), sorted(stand_in))
    for key in (
        "items",
        "facts",
        "proposals",
        "engines",
        "checks",
        "calls",
        "catalogue",
    ):
        rows = real.get(key) if isinstance(real, dict) else None
        if not isinstance(rows, list) or not rows or isinstance(rows[0], dict) is False:
            continue
        fake_rows = stand_in.get(key)
        assert isinstance(fake_rows, list) and fake_rows, (method, key)
        assert set(rows[0]) == set(fake_rows[0]), (
            method,
            key,
            sorted(rows[0]),
            sorted(fake_rows[0]),
        )


CONFIRM_WRITES = {
    "SettingsSet": ("privacy-mode", "true", False),
    "SettingsPreset": ("minimal", False),
    "SettingsEmail": ("{}", False),
    "ModelsUse": ("cloud", False),
    "SetupStep": ("brain", "", False),
    "ConversationsDelete": ("does-not-exist", False),
    "MemoryDecide": ("none:0", "yes", False),
}


@pytest.mark.parametrize("method", sorted(CONFIRM_WRITES))
def test_confirmed_writes_refuse_without_permission(bus, method):
    stand_in = fake_payload(bus, method, *CONFIRM_WRITES[method])
    assert set(stand_in) == {"error"}, (method, stand_in)
    assert isinstance(stand_in["error"], str) and stand_in["error"]


#: Writes with no consent key of their own: their success keys are the real
#: functions' return keys (`engine_ops.automation`), checked here as well.
PLAIN_WRITES = {
    "RoutinesRun": (("no-such-routine",), {"name", "request"}),
    "NoticesDismiss": (("no-such-notice",), {"dismissed"}),
}


@pytest.mark.parametrize("method", sorted(PLAIN_WRITES))
def test_plain_writes_have_the_real_engine_shape(bus, method):
    args, expected = PLAIN_WRITES[method]
    stand_in = fake_payload(bus, method, *args)
    assert set(stand_in) == expected, (method, stand_in)


@pytest.mark.parametrize("section", sorted(inventory.SECTIONS))
def test_inventory_sections_in_the_standin_match_the_real_reader(bus, section):
    """The `/status` crash this test guards against was a shape mismatch."""
    real = inventory.describe(section)
    stand_in = fake_payload(bus, "Inventory", section)
    assert set(real) == set(stand_in), (section, sorted(real), sorted(stand_in))
    real_items, fake_items = real.get("items"), stand_in.get("items")
    if isinstance(real_items, dict) and isinstance(fake_items, dict):
        assert set(real_items) == set(fake_items), (
            section,
            sorted(real_items),
            sorted(fake_items),
        )
    elif (
        isinstance(real_items, list)
        and real_items
        and isinstance(fake_items, list)
        and fake_items
    ):
        if isinstance(real_items[0], dict) and isinstance(fake_items[0], dict):
            assert set(real_items[0]) == set(fake_items[0]), (
                section,
                sorted(real_items[0]),
                sorted(fake_items[0]),
            )


def test_fake_agent_declares_every_contract_method():
    from shani_chronoa.agent_capabilities import METHODS

    from tests import tui_fake_agent

    for name in METHODS:
        assert hasattr(tui_fake_agent.FakeAgent, name), f"stand-in has no {name}"
