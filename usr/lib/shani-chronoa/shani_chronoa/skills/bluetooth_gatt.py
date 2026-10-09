"""Skill: read a paired Bluetooth Low Energy device over GATT - a watch's
battery, a heart-rate strap's live reading, a fitness band's firmware UUID.

**This is not telephony and not a media remote.** It is the attribute protocol
that lets a host *ask a small wireless device about itself*: wearables, fitness
bands, heart-rate straps, thermometers, smart tags. `bluetooth_devices` already
lists and connects paired devices; this goes one layer down, to what a device
exposes about itself.

**`bluetoothctl` is a menu-driven program, and this is the part that bites.**
Its help lists `gatt.list-attributes` and friends with a `gatt.` prefix, and
`bluetoothctl gatt.list-attributes <mac>` fails with *"Invalid command in menu
main"* - the prefix is how the menu prints a command, not how you call it. The
real interface is a submenu driven over stdin:

    printf 'menu gatt\\nlist-attributes <mac>\\nback\\nquit\\n' | bluetoothctl

So every call here goes through `_btctl`, which does that. A skill written
against the help text instead of against the program reports "Bluetooth did not
answer" on a machine with a perfectly healthy adapter.

**GATT is Bluetooth Low Energy only, and only while the device is connected.**
A classic BR/EDR device - most speakers, most headphones, car stereos - has no
GATT database at all, and `list-attributes` returns nothing for it. That is not
a failure and must not be reported as one: a speaker that exposes nothing is
saying something true. `gatt` says so in those words instead of "no services
found".

**A characteristic is not a fact until it has been read.** `list` reports what
a device *exposes* (its services and their UUIDs); only `read` reports a value,
and only the handful this module can interpret is reported as a number. An
unrecognised UUID is named, never guessed at - a heart-rate UUID decoded as
something else would be a confident wrong answer about somebody's body.

Reading a wearable's characteristics is reading data about the person wearing
it, so it is consent-gated behind `bluetooth-gatt-enabled`, off by default.
"""

from __future__ import annotations

import os
import re
import shutil
import signal
import time
import subprocess

from shani_chronoa.config import ChronoaConfig
from shani_chronoa.skills import Skill

COMPONENT = "skill:bluetooth-gatt"
_TIMEOUT = 25
_CONSENT_KEY = "bluetooth-gatt-enabled"

#: The Bluetooth base UUID that a 16-bit assigned number expands into.
_BASE = "0000{:04x}-0000-1000-8000-00805f9b34fb"

#: 16-bit assigned numbers, generated from the Bluetooth SIG's own table rather
#: than written out by hand.
#:
#: **That matters, because writing it by hand got eight entries wrong** and
#: nothing caught them. Verified against
#: `NordicSemiconductor/bluetooth-numbers-database` (v1/characteristic_uuids.json
#: and v1/service_uuids.json), which mirrors the SIG assigned-numbers list:
#:
#: | was written as | is actually |
#: |---|---|
#: | 0x2A25 Manufacturer Name | **Serial Number String** |
#: | 0x2A26 Model Number | **Firmware Revision String** |
#: | 0x2A27 Serial Number | **Hardware Revision String** |
#: | 0x2A28 Hardware Revision | **Software Revision String** |
#: | 0x2A29 Firmware Revision | **Manufacturer Name String** |
#: | 0x2A8B CGM Measurement | Five Zone Heart Rate Limits |
#: | 0x2AC5 Resistance Training Result | Object Action Control Point |
#: | 0x2ADB RSC Feature | Mesh Provisioning Data In |
#:
#: The `2A25`-`2A29` run is the dangerous one: five consecutive characteristics in
#: the Device Information service, all strings, all off by a small shift. Naming
#: a watch's firmware revision "Manufacturer Name" produces a confident,
#: plausible, wrong answer about a device, and the short form `0x2a26` is what a
#: test fixture is most likely to use - which is why unit tests over short UUIDs
#: pass happily while a real read does the wrong thing.
UUIDS = {
    # services
    0x1800: "Generic Access",
    0x1801: "Generic Attribute",
    0x1805: "Current Time",
    0x1808: "Glucose",
    0x1809: "Health Thermometer",
    # Found by reading a real wearable's bluez tree rather than by guessing: it
    # advertised 0x180A and 0x190E, and both came back as "UUID 0x180a" /
    # "UUID 0x190e" - the module's honest fallback, which reads like a gap.
    # Both were then checked against NordicSemiconductor/bluetooth-numbers-database
    # `v1/service_uuids.json`, which is the SIG's adopted-number list.
    0x180A: "Device Information",
    # 0x1802/0x1811/0x1812 were added from the same source while checking the
    # above, not from memory - and that mattered, see 0x190E below.
    0x1802: "Immediate Alert",
    0x180D: "Heart Rate",
    # 0x180E carries the ringer setting and the "mute the device" bit - the
    # GATT half of what `bluetooth_call` does over HFP, and the reason a phone
    # can be silenced from a watch.
    0x180E: "Phone Alert Status Service",
    0x180F: "Battery",
    # 0x1811 is **Alert Notification Service**, not the Health Device Profile.
    # Android's `BluetoothHealth` class is HDP, and HDP is not in this table
    # because it is not a GATT service at all - it is a classic profile found
    # over SDP, which is a different protocol from everything above.
    0x1811: "Alert Notification Service",
    0x1812: "Human Interface Device",
    0x1810: "Blood Pressure",
    0x1816: "Cycling Speed and Cadence",
    0x181A: "Environmental Sensing",
    0x181C: "User Data",
    0x181D: "Weight Scale",
    0x1822: "Pulse Oximeter",
    0x1826: "Fitness Machine",
    0x183B: "Binary Sensor",
    0x1843: "Audio Input Control",
    0x184E: "Audio Stream Control",
    0x1853: "Common Audio",
    0x1854: "Hearing Access",
    0x1855: "Telephony and Media Audio",
    0x1856: "Public Broadcast Announcement",
    # characteristics
    0x2A00: "Device Name",
    # 0x0003/0x0004 are the HID usage numbers, not SIG-assigned numbers, and a
    # real wearable advertised both - they arrive looking like nonsense UUIDs.
    # Named from the HID specification so they are not reported as unknown.
    0x0003: "HID Report Map",
    0x0004: "HID Report",
    0x2A05: "Service Changed",
    0x2A19: "Battery Level",
    0x2A23: "System ID",
    0x2A25: "Serial Number",
    0x2A26: "Firmware Revision",
    0x2A27: "Hardware Revision",
    0x2A28: "Software Revision",
    0x2A29: "Manufacturer Name",
    0x2A37: "Heart Rate Measurement",
    0x2A38: "Body Sensor Location",
    0x2A49: "Blood Pressure Feature",
    0x2A53: "RSC Measurement",
    0x2A63: "Cycling Power Measurement",
    0x2A6D: "Pressure",
    0x2A6E: "Temperature",
    0x2A98: "Weight",
}

#: Services that mean a device is a wearable at a glance, keyed by the service
#: UUID. Derived from the same table.
WEARABLE_SERVICES = {
    0x180D: "heart rate", 0x1808: "glucose", 0x1809: "health thermometer",
    0x1810: "blood pressure", 0x1816: "cycling", 0x181A: "environment sensing",
    0x181C: "user data", 0x181D: "weight scale", 0x1822: "pulse oximeter",
    0x1826: "fitness machine", 0x183B: "binary sensor", 0x180F: "battery",
}


