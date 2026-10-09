"""MoYoung watch protocol: framing, reassembly and every decoder.

Byte layouts are Gadgetbridge's (MoyoungPacketOut/In, MoyoungDeviceSupport,
FetchDataOperation); each fixture is built field by field so a test cannot
pass by sharing a mistake with the decoder.
"""

import struct
import sys
from datetime import datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "usr" / "lib" / "shani-chronoa"))

from shani_chronoa import moyoung as m  # noqa: E402


def test_find_my_watch_frame_is_the_bytes_that_made_the_watch_vibrate():
    assert m.frame(97).hex() == "feea100561"


def test_query_frames_match_gadgetbridge():
    assert m.frame(m.CMD_SYNC_PAST, bytes([1])).hex() == "feea10063301"
    assert m.frame(m.CMD_ADVANCED_QUERY, bytes([0x11, 3, 0])).hex() == "feea1008b9110300"
    big = m.frame(0x10, bytes(300))
    assert big[2] == 0x21 and big[3] == (305 & 0xFF) and m.packet_length(big) == 305


def test_a_reply_split_over_notifications_is_reassembled():
    packet = m.frame(m.CMD_SYNC_SLEEP, bytes(range(30)))
    r = m.Reassembler()
    assert r.put(packet[:20]) is None
    assert r.put(packet[20:]) == (m.CMD_SYNC_SLEEP, bytes(range(30)))
    assert r.put(b"\x00\x01garbage") is None and r.want == -1


def test_steps_are_three_little_endian_uint24():
    data = (12345).to_bytes(3, "little") + (9876).to_bytes(3, "little") + (432).to_bytes(3, "little")
    assert m.decode_steps(data) == {"steps": 12345, "distance_m": 9876, "calories": 432}
    assert m.decode_steps(bytes(8)) is None


def test_sleep_triples_become_stages_with_durations_and_evening_is_the_day_before():
    day = datetime(2026, 10, 8, 9, 0)
    data = bytes([1, 23, 10,  2, 1, 0,  1, 3, 30,  0, 6, 45])
    seg = m.decode_sleep(data, day)
    assert [s["stage"] for s in seg] == ["light", "deep", "light", "awake"]
    assert seg[0]["start"] == datetime(2026, 10, 7, 23, 10)
    assert seg[0]["minutes"] == 110 and seg[1]["minutes"] == 150 and seg[2]["minutes"] == 195
    summary = m.sleep_summary(seg)
    assert summary["asleep_minutes"] == 455 and summary["deep_minutes"] == 150


def test_training_v1_record():
    start = int(datetime(2026, 10, 8, 7, 0, tzinfo=m.WATCH_TZ).timestamp())
    rec = struct.pack("<IIhBBIIh", start, start + 1800, 1700, 0, 1, 4200, 3100, 250) + bytes(2)  # 22 bytes of fields, 24 per record
    assert len(rec) == 24
    got = m.decode_training(rec + bytes(24))
    assert len(got) == 1
    t = got[0]
    assert t["type"] == "run" and t["steps"] == 4200 and t["distance_m"] == 3100 and t["calories"] == 250
    assert t["start"] == datetime(2026, 10, 8, 7, 0) and t["active_seconds"] == 1700


def test_training_v2_record_carries_average_heart_rate():
    start = int(datetime(2026, 10, 8, 18, 0, tzinfo=m.WATCH_TZ).timestamp())
    rec = bytes([0, 1]) + struct.pack("<IIhBBIII", start, start + 600, 600, 128, 0, 900, 700, 60)
    assert len(rec) == 26
    t = m.decode_training(rec)[0]
    assert t["type"] == "walk" and t["avg_hr"] == 128 and t["calories"] == 60


def test_stress_slots_are_half_hours_from_midnight_and_zero_is_no_reading():
    day = datetime.now().replace(hour=0, minute=0) - timedelta(days=1)
    slots = bytes(26)
    slots = slots[:2] + bytes([40]) + slots[3:5] + bytes([55]) + slots[6:]
    got = m.decode_stress_day(bytes([0x11, 0x03, 0x01]) + slots, day)
    assert [(w.hour, w.minute, v) for w, v in got] == [(1, 0, 40), (2, 30, 55)]
    assert m.decode_stress_day(bytes([0x11, 0x00, 5]), day) == []


