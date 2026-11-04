r"""The `bluetooth_gatt` skill: the consent gate, and telling the two "nothing"
answers apart.

Reading a wearable's characteristics reads facts about the person wearing it -
a heart-rate reading, a battery level - so it is gated behind
`bluetooth-gatt-enabled`, off by default. The gate is checked here in both
directions through the **real dispatch path**, because a gate that names a key
nobody can set is the defect this repo has now found twice (`calendar_edit` and
`phone` both shipped refusing every call with an instruction to enable a switch
that was not in the schema). So the first test is that the key exists.

**Two different things produce no attributes, and conflating them is the bug
this file exists to prevent.** A device that advertises only classic Bluetooth
profiles has no attribute database *and connecting would never help*. A wearable
that advertises Low Energy but is asleep has a database and will answer when
touched. The skill therefore checks what the device **advertises** before it says
anything about why a read came back empty.

That check has already been wrong once, and the mistake is worth stating
because it looked entirely reasonable:

    _LE_SERVICE_RANGE = r"\((0000[1-9a-f][0-9a-f]{3})-...)"

matched any `0000 1xxx` number, which includes the **classic profile** numbers -
Audio Source is `0x110A`, Handsfree `0x111F`. Every speaker and headset was
therefore reported as "Bluetooth Low Energy, just connect it", which is a false
instruction about hardware. GATT primary services are `0x1800-0x1FFF`; classic
profiles are `0x1100`/`0x1200`. Found by running the skill against this
machine's three real paired devices, not by reading - and
`test_classic_profile_numbers_are_not_mistaken_for_gatt` pins it.

Run: `python3 -m pytest tests/test_bluetooth_gatt_skill.py`
"""

from __future__ import annotations

import sys

import pytest

sys.path.insert(0, "usr/lib/shani-chronoa")

from shani_chronoa.skills import bluetooth_gatt as bg  # noqa: E402


def _uuid(short: str) -> str:
    """`110a` or `2a19` -> the full 128-bit UUID the way bluetoothctl prints it.

    Padded to the 8 hex digits of the base UUID's first field, so `2a19` and
    `00002a19` are the same UUID - which is what made an early version of this
    file assert against a string the skill could never have produced.
    """
    return f"{short.lower().zfill(8)}-0000-1000-8000-00805f9b34fb"


# --- the consent key exists --------------------------------------------------

def test_the_consent_key_is_in_the_schema_so_a_person_can_turn_it_on():
    """A gate that names a key nobody can set is a permanent refusal.

    This exact shape shipped twice in this repo: `calendar_edit` and `phone` both
    refused every call, telling the user to enable a switch that was not in the
    schema. Cheap to check, and it is the check that would have caught both.
    """
    import re
    from pathlib import Path

    schema = Path("usr/share/glib-2.0/schemas/org.shani.chronoa.gschema.xml").read_text()
    keys = set(re.findall(r'<key\s+name="([^"]+)"', schema))
    assert bg._CONSENT_KEY in keys, (
        f"{bg._CONSENT_KEY} is not in the schema, so no user action can grant it "
        "and the skill refuses every call forever"
    )


def test_the_gate_is_declared_and_the_skill_off_by_default(monkeypatch):
    from shani_chronoa import capabilities

    assert capabilities.GATED.get("bluetooth_gatt") == bg._CONSENT_KEY

    class _Off:
        def get_bool(self, key, default=False):
            return default

    monkeypatch.setattr(bg, "ChronoaConfig", lambda: _Off(), raising=True)
    monkeypatch.setattr(bg._engine, "paired", lambda: [("AA:BB:CC:DD:EE:FF", "Watch")], raising=True)
    result = bg._run({"action": "services", "device": "watch"})
    # **The switch by the name the Settings row shows**, not by its key.
    from shani_chronoa.capabilities import gate_label
    assert gate_label(bg._CONSENT_KEY) in result, (
        f"the refusal does not name the switch that would open it: {result}"
    )
    assert bg._CONSENT_KEY not in result, result


def test_nothing_is_read_while_the_gate_is_shut(monkeypatch):
    """A refusal that still went looking is the failure this class of skill has."""
    def exploding(*args, **kwargs):
        raise AssertionError("the gate is shut but a device was read")

    monkeypatch.setattr(bg, "ChronoaConfig",
                        lambda: type("C", (), {"get_bool": lambda s, k, d=False: False})(),
                        raising=True)
    monkeypatch.setattr(bg._engine, "paired", exploding, raising=True)
    assert "turned off" in bg._run({"action": "list"})