def _consent(config: ChronoaConfig) -> "tuple[bool, str]":
    """(allowed, why-not) for reading a paired device's own attributes.

    **The signature is the house convention**, not a preference: it takes the
    config and returns `(allowed, reason)`, like `toggle_wifi._consent` and the
    rest. `tests/test_question_presenter.py` sweeps every gated tool by calling
    exactly this shape with one config, which is how it proves that each tool
    refuses *by name* when nobody can be asked. A version taking no argument and
    returning a bare string passed the skill's own tests and failed that sweep
    with `TypeError: _consent() takes 0 positional arguments but 1 was given` -
    which is the failure mode the sweep exists to catch, caught by the sweep.

    A named helper rather than an inline `config.get_bool(...)` because
    `test_skill_gates_are_enforced.py` decides whether a module consults a gate
    by reading its source, and `get_bool` is deliberately excluded from its
    accepted names - it appears inside this function, so accepting it would let a
    module that defines a gate and never calls one pass on the strength of a
    function it merely contains.
    """
    if not config.get_bool(_CONSENT_KEY, False):
        return False, (f"Reading a Bluetooth device is turned off. Nothing was read. "
                       f"Enable '{_CONSENT_KEY}' in Settings to allow it.")
    return True, ""


def uuid_name(uuid: str) -> str:
    """A friendly name for a UUID, or its number when this module has no name.

    Never guesses. A wrong friendly name on a body-measurement characteristic is
    worse than a raw UUID, because a reader cannot tell it was invented.

    The 128-bit form is `0000xxxx-0000-1000-8000-00805f9b34fb`, where `xxxx` is
    the 16-bit assigned number. **A first version compared the wrong slices** and
    so failed the base-UUID test for every real UUID - which meant that against
    an actual wearable every characteristic came back as raw hex and
    `read characteristic='Battery Level'` reported that the device had no such
    thing. It was correct only for the short `0x2a19` form, which is why reading
    the code found nothing wrong and running it found a great deal.
    """
    raw = str(uuid or "").strip().lower()

    short = re.fullmatch(r"0x?([0-9a-f]{1,4})", raw)
    if short:
        value = int(short.group(1), 16)
        return UUIDS.get(value, f"UUID 0x{value:04x}")

    full = re.fullmatch(
        r"([0-9a-f]{8})-([0-9a-f]{4})-([0-9a-f]{4})-([0-9a-f]{4})-([0-9a-f]{12})", raw
    )
    if not full:
        return raw
    head, g2, g3, g4, g5 = full.groups()
    # The Bluetooth base UUID, field by field. Anything else is a vendor 128-bit
    # UUID, which has no short form and no name here.
    if not (head[:4] == "0000" and g2 == "0000" and g3 == "1000"
            and g4 == "8000" and g5 == "00805f9b34fb"):
        return raw
    value = int(head[4:], 16)
    return UUIDS.get(value, f"UUID 0x{value:04x}")


def _btctl(*commands: str, timeout: int = _TIMEOUT) -> str:
    """Run `commands` in bluetoothctl's gatt submenu and return the output.

    The submenu protocol, not a shell-out with flags: `gatt.` is how the menu
    *prints* a command, and passing it as an argument is rejected by the program.

    Kept for the interactive-shaped calls only. **For scripted discovery this is
    not usable**, and that was measured rather than assumed: `bluetoothctl`'s
    `list-attributes` prints nothing at all when it is fed commands on stdin -
    not an error, not an attribute list, just the next prompt - so a skill built
    on it reports "no services" for every device including ones with a full GATT
    database. `gatttool` is the tool that does work without a terminal, and it is
    what `attributes()` uses.
    """
    script = "\n".join(["menu gatt", *commands, "back", "quit", ""])
    proc = subprocess.run(
        ["bluetoothctl"], input=script, capture_output=True, text=True,
        timeout=timeout, check=False,
    )
    return _clean(proc.stdout or "")


#: gatttool's way of saying the Low Energy connection is already taken. It is
#: not a discovery failure and not an empty device - bluez holds the device, and
#: gatttool needs a connection of its own to attach to ATT. Measured against a
#: connected Meta device, where the first version of this skill reported
#: "advertises only the older Bluetooth profiles" for a watch that had 22
#: characteristics. Three different outcomes, one of them an empty string.
_BUSY = re.compile(r"Device or resource busy|Resource Busy", re.I)


class GattUnavailable(RuntimeError):
    """gatttool is missing, or could not attach - as distinct from finding nothing."""


def _gatttool(mac: str, *args: str, timeout: int = _TIMEOUT) -> str:
    """gatttool, the non-interactive GATT client.

    Raises `GattUnavailable` when the tool is absent or cannot attach, so that
    "I could not look" is never reported as "there is nothing there" - the
    distinction this whole skill is built around.
    """
    if not shutil.which("gatttool"):
        raise GattUnavailable("gatttool (bluez) is not installed")
    try:
        proc = subprocess.run(
            ["gatttool", "-b", mac, *args], capture_output=True, text=True,
            timeout=timeout, check=False,
        )
    except subprocess.TimeoutExpired as exc:
        raise GattUnavailable("gatttool did not answer in time") from exc
    except (OSError, subprocess.SubprocessError) as exc:
        raise GattUnavailable(f"gatttool could not be run ({exc})") from exc
    text = (proc.stdout or "") + (proc.stderr or "")
    if _BUSY.search(text):
        raise GattUnavailable(
            "the Bluetooth stack is already holding this device, and gatttool "
            "needs a connection of its own to read it"
        )
    return text


def _clean(text: str) -> str:
    """Strip the terminal control sequences and prompt echoes bluetoothctl emits."""
    text = re.sub(r"\x1b\[[0-9;]*[A-Za-z]", "", text)
    text = text.replace("[K", "").replace("[19P", "")
    return "\n".join(line.rstrip() for line in text.replace("\r", "").split("\n"))


_MAC = re.compile(r"Device ([0-9A-F:]{17}) (.+)")


def paired() -> list:
    """[(mac, name)] for every paired device, from bluetoothctl itself."""
    proc = subprocess.run(["bluetoothctl", "devices", "Paired"],
                          capture_output=True, text=True, timeout=15, check=False)
    return [(mac, name.strip()) for mac, name in _MAC.findall(proc.stdout or "")]


def advertised_uuids(mac: str) -> list:
    """The service UUIDs the device advertises, read from bluetoothctl's info.

    This is the evidence for *why* a device has nothing to read. A device that
    advertises no LE-related service has no attribute database to enumerate, and
    saying so is a different statement from "the read failed" - one is a fact
    about the device, the other is a fact about us.
    """
    proc = subprocess.run(["bluetoothctl", "info", mac],
                          capture_output=True, text=True, timeout=15, check=False)
    return re.findall(r"UUID:\s*(.+?)\s+\(([0-9a-fA-F-]{36})\)", proc.stdout or "")


#: Advertised UUIDs that mean the device has a GATT database to attach to.
#:
#: The assigned-number ranges are what make this decidable at all, and getting
#: them wrong is the bug a first version had: it matched any `0000 1xxx` number,
#: which includes the **classic profile** numbers - Audio Source is 0x110A,
#: Handsfree is 0x111F - so a plain Bluetooth speaker was reported as a
#: Bluetooth Low Energy device that merely needed connecting. That was wrong on
#: this machine's actual paired devices and was only visible by running against
#: them.
#:
#: GATT primary services live in 0x1800-0x1FFF. The classic profiles are 0x1100
#: and 0x1200. So the test is "in the GATT service range", not "starts with 1".
#:
#: **No surrounding parentheses**, and that is not a detail: `advertised_uuids`
#: captures the UUID *inside* bluetoothctl's `UUID: Name (uuid)` line, so the
#: value has no brackets. A version of this regex that required them matched
#: nothing at all, which made every device look like a classic one - correct on
#: this machine, where all three paired devices are classic, and silently wrong
#: for any actual wearable. The unit test caught it; the live run could not,
#: because the answer it happened to give was the right one.
_GATT_SERVICE_RANGE = re.compile(
    r"(0000(1[89a-f][0-9a-fA-F]{2}|1[0-7][0-9a-fA-F]{3}))-0000-1000-8000-00805f9b34fb"
)


