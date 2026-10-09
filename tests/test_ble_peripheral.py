"""Chronoa Remote: the HID reports, the GATT tree, and the gates (no Bluetooth needed).

Measured live 2026-10-09 with an Android phone: it paired over LE, subscribed to
both input reports, and took volume up/down and typed text.
"""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "usr" / "lib" / "shani-chronoa"))
pytest.importorskip("gi")

from shani_chronoa import ble_peripheral as bp  # noqa: E402


def test_typing_sends_press_then_release_with_shift_for_capitals():
    reports, missing = bp.key_reports("Hi!")
    assert reports == [bytes([0x02, 0, 0x0B, 0, 0, 0, 0, 0]), bytes(8),       # H = shift + h
                       bytes([0x00, 0, 0x0C, 0, 0, 0, 0, 0]), bytes(8),       # i
                       bytes([0x02, 0, 0x1E, 0, 0, 0, 0, 0]), bytes(8)]       # ! = shift + 1
    assert missing == ""


def test_digits_map_to_the_number_row_and_zero_comes_last():
    reports, _ = bp.key_reports("109")
    assert [r[2] for r in reports[::2]] == [0x1E, 0x27, 0x26]


def test_what_a_us_keyboard_cannot_type_is_reported_not_dropped():
    _, missing = bp.key_reports("café ₹")
    assert missing == "é₹"


def test_media_keys_are_a_usage_then_a_release():
    assert bp.consumer_reports("volume_up") == [b"\xe9\x00", b"\x00\x00"]
    assert bp.consumer_reports("play_pause") == [b"\xcd\x00", b"\x00\x00"]
    assert bp.CONSUMER["shutter"] == bp.CONSUMER["volume_up"]   # Android camera: volume up takes the photo


def test_report_map_collections_balance_and_both_report_ids_exist():
    m = bp.REPORT_MAP
    assert m.count(bytes([0xA1, 0x01])) == m.count(0xC0) == 2      # two application collections, both closed
    assert bytes([0x85, 0x01]) in m and bytes([0x85, 0x02]) in m


def test_the_tree_has_what_bluez_requires():
    p = bp.Peripheral()
    tree = p.managed_objects()
    services = {v["org.bluez.GattService1"]["UUID"].unpack() for v in tree.values() if "org.bluez.GattService1" in v}
    assert services == {bp.uuid16(s) for s in ("1812", "1802", "180f", "1805")}
    for path, ifaces in tree.items():
        assert path.startswith(bp.ROOT + "/")
        for iface, props in ifaces.items():
            if iface == "org.bluez.GattCharacteristic1":
                assert props["Service"].unpack() in tree and props["Flags"].unpack()
    refs = [p.objects[i] for i in range(len(p.objects)) if p.objects[i].props.get("UUID", ("", ""))[1] == bp.uuid16("2908")]
    assert sorted(r.value for r in refs) == [bytes([1, 1]), bytes([2, 1])]


def test_find_me_alert_rings_and_level_zero_stops(monkeypatch):
    calls = []

    class Cfg:
        def get_bool(self, k, d=False):
            return False
    svc = bp.RemoteService(Cfg())
    monkeypatch.setattr(svc.ringer, "start", lambda: calls.append("ring"))
    monkeypatch.setattr(svc.ringer, "stop", lambda: calls.append("stop"))
    p = bp.Peripheral(on_alert=svc.on_alert)
    alert = next(o for o in p.objects if o.props.get("UUID", ("", ""))[1] == bp.uuid16("2a06"))
    alert.write(b"\x02")
    alert.write(b"\x00")
    assert calls == ["ring", "stop"]


def test_the_skill_presses_nothing_while_its_switch_is_off(monkeypatch):
    from shani_chronoa.skills import phone_remote as skill

    class Off:
        def get_bool(self, key, default=False):
            return False
    monkeypatch.setattr(skill, "ChronoaConfig", Off)
    monkeypatch.setattr(bp, "ask", lambda *a, **k: pytest.fail("the remote was asked with the switch off"))
    assert "phone-remote-enabled" in skill._run({"action": "press", "key": "shutter"})
