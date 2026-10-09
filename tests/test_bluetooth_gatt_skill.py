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
    monkeypatch.setattr(bg, "paired", lambda: [("AA:BB:CC:DD:EE:FF", "Watch")], raising=True)
    result = bg._run({"action": "services", "device": "watch"})
    assert bg._CONSENT_KEY in result, (
        f"the refusal does not name the switch that would open it: {result}"
    )


def test_nothing_is_read_while_the_gate_is_shut(monkeypatch):
    """A refusal that still went looking is the failure this class of skill has."""
    def exploding(*args, **kwargs):
        raise AssertionError("the gate is shut but a device was read")

    monkeypatch.setattr(bg, "ChronoaConfig",
                        lambda: type("C", (), {"get_bool": lambda s, k, d=False: False})(),
                        raising=True)
    monkeypatch.setattr(bg, "paired", exploding, raising=True)
    assert "turned off" in bg._run({"action": "list"})


# --- the range test that was wrong once --------------------------------------

@pytest.mark.parametrize("short", [
    "00001105",  # OBEX Object Push
    "0000110a",  # Audio Source
    "0000110d",  # Advanced Audio Distribution
    "0000110e",  # A/V Remote Control
    "00001112",  # Headset AG
    "0000111f",  # Handsfree Audio Gateway
    "0000112d",  # SIM Access
    "00000000",  # vendor specific / none
])
def test_classic_profile_numbers_are_not_mistaken_for_gatt(short):
    """The bug, pinned at the level it was found.

    A regex matching `0000[1-9a-f]xxxx` treats `0x110A` as Low Energy, so every
    speaker on this machine would have been told to connect. GATT primary
    services start at `0x1800`.
    """
    assert not bg._GATT_SERVICE_RANGE.search(_uuid(short)), (
        f"{short} is a classic Bluetooth profile number and must not read as a "
        "GATT service"
    )


@pytest.mark.parametrize("short", ["0000180d", "0000180f", "00001fff", "0000181a"])
def test_real_gatt_service_numbers_are_recognised(short):
    assert bg._GATT_SERVICE_RANGE.search(_uuid(short)), (
        f"{short} is in the GATT service range and should be recognised"
    )


# --- UUID naming -------------------------------------------------------------

def test_a_known_uuid_gets_a_name_and_an_unknown_one_keeps_its_number():
    assert bg.uuid_name("0x180d") == "Heart Rate"
    assert bg.uuid_name("0x180f") == "Battery"
    # 0x2A19 is Battery Level and 0x2A37 is Heart Rate Measurement. The first
    # version of this table had them the other way round, which would have
    # reported somebody's watch battery as their pulse - see the note in the
    # module and test_the_uuid_table_puts_battery_and_heart_rate_the_right_way_round.
    assert bg.uuid_name("0x2a19") == "Battery Level"
    assert bg.uuid_name("0x2a37") == "Heart Rate Measurement"
    unknown = bg.uuid_name("0x1234")
    assert "0x1234" in unknown, f"an unknown UUID lost its number: {unknown}"
    # And it must not acquire a name it was never given.
    assert "UUID" in unknown or unknown == "0x1234"


def test_the_uuid_table_puts_battery_and_heart_rate_the_right_way_round():
    """The mistake, pinned where it was found: by reading a real device.

    Its Battery *service* (0x180F) contains characteristic 0x2A19, and its
    Heart Rate service (0x180D) contains 0x2A37. Having those the other way
    round would report a battery percentage as a pulse - and the value would
    have been entirely plausible, which is the problem.
    """
    assert bg.uuid_name("0x2a19") != bg.uuid_name("0x2a37")
    assert "battery" in bg.uuid_name("0x2a19").lower()
    assert "heart" in bg.uuid_name("0x2a37").lower()


def test_the_full_128_bit_form_resolves_which_the_short_form_did_not_reach():
    """A bug only real hardware could find, because the short form worked.

    `uuid_name` compared the wrong slices when checking the Bluetooth base UUID,
    so it failed the test for *every* real 128-bit UUID and handed back raw hex.
    Against an actual wearable that meant `read characteristic='Battery Level'`
    reported the device had no such thing - while unit tests on the short
    `0x2a19` form passed throughout, because that path had never been broken.
    """
    assert bg.uuid_name(_uuid("2a19")) == "Battery Level"
    assert bg.uuid_name(_uuid("2a37")) == "Heart Rate Measurement"
    assert bg.uuid_name(_uuid("180d")) == "Heart Rate"
    assert bg.uuid_name(_uuid("180f")) == "Battery"


def test_text_characteristics_are_not_reported_as_numbers():
    """Manufacturer Name is a company *name*, not a byte.

    A first version returned `51` for the byte 0x33 and `74` for 0x4a. The hex
    was right and the reading of it was wrong, which is the worst combination -
    it looks like data.
    """
    readable, note = bg.describe_value("Manufacturer Name", "33")
    assert readable == "3", f"a text characteristic was read as a number: {readable!r}"
    assert note == ""
    readable, note = bg.describe_value("Battery Level", "12")
    assert readable == "18%", readable
    # And a single byte this module does know the meaning of is a number.
    assert bg.describe_value("Body Sensor Location", "01")[0] == "chest"