def test_measurements_send_start_and_stop_payloads_from_gadgetbridge():
    assert m.MEASUREMENTS["heart_rate"][:2] == (109, b"\x00")
    assert m.MEASUREMENTS["blood_oxygen"][:2] == (107, b"\x00")
    assert m.MEASUREMENTS["blood_pressure"] == (105, b"\x00\x00\x00", b"\xff\xff\xff")


def test_the_characteristic_list_is_read_in_both_gatttool_formats():
    assert m._CHAR.findall("handle: 0x0044, char properties: 0x04, char value handle: 0x0045, "
                           "uuid: 0000fee2-0000-1000-8000-00805f9b34fb") == [("0x0045", "fee2")]
    assert m._CHAR.findall("handle = 0x0046, char properties = 0x10, char value handle = 0x0047, "
                           "uuid = 0000fee3-0000-1000-8000-00805f9b34fb") == [("0x0047", "fee3")]


def test_an_empty_night_is_no_sleep_not_awake_at_midnight():
    """The watch's real reply for a night with nothing recorded: 03 00 00 00."""
    assert m.decode_sleep(bytes(3), datetime(2026, 10, 8)) == []


def test_stress_reads_the_whole_day_the_watch_sends():
    """The real reply was 51 bytes: 3 header bytes and 48 half-hour slots."""
    day = datetime.now().replace(hour=0, minute=0) - timedelta(days=1)
    slots = bytearray(48)
    slots[44] = 30                               # 22:00
    got = m.decode_stress_day(bytes([0x11, 0x03, 0x01]) + bytes(slots), day)
    assert [(w.hour, w.minute, v) for w, v in got] == [(22, 0, 30)]


def test_the_watch_skill_asks_nothing_while_its_switch_is_off(monkeypatch):
    from shani_chronoa.skills import watch as skill
    import pytest

    class Off:
        def get_bool(self, key, default=False):
            return False
    monkeypatch.setattr(skill, "ChronoaConfig", Off)
    monkeypatch.setattr(m, "Watch", lambda *a, **k: pytest.fail("the watch was reached with the switch off"))
    out = skill._run({"action": "settings"})
    assert "bluetooth-gatt-enabled" in out and "Nothing was asked" in out


def test_settings_encode_the_way_the_watch_reads_them():
    assert m._encode_setting("int", None, 10001) == bytes.fromhex("00002711")       # set: big-endian
    assert m._decode_setting("int", None, bytes.fromhex("10270000")) == 10000      # read: little-endian (measured)
    assert m._encode_setting("enum", {"12h": 0, "24h": 1}, "24h") == b"\x01"
    assert m.decode_dnd(bytes.fromhex("78050f00")) == {"start": "23:20", "end": "00:15", "on": True}
    assert m.message_payload("sms", "A:B", "hi") == b"\x01A;B:hi"
    assert m.time_payload(datetime(2026, 10, 8, 20, 49))[-1] == 8


def test_weather_packets_have_gadgetbridges_layout():
    cur = {"temperature_2m": 27.6, "weather_code": 61}
    daily = {"temperature_2m_max": [31, 30, 29], "temperature_2m_min": [24, 23, -2],
             "weather_code": [61, 0, 71], "sunrise": ["2026-10-09T06:12"], "sunset": ["2026-10-09T18:05"]}
    pk = dict(m.weather_packets(cur, daily, "Pune", datetime(2026, 10, 8, 21, 20)))
    assert len(pk[m.CMD_SET_WEATHER_TODAY]) == 19
    assert pk[m.CMD_SET_WEATHER_TODAY][:3] == bytes([0, m.W_RAINY, 28])
    assert pk[m.CMD_SET_WEATHER_TODAY][-8:] == "Pune".encode("utf-16-be")
    assert pk[m.CMD_SET_SUNRISE_SUNSET][:9] == bytes([0, m.W_RAINY, 28, 0, 0, 6, 12, 18, 5])
    assert pk[m.CMD_SET_WEATHER_LOCATION] == b"21:20 Pune"
    fut = pk[m.CMD_SET_WEATHER_FUTURE]
    assert len(fut) == 24 and fut[3:6] == bytes([m.W_SUNNY, 30, 23]) and fut[6:9] == bytes([m.W_SNOWY, 29, 254])
    assert fut[9:12] == bytes([m.W_HAZE, 156, 156])           # no forecast: Gadgetbridge's -100