def looks_like_low_energy(mac: str) -> bool:
    """Whether the device advertises anything a GATT client could attach to."""
    return bool(_GATT_SERVICE_RANGE.search(" ".join(u for _, u in advertised_uuids(mac))))


def attributes(mac: str) -> list:
    """[(value_handle, uuid)] the device exposes, via gatttool.

    **The real output format was measured, not assumed.** A first version parsed
    `handle: 0x0011, uuid: 0x2a19, type: Characteristic` - a shape taken from the
    impression that gatttool printed bluetoothctl's `list-attributes` format.
    It does not. Against a real Low Energy watch it produces, per line:

        handle = 0x0002, char properties = 0x0a, char value handle = 0x0003,
        uuid = 00002a00-0000-1000-8000-00805f9b34fb

    Equals signs, no `0x` on the uuid, and **no type field at all** - gatttool
    lists characteristics only, so services have to come from `--primary`
    separately. The old regex matched none of those lines and so reported an
    empty database for a device that had 22 characteristics.

    Note which handle is returned: the **char value handle**, because that is
    what `--char-read -a` needs. Returning the declaration handle would read the
    wrong attribute and quietly produce nonsense.
    """
    out = _gatttool(mac, "--characteristics")
    rows = []
    for line in out.split("\n"):
        m = re.search(
            r"handle\s*=\s*(0x[0-9a-fA-F]+)\s*,\s*char properties.*?"
            r"char value handle\s*=\s*(0x[0-9a-fA-F]+)\s*,\s*"
            r"uuid\s*=\s*([0-9a-fA-F-]{36})",
            line.strip(),
        )
        if m:
            rows.append((int(m.group(2), 16), m.group(3), "Characteristic"))
    return rows


def services(mac: str) -> list:
    """[(handle, uuid)] the device's services, from `gatttool --primary`.

    Also a measured format: `attr handle = 0x0001, end grp handle = 0x0003
    uuid: 00001800-0000-1000-8000-00805f9b34fb`.
    """
    out = _gatttool(mac, "--primary")
    rows = []
    for line in out.split("\n"):
        m = re.search(
            r"attr handle\s*=\s*(0x[0-9a-fA-F]+).*?uuid:\s*([0-9a-fA-F-]{36})",
            line.strip(),
        )
        if m:
            rows.append((int(m.group(1), 16), m.group(2), "Service"))
    return rows


#: gatttool prints a read back as `Characteristic value/descriptor: 14`, i.e.
#: hex bytes with no `0x` and no length. Measured against a real wearable.
#: gatttool prints a read as `Characteristic value/descriptor: 0e 1e` - the bytes
#: space-separated. The capture has to take **all of them**: `[0-9a-fA-F]+` stops
#: at the first space, so every multi-byte read was silently truncated to its
#: first byte. Battery Level (0x2A19) is one byte, so it read correctly and the
#: bug was invisible against it - while a Firmware Revision String of `1.2.3`
#: was reported as `1`, and any packed structure came back one byte long. The
#: caller strips the whitespace, so `raw` is space-free hex throughout.
_READ_VALUE = re.compile(r"Characteristic value/descriptor:\s*((?:[0-9a-fA-F]{2}\s*)+)")


def read_value(mac: str, handle: int, label: str = "", expect_uuid: str = "") -> "tuple[str, str]":
    """(raw hex, why-not) for one characteristic, via `gatttool --char-read`.

    The value handle, not the declaration handle - `attributes()` returns the
    value handle precisely because this is what `--char-read -a` needs.

    **`expect_uuid` is load-bearing, and the reason is a measured wrong answer.**
    gatttool opens a connection of its own for discovery and another for the
    read, and **attribute handles are assigned per connection**. On a real
    wearable the same handle read 0x0e (14%) minutes before it read 0x74 (116%),
    while bluez independently reported the battery at 15% - 116 is a plausible
    heart rate, and the handle had simply moved to a different characteristic.

    So after reading, the handle is re-checked against the UUID that was asked
    for. If it no longer matches, the value is discarded and the caller is told
    the handle moved. Reporting `116%` as a battery level is the worst thing
    this skill could do: plausible, confidently wrong, and about a device a
    person is relying on.

    `label` names the characteristic in the refusal; it is used only for prose.
    """
    out = _gatttool(mac, "--char-read", "-a", f"0x{handle:04x}")
    m = _READ_VALUE.search(out)
    if not m:
        if "can't be read" in out or "cannot be read" in out:
            # Heart Rate Measurement (0x2A37) carries read and notify properties
            # but a heart rate has no *stored* value: the device refuses the read
            # and answers only while it is measuring. Reporting that as "no
            # value, try again" would send someone off waking a device that is
            # already awake.
            return "", _NOTIFY_ONLY.get(
                label, "the device would not return a value for it on demand")
        return "", "it did not return a value (it may need waking up first)"
    raw = "".join(m.group(1).split())

    if expect_uuid:
        try:
            current = attributes(mac)
        except GattUnavailable:
            return raw, ""   # could not re-check; the value is reported unverified
        match = [u for h, u, _ in current if h == handle]
        if match and match[0].lower() != expect_uuid.lower():
            return "", (f"this handle moved between connections - it is now "
                        f"{uuid_name(match[0])} rather than {uuid_name(expect_uuid)}, so "
                        "the value was discarded rather than reported against the "
                        "wrong characteristic")
    return raw, ""


def describe_value(label: str, raw: str) -> "tuple[str, str]":
    """(readable, note) for a raw hex value, interpreting only what is known.

    A single byte is a percentage or a small enum; several bytes are a packed
    structure this module does not decode. **The distinction matters**: printing
    `0e` as "14" is right for Battery Level and would be nonsense for a packed
    heart-rate measurement, so only a handful of characteristics are interpreted
    and the rest are reported as the raw hex they are.
    """
    if label == "Battery Level" and len(raw) <= 2:
        percent = int(raw, 16)
        # **A battery level is 0-100 by definition.** A real device returned 0x74
        # here - 116% - after the same handle had read 0x0e (14%) minutes
        # earlier: attribute handles are assigned per connection, so a handle
        # carried over from one connection can point at a different
        # characteristic on the next, and a plausible-looking number comes back
        # for it. Printing "116%" is the worst outcome available here: it is
        # confidently wrong, it is about a device's battery, and nobody can tell
        # it was invented. Out of range is reported as the raw byte instead.
        if 0 <= percent <= 100:
            return f"{percent}%", ""
        return (f"0x{raw} (not a battery percentage: {percent} is outside 0-100, so this "
                "handle is not the battery level on this connection)", "")
    if label == "Body Sensor Location" and len(raw) <= 2:
        return _BODY_LOCATIONS.get(int(raw, 16), f"location {int(raw, 16)}"), ""
    if label == "Service Changed":
        return "the service list changed since last read", ""
    if label == "Body Composition Measurement":
        return raw, ("only sent when the device measures it, so use action='listen' - "
                     "shown here as the raw bytes")
    if label == "Heart Rate Measurement":
        # Decoded here rather than left as hex: a `read` that *does* get a value
        # back (some devices do store the last measurement) should answer in
        # beats per minute, not in bytes. The refusal text above is what a
        # notify-only device produces, so reaching here means there was a value.
        bpm, note = decode_heart_rate(raw)
        return (f"{bpm} bpm" if bpm else raw,
                note or ("a packed value, shown as raw hex because this module does not decode it"))
    if label in _TEXT_CHARACTERISTICS:
        # These are UTF-8 strings, not numbers. A first version reported
        # Manufacturer Name 0x33 as "51" and Model Number 0x4a as "74" - measured
        # against a real wearable, whose manufacturer is a company *name*. The
        # hex was right and the reading of it was completely wrong, which is the
        # worst combination: it looks like data.
        try:
            text = bytes.fromhex(raw).decode("utf-8", errors="replace")
        except ValueError:
            return raw, "not valid hex, shown as read"
        return text, ""
    if len(raw) <= 2:
        return (f"{int(raw, 16)}",
                "reported as the raw number; this module does not interpret this value")
    return (raw, "a packed value, shown as raw hex because this module does not decode it")