def test_a_vendor_uuid_is_not_squeezed_into_the_base_range():
    """A vendor 128-bit UUID has no short form; reporting one would be a guess."""
    vendor = "6e400001-b5a3-f393-e0a9-e50e24dcca9e"
    assert bg.uuid_name(vendor) == vendor, (
        "a vendor UUID was rewritten into an assigned number it does not hold"
    )


def test_naming_never_raises_on_junk():
    """A malformed UUID from a device must not become a traceback for the model."""
    for junk in ("", "not-a-uuid", "0xZZZZ", "zzzz-zzzz"):
        assert isinstance(bg.uuid_name(junk), str)


# --- the two "nothing" answers ----------------------------------------------

def _device(monkeypatch, *, uuids, connected, attributes=()):
    monkeypatch.setattr(bg, "advertised_uuids", lambda mac: [(n, u) for n, u in uuids],
                        raising=True)
    monkeypatch.setattr(bg, "_connected", lambda mac: connected, raising=True)
    monkeypatch.setattr(bg, "attributes", lambda mac: list(attributes), raising=True)


def test_a_classic_device_is_told_connecting_would_not_help(monkeypatch):
    """Connecting a speaker is useless advice, and this is what it used to give."""
    _device(monkeypatch,
            uuids=[("Audio Source", _uuid("0000110a")),
                   ("Advanced Audio Distribution", _uuid("0000110d"))],
            connected=False)
    answer = bg._no_attributes_answer("AA:BB:CC:DD:EE:FF", "JBL GO Essential")
    assert "older Bluetooth" in answer or "older Bluetooth profiles" in answer, answer
    assert "not connected" not in answer.lower(), (
        f"a classic device was told to connect, which would never help: {answer}"
    )


def test_a_sleeping_wearable_is_told_to_wake_it(monkeypatch):
    """The opposite case: it *does* have a database, so connecting is the advice."""
    _device(monkeypatch, uuids=[("Battery", _uuid("0000180f"))], connected=False)
    answer = bg._no_attributes_answer("AA:BB:CC:DD:EE:FF", "Watch")
    assert "connect" in answer.lower(), (
        f"a Low Energy device was not told to connect: {answer}"
    )
    assert "older Bluetooth" not in answer, answer


def test_a_connected_wearable_with_nothing_is_told_it_is_asleep(monkeypatch):
    _device(monkeypatch, uuids=[("Battery", _uuid("0000180f"))], connected=True)
    answer = bg._no_attributes_answer("AA:BB:CC:DD:EE:FF", "Watch")
    assert "asleep" in answer.lower() or "out of range" in answer.lower(), answer


def test_every_branch_ends_by_saying_nothing_was_read(monkeypatch):
    """A refusal that does not say the state is unchanged reads as a maybe."""
    _device(monkeypatch, uuids=[("Audio", _uuid("0000110a"))], connected=False)
    assert "read" in bg._no_attributes_answer("AA", "Speaker").lower()
    _device(monkeypatch, uuids=[("Battery", _uuid("0000180f"))], connected=True)
    assert "read" in bg._no_attributes_answer("AA", "Watch").lower()


# --- characteristic selection ------------------------------------------------

def test_an_unknown_characteristic_is_refused_with_what_is_on_offer():
    rows = [(0x0011, _uuid("2a37"), "Characteristic"), (0x0013, _uuid("2a26"), "Characteristic")]
    found = bg._find(rows, "Battery Level")
    assert isinstance(found, str), found
    assert "Heart Rate Measurement" in found, (
        f"the refusal does not say what the device does offer: {found}"
    )


def test_a_matching_characteristic_resolves_to_its_handle():
    rows = [(0x0011, _uuid("2a37"), "Characteristic")]
    found = bg._find(rows, "Heart Rate Measurement")
    assert found == (0x0011, "Heart Rate Measurement", _uuid("2a37")), found


def test_a_device_with_no_characteristics_says_none_rather_than_an_empty_list():
    rows = [(0x0001, _uuid("180d"), "Service")]
    found = bg._find(rows, "Battery Level")
    assert isinstance(found, str) and "none" in found, found


# --- argument shape ----------------------------------------------------------

def test_arguments_that_are_not_a_mapping_are_refused():
    result = bg._run(["list"])
    assert "not an object" in result or "nothing was read" in result.lower(), result


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