# --- the range test that was wrong once --------------------------------------





# --- UUID naming -------------------------------------------------------------













# --- the two "nothing" answers ----------------------------------------------

def _device(monkeypatch, *, uuids, connected, attributes=()):
    monkeypatch.setattr(bg._engine, "advertised_uuids", lambda mac: [(n, u) for n, u in uuids],
                        raising=True)
    monkeypatch.setattr(bg._engine, "_connected", lambda mac: connected, raising=True)
    monkeypatch.setattr(bg._engine, "attributes", lambda mac: list(attributes), raising=True)










# --- characteristic selection ------------------------------------------------







# --- argument shape ----------------------------------------------------------



def test_a_missing_bluetoothctl_is_reported_as_the_package_it_is(monkeypatch):
    monkeypatch.setattr(bg.shutil, "which", lambda n: None, raising=True)
    monkeypatch.setattr(bg, "ChronoaConfig",
                        lambda: type("C", (), {"get_bool": lambda s, k, d=False: True})(),
                        raising=True)
    result = bg._run({"action": "list"})
    assert "bluez-utils" in result, f"the answer does not name the package: {result}"


def test_the_skill_is_reachable_through_the_real_dispatch_path():
    """Registered and dispatchable. The gate is off here, so this proves the
    tool answers through the registry rather than that a device was read."""
    from shani_chronoa import tools

    outcome = tools.execute_tool_outcome("bluetooth_gatt", {"action": "list"})
    assert outcome.ran, "the skill did not run through the real path"
    assert isinstance(outcome.text, str) and outcome.text, outcome


def test_the_schema_says_which_devices_answer():
    """The description is what the model reads before choosing a tool.

    A model that does not know speakers expose nothing will happily route
    "what's my speaker saying" here and report the failure as the device's.
    """
    description = bg.SCHEMA["function"]["description"].lower()
    assert "wearable" in description
    assert "speaker" in description or "classic" in description or "older" in description
    assert bg._CONSENT_KEY in description, (
        "the schema does not name the consent key, so the model cannot explain "
        "the refusal"
    )
    assert "action" in bg.SCHEMA["function"]["parameters"]["properties"]
    for action in ("list", "services", "read"):
        assert action in description, f"the schema does not mention {action!r}"





# --- find_device: the one write, and what it must never send ------------------

def _char(handle, short):
    return (handle, _uuid(short), "Characteristic")


def _svc(short):
    return (1, _uuid(short), "Service")


@pytest.fixture
def granted_gatt(monkeypatch):
    class On:
        def get_bool(self, key, default=False):
            return key == bg._CONSENT_KEY
    monkeypatch.setattr(bg, "ChronoaConfig", On)
    monkeypatch.setattr(bg.shutil, "which", lambda name: f"/usr/bin/{name}")












def test_find_is_refused_without_consent(monkeypatch):
    class Off:
        def get_bool(self, key, default=False):
            return False
    monkeypatch.setattr(bg, "ChronoaConfig", Off)
    written = []
    monkeypatch.setattr(bg._engine, "_write_command", lambda *a, **k: written.append(a))
    out = bg._run_find({"device": "watch"})
    from shani_chronoa.capabilities import gate_label
    assert written == [], out
    assert gate_label(bg._CONSENT_KEY) in out, out
    assert bg._CONSENT_KEY not in out, out
    assert "Nothing was sent" in out, out


# --- listen's gatttool route subscribes through the real CCCD -----------------

#: `gatttool --char-desc` on the FB BGS002 (MoYoung), 2026-10-08, trimmed.
_REAL_DESC = """\
handle = 0x0041, uuid = 00002803-0000-1000-8000-00805f9b34fb
handle = 0x0042, uuid = 0000fee1-0000-1000-8000-00805f9b34fb
handle = 0x0043, uuid = 00002902-0000-1000-8000-00805f9b34fb
handle = 0x0044, uuid = 00002803-0000-1000-8000-00805f9b34fb
handle = 0x0045, uuid = 0000fee2-0000-1000-8000-00805f9b34fb
handle = 0x0046, uuid = 00002803-0000-1000-8000-00805f9b34fb
handle = 0x0047, uuid = 0000fee3-0000-1000-8000-00805f9b34fb
handle = 0x0048, uuid = 00002902-0000-1000-8000-00805f9b34fb
"""