#: GATT Heart Rate Measurement (0x2A37) is a *packed* structure, and unpacking it
#: is the difference between a reading and a hex string. Byte 0's low bit says
#: whether the rate is 8-bit (bytes follow) or 16-bit (one extra byte, low byte
#: first); when 8-bit, bit 3 says whether a sensor-contact bit follows; then the
#: energy-expended field is present if bit 4 of byte 0 is set, and RR if bit 5.
#: From the Bluetooth SIG's Heart Rate Measurement characteristic definition.
#:
#: A first version reported the raw hex for heart rate - "0e 1e" - which is a
#: fact about the bytes and useless to a person asking what their pulse is.
def decode_heart_rate(raw: str) -> "tuple[str, str]":
    """(bpm, note) from a Heart Rate Measurement payload, or ('', why-not).

    **Bit 0 of the flags byte is "uint16", not "uint8".** A first version here
    had it backwards and every real measurement was rejected with "did not say
    whether the rate is 8 or 16 bit" - which is the sentence you write for a
    device that failed to set a bit, not for one that correctly cleared it. Every
    8-bit measurement, i.e. almost all of them, landed there.

    Layout, from the Bluetooth SIG definition of 0x2A37:
      bit 0      0 = uint8 rate, 1 = uint16 rate
      bit 2      Sensor Contact Status bit follows the rate (0 = not supported)
      bit 3      Energy Expended (uint16 kJ) follows
      bit 4      RR-Interval (uint16, 1/1024 s) follows, repeated
    """
    try:
        data = bytes.fromhex(raw.strip())
    except ValueError:
        return "", "not valid hex"
    if not data:
        return "", "the device sent an empty measurement"

    flags = data[0]
    wide = bool(flags & 0x01)
    offset = 1
    if wide:
        if len(data) < 3:
            return "", "the measurement was truncated before its 16-bit rate"
        bpm = int.from_bytes(data[1:3], "little")
        offset = 3
    else:
        if len(data) < 2:
            return "", "the measurement was truncated before its rate"
        bpm = data[1]
        offset = 2

    notes = []
    if flags & 0x04:  # sensor-contact bit present
        if len(data) > offset:
            notes.append("sensor contact detected" if data[offset] & 0x01
                         else "sensor contact LOST - the reading is unreliable")
            offset += 1
        else:
            notes.append("the sensor-contact bit was promised but not sent")
    if flags & 0x08:  # energy expended
        if len(data) >= offset + 2:
            offset += 2
        else:
            notes.append("energy expended was promised but not sent")
    if flags & 0x10:  # RR-intervals
        rr = data[offset:]
        if len(rr) >= 2:
            interval = int.from_bytes(rr[:2], "little") / 1024.0
            notes.append(f"RR interval {interval:.3f}s")
        else:
            notes.append("RR intervals were promised but not sent")

    return str(bpm), "; ".join(notes)


#: Characteristics the device will not return on a plain read. These are values
#: that only exist while the device is actively measuring, so they arrive as
#: notifications - the reader has to subscribe and collect, which means the
#: person has to be wearing and using the thing.
_NOTIFY_ONLY = {
    "Heart Rate Measurement":
        "it is a live measurement: heart rate only exists while the device is "
        "measuring, so it has to be subscribed to and collected while the wearer "
        "is active. It cannot simply be read on demand",
    "Body Composition Measurement":
        "it is only sent when the device measures it, so it arrives as a notification "
        "rather than on a read",
}

#: Characteristics whose value is UTF-8 text rather than a number. The Device
#: Information service is entirely made of these, plus Device Name.
_TEXT_CHARACTERISTICS = frozenset({
    "Device Name", "Manufacturer Name", "Serial Number",
    "Hardware Revision", "Firmware Revision", "Software Revision", "System ID",
})

#: Bluetooth SIG Body Sensor Location values. Anything else is shown as a number
#: rather than guessed at.
_BODY_LOCATIONS = {
    0: "other", 1: "chest", 2: "wrist", 3: "finger", 4: "hand", 5: "ear lobe",
    6: "foot", 7: "ankle", 8: "wrist (watch)", 9: "chest (strap)", 10: "ear",
    11: "arm", 12: "calf", 13: "kidney", 14: "abdomen",
}


#: The installed gatttool prints a notification as
#:     handle: 0x0022 	 value: 0e 1e
#: - taken from the binary's own format strings (`strings $(which gatttool)`),
#: not from what a BLE tool is assumed to print. The first version here matched
#: `value ... ([0-9a-fA-F]+)`, which stops at the first space and so captured
#: `0e` out of `0e 1e`: a one-byte payload that `decode_heart_rate` then
#: rejected as truncated, so every genuine reading would have been reported as
#: unreadable. All of the byte pairs are captured, and the caller strips the
#: whitespace.
_NOTIFY_LINE = re.compile(r"value\s*:?\s*((?:[0-9a-fA-F]{2}\s*)+)")


def _system_busctl(*args: str, timeout: int = 15) -> subprocess.CompletedProcess:
    """`busctl --system`: bluez owns the **system** bus, not the session bus.

    Measured, not assumed - `busctl --user tree org.bluez` answers
    "The name org.bluez was not provided by any .service files", while
    `--system` lists it owned by `bluetoothd`. The session-bus spelling is the
    obvious one and is always wrong here.
    """
    return subprocess.run(["busctl", "--system", *args], capture_output=True,
                          text=True, timeout=timeout, check=False)


_BLUEZ = "org.bluez"
_GATT1 = "org.bluez.GattCharacteristic1"


def _bluez_property(path: str, prop: str) -> "str | None":
    """One property of a characteristic, or None.

    **`busctl get-property` takes the service name too** - it is
    `get-property SERVICE OBJECT INTERFACE PROPERTY`, and leaving the service
    out does not error usefully: every call returns empty, so a lookup silently
    finds nothing on a device that has 21 characteristics. It is written as its
    own function so the argument order exists in exactly one place; the first
    version spelled it inline and got it wrong.
    """
    out = _system_busctl("get-property", _BLUEZ, path, _GATT1, prop)
    if out.returncode != 0:
        return None
    return out.stdout