def test_wmo_codes_map_onto_the_watchs_icons():
    assert [m.wmo_icon(c) for c in (0, 2, 3, 45, 61, 73, 95, None)] == [
        m.W_SUNNY, m.W_OVERCAST, m.W_CLOUDY, m.W_FOGGY, m.W_RAINY, m.W_SNOWY, m.W_RAINY, m.W_HAZE]


def test_the_sunrise_packet_location_is_bounded_or_the_watch_restarts():
    """Measured 2026-10-08 on an FB BGS002: 0xB5 with a long place name reboots it.

    The command is byte-for-byte Gadgetbridge's, so this is invisible from the
    protocol alone - what differs is the *content*. Da Fit sends the short name a
    person typed; `weather._here()` returns the geocoder's full locality
    ("Sangli, Maharashtra, India", 24 chars, 35-byte payload) and the watch
    restarts. 9 chars already does it; 6 survives.
    """
    from datetime import datetime

    from shani_chronoa import moyoung

    current = {"temperature_2m": 26.2, "weather_code": 0}
    daily = {"temperature_2m_max": [28], "temperature_2m_min": [22],
             "weather_code": [0, 0, 0, 0, 0, 0, 0, 0],
             "sunrise": ["2026-10-08T06:12", "x"] * 8, "sunset": ["2026-10-08T18:24", "x"] * 8}

    def sunrise_payload(label):
        for cmd, payload in moyoung.weather_packets(current, daily, label, datetime(2026, 10, 8, 12, 0)):
            if cmd == moyoung.CMD_SET_SUNRISE_SUNSET:
                return payload
        raise AssertionError("no sunrise/sunset packet was produced")

    geocoded = "Sangli, Maharashtra, India"
    payload = sunrise_payload(geocoded)
    assert len(payload) <= 9 + moyoung.WEATHER_LABEL_CHARS, (
        f"the label was not bounded: {geocoded!r} produced a {len(payload)}-byte packet")
    assert geocoded[:6].encode() in payload, "the place is not in the packet at all"
    assert b"India" not in payload, "the unbounded tail is still on the wire"
    # And the short name Da Fit itself would send is unchanged by the bound.
    assert sunrise_payload("Sangli") == sunrise_payload("Sangli, Maharashtra, India")


# --- the bluez transport (no gatttool: Arch ships none) -----------------------

def _bluez_watch():
    from gi.repository import GLib
    w = m.BluezWatch.__new__(m.BluezWatch)
    m.Watch.__init__(w, "B3:69:73:62:B2:B6")
    w._GLib, w.device, w._connected = GLib, "/org/bluez/hci0/dev_B3_69_73_62_B2_B6", True
    w.paths = {m.OUT: w.device + "/service0040/char0044", m.IN: w.device + "/service0040/char0046",
               m.HR_MEASUREMENT: w.device + "/service0020/char0021"}
    w.calls = []
    w._call = lambda path, iface, method, args=None, timeout=10.0: w.calls.append(
        (path, iface, method, args.unpack() if args is not None else None))
    return w


def _changed(w, path, iface, props):
    from gi.repository import GLib
    w._on_props(None, None, path, None, None,
                GLib.Variant("(sa{sv}as)", (iface, props, [])))


def test_bluez_writes_commands_without_response_to_fee2():
    w = _bluez_watch()
    w._write(m.frame(97))
    path, iface, method, args = w.calls[0]
    assert (path, iface, method) == (w.paths[m.OUT], "org.bluez.GattCharacteristic1", "WriteValue")
    assert bytes(args[0]) == bytes.fromhex("feea100561") and args[1] == {"type": "command"}


def test_bluez_notifications_reach_the_inbox_and_the_heart_rate():
    from gi.repository import GLib
    w = _bluez_watch()
    reply = m.frame(38, (10000).to_bytes(4, "little"))
    _changed(w, w.paths[m.IN], "org.bluez.GattCharacteristic1", {"Value": GLib.Variant("ay", list(reply))})
    assert w.inbox == [(38, (10000).to_bytes(4, "little"))]
    _changed(w, w.paths[m.HR_MEASUREMENT], "org.bluez.GattCharacteristic1",
             {"Value": GLib.Variant("ay", [0x06, 72])})          # flags: contact detected, uint8 bpm
    assert w.heart_rates == [72]


def test_bluez_link_loss_is_seen_so_the_companion_reconnects():
    from gi.repository import GLib
    w = _bluez_watch()
    assert w.alive()
    _changed(w, w.device, "org.bluez.Device1", {"Connected": GLib.Variant("b", False)})
    assert not w.alive()
