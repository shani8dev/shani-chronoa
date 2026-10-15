"""adb pairing by USB and Wi-Fi, against `tools/fake_adb.py` first on PATH."""
import json
import os
import pathlib
import sys

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "usr" / "lib" / "shani-chronoa"))

from shani_chronoa import adb_pair  # noqa: E402


@pytest.fixture
def phone(tmp_path, monkeypatch):
    bindir = tmp_path / "bin"
    bindir.mkdir()
    (bindir / "adb").symlink_to(ROOT / "tools" / "fake_adb.py")
    state = tmp_path / "adb.json"
    monkeypatch.setenv("PATH", f"{bindir}{os.pathsep}{os.environ['PATH']}")
    monkeypatch.setenv("FAKE_ADB_STATE", str(state))

    class Phone:
        def set(self, **values):
            data = self.read()
            data.update(values)
            state.write_text(json.dumps(data))

        def read(self):
            return json.loads(state.read_text()) if state.exists() else {}

    p = Phone()
    p.set(devices=[], mdns=[], code="123456", connect="192.168.1.20:41000")
    return p


def test_usb_steps_follow_the_phone(phone):
    assert adb_pair.devices() == []
    phone.set(devices=[["R5CT10", "unauthorized", "usb:1-1 transport_id:3"]])
    (d,) = adb_pair.devices()
    assert d["state"] == "unauthorized" and d["via"] == "usb"
    assert "Allow" in adb_pair.STATE_STEP[d["state"]]
    phone.set(devices=[["R5CT10", "device", "usb:1-1 product:p model:Pixel_8 device:d"]])
    (d,) = adb_pair.devices()
    assert d["state"] == "device" and d["model"] == "Pixel 8"


def test_pairing_code_then_connect(phone):
    phone.set(mdns=[["adb-R5CT10-abc", "pairing", "192.168.1.20:37099"],
                    ["adb-R5CT10-abc", "connect", "192.168.1.20:41000"]])
    services = adb_pair.mdns_services()
    assert {s["kind"] for s in services} == {"pairing", "connect"}
    ok, why = adb_pair.pair("192.168.1.20:37099", "000000")
    assert not ok and "did not match" in why
    ok, why = adb_pair.pair("192.168.1.20:37099", "123 456")
    assert ok, why
    ok, why = adb_pair.connect_after_pairing("192.168.1.20:37099", timeout=2)
    assert ok, why
    (d,) = adb_pair.devices()
    assert d["via"] == "wifi" and d["state"] == "device"


def test_qr_pairing_finds_the_phone_that_scanned(phone):
    name, password, text = adb_pair.qr_secret()
    assert text == f"WIFI:T:ADB;S:{name};P:{password};;"
    assert password.isdigit() and len(password) == 8
    phone.set(code=password, mdns=[["someone-else", "pairing", "192.168.1.9:1111"],
                                   [name, "pairing", "192.168.1.20:37099"]])
    address = adb_pair.wait_for_pairing_service(name, timeout=2)
    assert address == "192.168.1.20:37099", "picked the wrong phone's pairing service"
    assert adb_pair.pair(address, password)[0]


def test_nobody_scanning_times_out(phone):
    name, _pw, _text = adb_pair.qr_secret()
    assert adb_pair.wait_for_pairing_service(name, timeout=1.5) is None


def test_bad_input_is_a_sentence_not_an_adb_call(phone):
    assert "not an address" in adb_pair.pair("pixel", "123456")[1]
    assert "digits" in adb_pair.pair("1.2.3.4:5", "abc")[1]
    assert not any(c[0] == "pair" for c in phone.read().get("calls", []))


def test_connect_without_pairing_is_refused(phone):
    ok, why = adb_pair.connect_after_pairing("192.168.1.20:37099", timeout=1.5)
    assert not ok and "Wireless debugging" in why


def test_qr_png_when_qrencode_exists():
    import shutil
    png = adb_pair.qr_png("WIFI:T:ADB;S:x;P:1;;")
    if shutil.which("qrencode"):
        assert png and png[:8] == b"\x89PNG\r\n\x1a\n"
    else:
        assert png is None


def test_no_adb_is_a_sentence(monkeypatch, tmp_path):
    monkeypatch.setenv("PATH", str(tmp_path))
    assert not adb_pair.available()
    assert adb_pair.devices() == []
    assert "not installed" in adb_pair.connect("1.2.3.4:5")[1]