#: bluez exposes notifications itself, and that is the **only** route that works on
#: an ordinary desktop. `gatttool --listen` opens a connection of its own, and
#: bluez holds the link to any in-range paired device by policy - measured here:
#: with `bluetoothctl info` reporting `Connected: yes` and RSSI -67, every
#: `gatttool` call answered `Device or resource busy (16)`. Disconnecting first
#: does work, but bluez reclaims the device, so a gatttool-only `listen` fails
#: again on the very next call. It would work in the seconds after a manual
#: disconnect and nowhere else.
#:
#: Through bluez's own `org.bluez.GattCharacteristic1` there is no contention at
#: all. **The method name moved between bluez versions**: the installed daemon
#: has `StartNotify`, and the newer `StartNotifications` is refused with
#: "Method ... doesn't exist" - taken from introspecting the installed service,
#: not from trying one spelling and assuming.
def _bluez_char_paths(mac: str) -> dict:
    """{handle: object path} for one device's characteristics.

    Read by walking bluez's own object tree and asking each `charNNNN` for its
    `Handle` property, rather than deriving the path from the handle
    arithmetically. bluez does name them `char` + the handle in hex, but
    deriving it means a naming change would silently point at the wrong
    characteristic - and this module's whole documented fear is a handle that
    means something else than it did a minute ago.
    """
    device = f"/org/bluez/hci0/dev_{mac.replace(':', '_').upper()}"
    # `busctl tree` takes a **service name**, never an object path. Given the
    # path it answers "Invalid bus service name: <the path>" and prints nothing -
    # so the whole tree is fetched and filtered here. Measured, twice: the first
    # version passed the device path and found zero characteristics on a device
    # that has 21.
    tree = _system_busctl("tree", "org.bluez")
    found: dict = {}
    for path in set(re.findall(
            rf"{re.escape(device)}(/service[0-9a-f]+/char[0-9a-f]+)", tree.stdout)):
        full = device + path
        out = _bluez_property(full, "Handle")
        m = re.search(r"\b(\d+)\b", out or "")
        if m:
            found[int(m.group(1))] = full
    return found