def test_an_impossible_battery_percentage_is_never_printed_as_one():
    """Measured, not hypothesised: a real wearable returned 116%.

    Attribute handles are assigned per connection, so a handle carried over
    from one connection can point at a different characteristic on the next. The
    same handle read 0x0e (14%) minutes before it read 0x74 (116%). "116%" is
    the worst available answer - confidently wrong, about a battery, and
    indistinguishable from a real reading.
    """
    readable, note = bg.describe_value("Battery Level", "74")
    assert "116%" not in readable, f"an out-of-range value was shown as a percentage: {readable}"
    assert "0-100" in readable or "outside" in readable, readable
    # The in-range cases still read as percentages.
    assert bg.describe_value("Battery Level", "12")[0] == "18%"
    assert bg.describe_value("Battery Level", "00")[0] == "0%"
    assert bg.describe_value("Battery Level", "64")[0] == "100%"


def test_a_notification_only_characteristic_says_so_rather_than_looking_asleep():
    """`Characteristic value/descriptor read failed: Attribute can't be read` -
    measured from a real heart-rate characteristic.

    Heart rate has no stored value; the device answers only while measuring.
    Telling somebody to "wake the device up" when it is already awake and worn
    sends them off to do the wrong thing, so the refusal names the real reason.
    """
    assert "Heart Rate Measurement" in bg._NOTIFY_ONLY
    note = bg._NOTIFY_ONLY["Heart Rate Measurement"].lower()
    assert "subscribed" in note or "notification" in note, note


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


def test_the_moyoung_find_packet_is_gadgetbridges_bytes():
    """FE EA | 0x10 (MTU 20) | length 5 | CMD_FIND_MY_WATCH 97. Asserted as a
    literal, so a framing change cannot pass by being re-derived the same way."""
    assert bg.FIND_MOYOUNG == "feea100561"
    assert int(bg.FIND_MOYOUNG[8:10], 16) == 97


def test_a_moyoung_watch_is_sent_the_find_command_on_fee2():
    chars = [_char(0x0003, "2a19"), _char(0x0045, "fee2"), _char(0x0047, "fee3")]
    assert bg.find_route(chars, [_svc("180f"), _svc("feea")]) == (
        0x0045, "feea100561", "MoYoung find-my-watch")


def test_fee2_without_the_moyoung_service_is_not_written_to():
    """0xFEE2 alone is a number other vendors use; the packet means something
    else, or nothing, to them."""
    assert bg.find_route([_char(0x0045, "fee2")], [_svc("180f")]) is None


def test_the_standard_immediate_alert_wins_and_sends_high_alert():
    chars = [_char(0x0010, "2a06"), _char(0x0045, "fee2")]
    assert bg.find_route(chars, [_svc("1802"), _svc("feea")]) == (
        0x0010, "02", "Immediate Alert")


def test_a_device_with_no_known_route_is_refused_and_nothing_is_written(monkeypatch, granted_gatt):
    monkeypatch.setattr(bg, "paired", lambda: [("B3:69:73:62:B2:B6", "Band")])
    monkeypatch.setattr(bg, "looks_like_low_energy", lambda mac: True)
    monkeypatch.setattr(bg, "attributes", lambda mac: [_char(0x0003, "2a19")])
    monkeypatch.setattr(bg, "services", lambda mac: [_svc("180f")])
    written = []
    monkeypatch.setattr(bg, "_write_command", lambda *a, **k: written.append(a))
    out = bg._run_find({"device": "band"})
    assert written == [], out
    assert "Nothing was sent" in out


def test_find_is_refused_without_consent(monkeypatch):
    class Off:
        def get_bool(self, key, default=False):
            return False
    monkeypatch.setattr(bg, "ChronoaConfig", Off)
    written = []
    monkeypatch.setattr(bg, "_write_command", lambda *a, **k: written.append(a))
    out = bg._run_find({"device": "watch"})
    assert written == [] and "bluetooth-gatt-enabled" in out and "Nothing was sent" in out, out


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


def test_the_cccd_is_found_after_its_own_value():
    assert bg.cccd_for(_REAL_DESC, 0x0047) == 0x0048
    assert bg.cccd_for(_REAL_DESC, 0x0042) == 0x0043


def test_a_value_with_no_cccd_before_the_next_declaration_has_none():
    """fee2 is write-only: the 0x2902 after it belongs to fee3, not to it."""
    assert bg.cccd_for(_REAL_DESC, 0x0045) is None


def test_list_does_not_send_the_person_to_connect_a_device_a_read_reaches_anyway(monkeypatch):
    monkeypatch.setattr(bg, "looks_like_low_energy", lambda mac: True)
    monkeypatch.setattr(bg, "_connected", lambda mac: False)
    out = bg._list_all([("B3:69:73:62:B2:B6", "FB BGS002")])
    assert "connect it to read" not in out, out
    assert "connects to it" in out, out


def test_a_user_description_before_the_cccd_is_skipped():
    desc = ("handle = 0x0010, uuid = 00002a37-0000-1000-8000-00805f9b34fb\n"
            "handle = 0x0011, uuid = 00002901-0000-1000-8000-00805f9b34fb\n"
            "handle = 0x0012, uuid = 00002902-0000-1000-8000-00805f9b34fb\n")
    assert bg.cccd_for(desc, 0x0010) == 0x0012