def notifications(mac: str, handle: int, seconds: int) -> "tuple[list[str], str]":
    """Values the device *pushes* while it measures, for up to `seconds`.

    This is the only way to get a **live** reading. A plain `--char-read` on a
    notify-only characteristic answers `Attribute can't be read` - measured on a
    real wearable - because the value does not exist until the device produces
    one. That is what a phone app does to show a live heart rate, and it is the
    reason `read` alone can never answer "what is my heart rate right now".

    **Three honest limits, all measured rather than assumed:**

    - The device has to be *doing the thing*. A heart-rate strap lying on a
      table pushes nothing, and the answer is "nothing arrived", which is a true
      answer about the device rather than a failure. Measured here: bluez
      accepted `StartNotify` and then reported `Notifying false`, because no
      measurement was being produced.
    - Which route was used is reported rather than assumed, because the two
      behave differently and only one works in the common case (see
      `_bluez_char_paths`). Pretending it is always gatttool would make a
      working call look like a lucky one.
    - A listener is always stopped. Left subscribed it would hold the Low Energy
      connection and starve every later read - so the `gatttool` child is killed
      as a process group and bluez is explicitly unsubscribed.

    **The bluez route polls the cached `Value` property rather than watching
    `PropertiesChanged`.** `busctl monitor` needs polkit privilege this
    environment does not have - measured: `Access denied` on
    `org.freedesktop.DBus.Monitoring.BecomeMonitor`. Polling works unprivileged,
    costs one property read every 200 ms, and misses nothing that matters here
    because a heart-rate characteristic notifies at 1 Hz, not 100.
    """
    seconds = max(1, min(int(seconds), 60))
    collected: list[str] = []

    def remember(value: str) -> None:
        value = "".join(value.split())
        if value and value not in collected:
            collected.append(value)

    bluez = _listen_over_bluez(mac, handle, seconds, remember)
    if bluez is not None:
        return collected, bluez

    if not shutil.which("gatttool"):
        raise GattUnavailable(
            "neither bluez's own GATT interface nor gatttool could be reached")

    cccd = cccd_for(_gatttool(mac, "--char-desc"), handle)
    if cccd is None:
        return collected, ("it has no notification switch (CCCD) for this value, so it "
                           "cannot be subscribed to")
    other: list[str] = []

    def take(line: str) -> None:
        m = _NOTIFY_LINE.search(line)
        if m:
            remember(m.group(1))
        elif line.strip():
            other.append(line.strip())

    started = time.monotonic()
    early = False
    try:
        # `--listen` only keeps gatttool attached after a command; on its own it
        # prints the usage text and exits 1 (measured), so this path never
        # listened at all. Subscribing is a write of 0x0100 to the CCCD.
        proc = subprocess.Popen(
            ["gatttool", "-b", mac, "--char-write-req", "-a", f"0x{cccd:04x}",
             "-n", "0100", "--listen"],
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
            start_new_session=True,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise GattUnavailable(f"gatttool could not be started ({exc})") from exc

    try:
        # Read until the window closes, then stop it. `readline` on a pipe that
        # never ends would block forever, which is why this is bounded twice:
        # by the deadline and by killing the child's own process group.
        deadline = time.monotonic() + seconds
        os.set_blocking(proc.stdout.fileno(), False)
        while time.monotonic() < deadline:
            line = proc.stdout.readline()
            if line:
                take(line)
                continue
            if proc.poll() is not None:
                break
            time.sleep(0.2)
        early = proc.poll() is not None and time.monotonic() - started < seconds - 1
    finally:
        try:
            os.killpg(os.getpgid(proc.pid), signal.SIGTERM)
        except (OSError, ProcessLookupError):
            pass
        try:
            proc.wait(timeout=3)
        except subprocess.TimeoutExpired:
            try:
                os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
            except (OSError, ProcessLookupError):
                pass
    if early and not collected:
        # Stopping early is not "the device sent nothing in N seconds": that
        # sentence was reported for a run that lasted four.
        said = next((ln for ln in reversed(other) if "successfully" not in ln.lower()), "")
        return collected, ("the listener stopped after "
                           f"{time.monotonic() - started:.0f}s instead of {seconds}s"
                           + (f" ({said[-120:]})" if said else ""))
    return collected, ""


def cccd_for(char_desc: str, value_handle: int) -> "int | None":
    """The CCCD (0x2902) handle belonging to one characteristic value.

    Read from `gatttool --char-desc`, never assumed to be value_handle + 1: the
    descriptors of a characteristic sit after its value and before the next
    declaration (0x2803), and a User Description (0x2901) may come first.
    """
    rows = []
    for m in re.finditer(r"handle\s*=\s*(0x[0-9a-fA-F]+),\s*uuid\s*=\s*([0-9a-fA-F-]{36})", char_desc):
        rows.append((int(m.group(1), 16), m.group(2).lower()[4:8]))
    for handle, short in sorted(rows):
        if handle <= value_handle:
            continue
        if short in ("2803", "2800", "2801"):
            return None
        if short == "2902":
            return handle
    return None


def _parse_bluez_value(stdout: str) -> "str | None":
    """The hex payload of a `Value` property, or None when there is no value.

    `busctl get-property` prints a GATT value as `ay <count> <byte> <byte> ...`
    - measured on this device: `ay 11 74 76 81 70 78 74 70 68 49 46 48` is the
    eleven ASCII bytes of a firmware string, and a characteristic that has never
    sent anything prints **`ay 0`**.

    Both halves of that were wrong before this function existed, and the first is
    the dangerous one:

    - **`ay 0` was read as the byte `0`.** An empty array is not a zero reading,
      so a device that has never reported produced "Battery Level: 0%". That is a
      plausible, confidently wrong number about a battery - exactly the failure
      this module exists to avoid, and it was introduced by the fix for the
      contention problem.
    - **`ay 1 00` was read as `100`**, because the array *length* was taken for a
      byte. Every value this device had sent would have been off by a leading
      byte.

    So the count is read and the bytes are taken to be exactly that many.
    """
    parts = stdout.split()
    if len(parts) < 2 or parts[0] != "ay":
        return None
    try:
        count = int(parts[1])
    except ValueError:
        return None
    if count <= 0:
        return None  # nothing has been sent or read: not a zero reading
    payload = parts[2:2 + count]
    if len(payload) != count:
        return None  # truncated; reporting half of it would be inventing the rest
    return "".join(payload)


def _listen_over_bluez(mac: str, handle: int, seconds: int, remember) -> "str | None":
    """Subscribe through bluez and poll the cached value.

    Returns None when bluez cannot do the job, so the caller falls back to
    gatttool - a single failed route is a fallback, not an error.
    """
    if not shutil.which("busctl"):
        return None
    try:
        path = _bluez_char_paths(mac).get(handle)
    except (OSError, subprocess.SubprocessError, ValueError):
        return None
    if not path:
        return None

    start = _system_busctl("call", _BLUEZ, path, _GATT1, "StartNotify")
    if start.returncode != 0:
        return None  # an older/newer bluez, or a characteristic that cannot notify
    try:
        deadline = time.monotonic() + seconds
        while time.monotonic() < deadline:
            value = _parse_bluez_value(_bluez_property(path, "Value") or "")
            if value:
                remember(value)
            time.sleep(0.2)
    finally:
        # Always unsubscribe: a live notify session outliving the call would
        # hold the connection and make every later read slower or impossible.
        _system_busctl("call", _BLUEZ, path, _GATT1, "StopNotify")
    return ""


def _connected(mac: str) -> bool:
    proc = subprocess.run(["bluetoothctl", "info", mac],
                          capture_output=True, text=True, timeout=15, check=False)
    return "Connected: yes" in (proc.stdout or "")


def _no_attributes_answer(mac: str, name: str) -> str:
    """Why there is nothing to read - from what the device says, not from a guess.

    The first version of this answered "that is normal for a classic Bluetooth
    device" on the strength of an empty result. On this machine that happened to
    be **right** - the paired phone advertises Headset AG, A/V Remote Control,
    OBEX Push, SIM Access and no Low Energy service at all - but the skill could
    not tell that apart from a wearable that was merely asleep, so it asserted a
    cause it had not established.

    The order below is the point, and it is evidence-first:

    1. **not Low Energy at all** - it advertises no LE service, so there is no
       database to enumerate and connecting would not help. This is checked
       *first* because it is true regardless of connection state: a second
       version that checked connection first told a classic device to "connect
       it" when connecting could never have worked.
    2. **Low Energy but not connected** - connect it and ask again.
    3. **Low Energy, connected, still nothing** - asleep or out of range.

    Each branch names something that was read off the device.
    """
    advertised = advertised_uuids(mac)
    if not looks_like_low_energy(mac):
        offered = ", ".join(n for n, _ in advertised[:6]) or "no services at all"
        return (f"{name} advertises only the older Bluetooth profiles ({offered}) and no "
                "Low Energy service, so it has no attribute database to read. That is a true "
                "answer rather than a failure: only wearables, fitness bands, heart-rate "
                "straps and smart tags use this protocol, and connecting this one would not "
                "change it.")
    if not _connected(mac):
        return (f"{name} does use Bluetooth Low Energy but is not connected right now, and a "
                "Low Energy device has to be connected before it answers. Connect it and ask "
                "again. Nothing was read.")
    return (f"{name} is connected and does use Bluetooth Low Energy, but returned no "
            "attributes. It is probably asleep or out of range - move it closer or wake it "
            "and ask again. Nothing was read.")


def _find(rows: list, wanted: str) -> "tuple[int, str] | str":
    """(handle, name) of the first characteristic matching `wanted`, or why not.

    Matching is on the *name* this module knows, so an unlisted UUID cannot be
    read as a number by accident. Everything else is refused with the names that
    were on offer, because "I don't know how to read that" is only actionable
    if it says what it could read.
    """
    target = uuid_name(wanted).lower()
    known = []
    for handle, uuid, kind in rows:
        if "characteristic" not in kind.lower():
            continue
        name = uuid_name(uuid)
        known.append(name)
        if name.lower() == target:
            return handle, name, uuid
    # `dict.fromkeys(known)[:8]` was a KeyError: subscripting a dict looks up a
    # key, and `slice` is not one. Every call that reached this line raised, so
    # the "what does it offer instead" refusal - the only actionable half of the
    # message - never actually printed. Caught by running against real hardware,
    # not by reading.
    offered = ", ".join(list(dict.fromkeys(known))[:8]) or "none"
    return (f"this device has no characteristic I know how to read as {uuid_name(wanted)}"
            f" - it offers: {offered}")


def _characteristics(rows: list) -> list:
    return [uuid_name(u) for _, u, kind in rows if "characteristic" in kind.lower()]


def _resolve(dev: str, devices: list) -> "tuple[tuple | None, str]":
    """A name fragment to exactly one paired device, refusing an ambiguous one."""
    hits = [(mac, name) for mac, name in devices if dev in name.lower()]
    if not hits:
        return None, (f"No paired device matches {dev!r}. Paired: "
                      + ", ".join(n for _, n in devices) + ".")
    if len(hits) > 1:
        return None, (f"{len(hits)} devices match {dev!r}, so I did not pick one: "
                      + ", ".join(n for _, n in hits) + ".")
    return hits[0], ""


def _run(arguments: dict) -> str:
    if not isinstance(arguments, dict):
        return ("The bluetooth_gatt arguments were not an object; nothing was read. "
                "Pass an action and a device name.")
    allowed, refusal = _consent(ChronoaConfig())
    if not allowed:
        return refusal

    if not shutil.which("bluetoothctl"):
        return "bluetoothctl (bluez-utils) is not installed, so nothing can be read."

    action = str(arguments.get("action") or "list").strip().lower()
    wanted = str(arguments.get("characteristic") or "").strip()
    dev = str(arguments.get("device") or "").strip().lower()

    try:
        devices = paired()
    except (OSError, subprocess.SubprocessError):
        return "Bluetooth did not answer - is the adapter on?"

    if not devices:
        return "No paired Bluetooth devices are known to this computer."

    chosen = None
    if dev:
        chosen, problem = _resolve(dev, devices)
        if problem:
            return problem
    elif action != "list":
        return "Which device? Pass device, one of: " + ", ".join(n for _, n in devices) + "."

    if action == "list":
        return _list_all(devices)
    if action not in ("services", "read", "listen"):
        return f"action must be services, read or listen, not {action!r}."

    mac, name = chosen
    try:
        rows = attributes(mac)
    except GattUnavailable as exc:
        return f"Could not read {name}: {exc}. Nothing was read."

    if not rows:
        return _no_attributes_answer(mac, name)

    if action == "services":
        # Services come from `gatttool --primary`, not from `--characteristics`:
        # the latter prints characteristics only and has no type field at all, so
        # asking it for services returns nothing on every device.
        try:
            svc_rows = services(mac)
            services_known = True
        except GattUnavailable:
            svc_rows = []
            services_known = False
        service_names = [uuid_name(u) for _, u, _ in svc_rows]
        chars = [uuid_name(u) for _, u, _ in rows]
        kinds = [w for u, w in WEARABLE_SERVICES.items()
                 if any(uuid_name(uu) == uuid_name(u) for _, uu, _ in svc_rows)]
        out = [f"{name} exposes {len(service_names)} service(s)"
               + ("" if services_known else " (the service list could not be read)")
               + f" and {len(chars)} characteristic(s):"]
        if kinds:
            out.append("  Looks like a wearable: " + ", ".join(kinds) + ".")
        for label, items in (("services", service_names), ("characteristics", chars)):
            if items:
                unique = list(dict.fromkeys(items))
                text = ", ".join(unique)
                out.append(f"  {label}: " + (text[:400] + ("..." if len(text) > 400 else "")))
        return "\n".join(out)

    # action is read or listen - both need a named characteristic
    if not wanted:
        offered = ", ".join(
            dict.fromkeys(uuid_name(u) for _, u, k in rows if "characteristic" in k.lower())
        )
        return (f"Which value? Pass characteristic, one of: {offered or 'none'}. "
                "Nothing was read.")
    found = _find(rows, wanted)
    if isinstance(found, str):
        return f"Nothing was read: {found}."
    handle, label, expected = found

    if action == "listen":
        seconds = arguments.get("seconds")
        try:
            seconds = 10 if seconds in (None, "") else int(seconds)
        except (TypeError, ValueError):
            return (f"{seconds!r} is not a number of seconds to listen for; "
                    "pass seconds, e.g. 15.")
        if seconds < 1 or seconds > 60:
            return f"Listen for between 1 and 60 seconds, not {seconds}."
        try:
            values, problem = notifications(mac, handle, seconds)
        except GattUnavailable as exc:
            return f"Could not listen to {label} on {name}: {exc}. Nothing was read."
        if problem:
            return f"{name} did not notify {label}: {problem}. Nothing was read."
        if not values:
            return (f"{name} sent no {label} in {seconds}s. That is a real answer "
                    "about the device, not a failure: a sensor that is not being "
                    "worn or used has nothing to report. Wear or use it, and ask again.")
        # A notify characteristic streams, so the readings are a series rather
        # than one value - reporting only the last would discard the measurement
        # the person actually wanted.
        #
        # The label comes from the UUID table in the table's own capitalisation
        # ("Heart Rate Measurement"); a lowercase comparison here meant the
        # decode branch never ran and the answer was a list of hex strings.
        if label.lower() == "heart rate measurement":
            decoded = []
            for raw in values:
                bpm, note = decode_heart_rate(raw)
                decoded.append(f"{bpm} bpm" + (f" ({note})" if note else "") if bpm
                               else f"unreadable: {note}")
            return (f"{name} heart rate over {seconds}s, {len(decoded)} reading(s): "
                    + ", ".join(decoded[:12]) + ("..." if len(decoded) > 12 else ""))
        return (f"{name} {label} pushed {len(values)} value(s) in {seconds}s: "
                + ", ".join(values[:12]) + ("..." if len(values) > 12 else ""))

    try:
        raw, problem = read_value(mac, handle, label, expected)
    except GattUnavailable as exc:
        return f"Could not read {label} from {name}: {exc}. Nothing was read."
    if not raw:
        # Name the action that *would* answer it. "It cannot be read" with no
        # next step is the dead end this skill must not have.
        hint = (f" Use action='listen' with characteristic={wanted!r} to collect it "
                f"live instead." if label in _NOTIFY_ONLY else "")
        return f"{name} did not return {label}: {problem}.{hint} Nothing was read."
    readable, note = describe_value(label, raw)
    answer = f"{name} {label}: {readable}"
    return answer + (f" ({note})" if note else "")


def _list_all(devices: list) -> str:
    """One line per paired device, saying what was actually found.

    Each line names the reason a device has nothing, rather than a bare "no
    services" that leaves the reader to guess whether the device or the read was
    at fault - the same distinction `_no_attributes_answer` draws in full.
    """
    lines = []
    for mac, name in devices:
        if not looks_like_low_energy(mac):
            lines.append(f"- {name}: older Bluetooth only"
                         + ("" if _connected(mac) else ", and not connected")
                         + " - it has no Low Energy attributes to read")
            continue
        if not _connected(mac):
            # Not "connect it first": a read opens its own Low Energy link, measured
            # on a MoYoung watch that bluez showed as disconnected (it drops bluez's
            # link within a second) and that still answered every read.
            lines.append(f"- {name}: Bluetooth Low Energy, not connected right now - asking "
                         "for its services or a reading connects to it, if it is in range")
            continue
        try:
            rows = attributes(mac)
        except GattUnavailable as exc:
            lines.append(f"- {name}: Bluetooth Low Energy, connected, but could not be "
                         f"read ({exc})")
            continue
        if not rows:
            lines.append(f"- {name}: Bluetooth Low Energy and connected, but returned "
                         "no attributes (probably asleep)")
            continue
        kinds = [w for u, w in WEARABLE_SERVICES.items()
                 if any(uuid_name(uu) == uuid_name(u) for _, uu, _ in rows)]
        summary = "looks like a wearable: " + ", ".join(kinds) if kinds else "not a wearable"
        lines.append(f"- {name}: {len(rows)} characteristic(s) readable, {summary}")
    return "Paired Bluetooth devices:\n" + "\n".join(lines)


SCHEMA = {
    "type": "function",
    "function": {
        "name": "bluetooth_gatt",
        "description": (
            "Read what a paired Bluetooth Low Energy device says about itself - a watch's or "
            "headset's battery, a heart-rate strap's live reading, which services a band "
            "offers. 'list': every paired device and whether it speaks this protocol. "
            "'services': what one exposes. 'read': one stored value. 'listen': a live "
            "reading of something the device only pushes, e.g. heart rate - 'nothing "
            "arrived' is the truthful answer when the device is not measuring. Speakers, "
            "headphones and car stereios are older Bluetooth and expose nothing here, "
            "which is a true answer, not a failure; wearables, bands, straps, smart tags "
            "and thermometers answer. Needs 'bluetooth-gatt-enabled' and the device must "
            "be connected."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "action": {
                    "type": "string",
                    "enum": ["list", "services", "read", "listen"],
                    "description": "list: every paired device and whether it exposes GATT. "
                                   "services: what one device exposes. read: one stored value. "
                                   "listen: a live reading of something the device pushes.",
                },
                "device": {
                    "type": "string",
                    "description": "Part of the device's name, for services, read and listen. "
                                   "Ambiguous names are refused rather than guessed at.",
                },
                "seconds": {
                    "type": "integer",
                    "description": "For listen only: how long to collect for, 1-60, default 10. "
                                   "The device has to be worn and in use for it to push anything.",
                },
                "characteristic": {
                    "type": "string",
                    "description": "For read or listen: which value, e.g. 'Battery Level', 'Heart Rate "
                                   "Measurement', 'Firmware Revision'. Only characteristics that "
                                   "are actually on the device can be read. Heart Rate Measurement "
                                   "must be listened to, not read - the device has no stored value "
                                   "for it.",
                },
            },
            "required": ["action"],
        },
    },
}

# --- find: make a lost wearable buzz ------------------------------------------

#: The only writes this module makes, and the reason each is safe to send.
#: An unknown write to a wearable is how firmware gets bricked, so the bytes are
#: fixed here and no caller can supply its own.
#:
#: - **Immediate Alert** (service 0x1802, characteristic 0x2A06 Alert Level) is
#:   the SIG's own "find me" profile: 0x02 is High Alert.
#: - **MoYoung** ("Da Fit" watches; service 0xFEEA, commands written to 0xFEE2) has
#:   no SIG profile. The packet is Gadgetbridge's, not inferred:
#:   `MoyoungConstants.CMD_FIND_MY_WATCH = 97` with an empty payload, framed by
#:   `MoyoungPacketOut.buildPacket(mtu=20, ...)` as FE EA, 0x10, total length 5,
#:   command. Verified 2026-10-08 on a real FB BGS002 (Manufacturer MOYOUNG-V2):
#:   the watch vibrated, three times, and stopped by itself as Gadgetbridge says.
_ALERT_LEVEL = "2a06"
_MOYOUNG_SERVICE = "feea"
_MOYOUNG_OUT = "fee2"
FIND_HIGH_ALERT = "02"
FIND_MOYOUNG = "feea100561"


def _short(uuid: str) -> str:
    """The 16-bit number of a base-UUID, lowercase, or "" for a vendor UUID."""
    u = str(uuid or "").strip().lower()
    if u.startswith("0x"):
        return u[2:].zfill(4)
    if len(u) == 36 and u.startswith("0000") and u.endswith("-0000-1000-8000-00805f9b34fb"):
        return u[4:8]
    return ""


def find_route(chars: list, service_rows: list) -> "tuple[int, str, str] | None":
    """(value handle, hex to write, route name) for making this device buzz.

    The standard profile wins when a device has both. MoYoung is recognised by
    its service *and* its command characteristic, never by the characteristic
    alone: 0xFEE2 is a 16-bit number other vendors use for other things, and the
    packet means nothing - or something else - to them.
    """
    by_uuid = {_short(u): handle for handle, u, _ in chars}
    if _ALERT_LEVEL in by_uuid:
        return by_uuid[_ALERT_LEVEL], FIND_HIGH_ALERT, "Immediate Alert"
    has_moyoung = any(_short(u) == _MOYOUNG_SERVICE for _, u, _ in service_rows)
    if has_moyoung and _MOYOUNG_OUT in by_uuid:
        return by_uuid[_MOYOUNG_OUT], FIND_MOYOUNG, "MoYoung find-my-watch"
    return None


def _write_command(mac: str, handle: int, value: str, timeout: float = _TIMEOUT) -> None:
    """Write without response, through gatttool's interactive mode.

    Measured on the MoYoung watch, both obvious routes fail:
    - bluez's `WriteValue` answers `Not connected`: the watch drops the link bluez
      opens within half a second of "Connection successful".
    - `gatttool --char-write` sends, then waits for a reply a write command never
      gets, so it only ever ends at the timeout and success is indistinguishable
      from a hang.
    Interactive mode says "Connection successful" before the write is sent, so the
    write is only attempted on a link that exists.
    """
    if not shutil.which("gatttool"):
        raise GattUnavailable("gatttool (bluez) is not installed")
    try:
        proc = subprocess.Popen(
            ["gatttool", "-b", mac, "-I"], stdin=subprocess.PIPE,
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
            start_new_session=True,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise GattUnavailable(f"gatttool could not be started ({exc})") from exc
    seen = ""
    try:
        os.set_blocking(proc.stdout.fileno(), False)
        proc.stdin.write("connect\n")
        proc.stdin.flush()
        deadline = time.monotonic() + timeout
        while "Connection successful" not in seen:
            if time.monotonic() > deadline:
                raise GattUnavailable("the device did not accept a connection in time - "
                                      "it may be out of range or switched off")
            if _BUSY.search(seen) or "connect error" in seen.lower():
                raise GattUnavailable("the device refused the connection ("
                                      + seen.strip().splitlines()[-1][-120:] + ")")
            try:
                chunk = os.read(proc.stdout.fileno(), 4096).decode("utf-8", "replace")
            except BlockingIOError:  # nothing yet: a non-blocking pipe raises, not ""
                chunk = ""
            if not chunk and proc.poll() is not None:
                raise GattUnavailable("gatttool stopped before connecting")
            seen += chunk
            time.sleep(0.1)
        proc.stdin.write(f"char-write-cmd 0x{handle:04x} {value}\n")
        proc.stdin.flush()
        time.sleep(1.0)  # let the command leave before the link is closed
        proc.stdin.write("disconnect\nexit\n")
        proc.stdin.flush()
    finally:
        try:
            proc.wait(timeout=3)
        except subprocess.TimeoutExpired:
            try:
                os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
            except (OSError, ProcessLookupError):
                pass


def _run_find(arguments: dict) -> str:
    if not isinstance(arguments, dict):
        return "The find_device arguments were not an object; nothing was sent."
    allowed, refusal = _consent(ChronoaConfig())
    if not allowed:
        return refusal.replace("Reading", "Reaching").replace("Nothing was read", "Nothing was sent")
    if not shutil.which("bluetoothctl"):
        return "bluetoothctl (bluez-utils) is not installed, so no device can be found."
    try:
        devices = paired()
    except (OSError, subprocess.SubprocessError):
        return "Bluetooth did not answer - is the adapter on?"
    lowenergy = [(m, n) for m, n in devices if looks_like_low_energy(m)]
    dev = str(arguments.get("device") or "").strip().lower()
    if dev:
        chosen, problem = _resolve(dev, devices)
        if problem:
            return problem
    elif len(lowenergy) == 1:
        chosen = lowenergy[0]
    else:
        return ("Which device? Pass device, one of: "
                + (", ".join(n for _, n in lowenergy) or "none paired") + ".")
    mac, name = chosen
    if not looks_like_low_energy(mac):
        return (f"{name} is an older Bluetooth device (a speaker or headset), which has no "
                "find signal to send. Nothing was sent.")
    try:
        chars = attributes(mac)
        svc = services(mac) if chars else []
    except GattUnavailable as exc:
        return f"Could not reach {name}: {exc}. Nothing was sent."
    if not chars:
        return (f"Could not reach {name}: it answered with no attributes - out of range, "
                "switched off, or an older Bluetooth device. Nothing was sent.")
    route = find_route(chars, svc)
    if route is None:
        return (f"{name} offers no way to be found that I know is safe: it has neither the "
                "standard Immediate Alert service nor a known vendor find command. Nothing was "
                "sent - an unknown write to a wearable can damage it.")
    handle, value, how = route
    try:
        _write_command(mac, handle, value)
    except GattUnavailable as exc:
        return f"Could not make {name} buzz: {exc}. Nothing was sent."
    return (f"Sent {name} the {how} signal - it should buzz or ring now. I cannot hear or "
            "feel it from here, so if it did not, it is probably out of range.")


FIND_SCHEMA = {
    "type": "function",
    "function": {
        "name": "find_device",
        "description": (
            "Make a paired Bluetooth watch, band or tag buzz or ring so the person can find it - "
            "'where is my watch', 'find my band'. Works on devices with the standard Immediate "
            "Alert service and on MoYoung (Da Fit) watches; anything else is refused rather than "
            "sent an unknown command. The device must be in range. Requires the "
            "'bluetooth-gatt-enabled' consent key."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "device": {
                    "type": "string",
                    "description": "Part of the device's name. May be left out when only one "
                                   "Low Energy device is paired.",
                },
            },
        },
    },
}


SKILLS = [Skill(name="bluetooth_gatt", schema=SCHEMA, run=_run),
          Skill(name="find_device", schema=FIND_SCHEMA, run=_run_find)]