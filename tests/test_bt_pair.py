"""Pairing through BlueZ's D-Bus API, against `tools/fake_bluez.py` on a private bus.

The fake answers the real methods and calls back into the agent `bt_pair`
registers, so every path here is the real agent round-trip: a code to compare,
a passkey to type on the device, a PIN to enter, a device that refuses to bond
(a GATT wearable, connected instead), one that is out of reach. No test touches
the machine's Bluetooth: the client is given the private bus's address.
"""
import pathlib
import sys
import time

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "usr" / "lib" / "shani-chronoa"))
sys.path.insert(0, str(ROOT / "tools"))

from gi.repository import Gio, GLib  # noqa: E402

from shani_chronoa import bt_pair  # noqa: E402
import fake_bluez  # noqa: E402


@pytest.fixture
def bluez():
    bus = Gio.TestDBus.new(Gio.TestDBusFlags.NONE)
    bus.up()
    fake = fake_bluez.FakeBluez(bus.get_bus_address()).start()
    yield fake
    fake.stop()
    bus.down()


def _spin(until, seconds=5.0):
    ctx = GLib.MainContext.default()
    deadline = time.monotonic() + seconds
    while not until() and time.monotonic() < deadline:
        ctx.iteration(False)
        time.sleep(0.005)
    return until()


def _open(bluez, on_prompt=None):
    pairing = bt_pair.Pairing(bluez.address, on_prompt=on_prompt)
    ok, why = pairing.open()
    assert ok, why
    pairing.start_discovery()
    assert _spin(lambda: len(pairing.nearby()) == len(fake_bluez.DEVICES))
    return pairing


def _by(pairing, name):
    return next(d for d in pairing.nearby() if d.name == name)


def _pair(pairing, name, connect_only=False):
    result = []
    device = _by(pairing, name)
    (pairing.connect_only if connect_only else pairing.pair)(
        device.path, lambda ok, why: result.append((ok, why)))
    assert _spin(lambda: result, 10), f"{name}: no answer"
    return result[0]


def test_the_agent_is_ours_and_never_the_default(bluez):
    pairing = _open(bluez)
    assert bluez.agent and bluez.agent[1] == bt_pair.AGENT_PATH
    assert not any("RequestDefaultAgent" in c for c in bluez.calls)
    pairing.close()
    assert _spin(lambda: bluez.agent is None), "closing left the agent registered"


def test_nearby_is_sorted_and_classified(bluez):
    pairing = _open(bluez)
    devices = pairing.nearby()
    assert devices[0].name == "Pixel 9", "the strongest signal is not first"
    kinds = {d.name: d.kind for d in devices}
    assert kinds["Pixel 9"] == "phone" and kinds["WH-1000XM5"] == "audio"
    assert kinds["K380 Keyboard"] == "input" and kinds["Band 8"] == "watch"
    les = {d.name for d in devices if d.le}
    assert les == {"Band 8"}, les
    pairing.close()


def test_a_code_is_compared_by_a_person(bluez):
    prompts = []
    pairing = _open(bluez, on_prompt=prompts.append)
    result = []
    pairing.pair(_by(pairing, "Pixel 9").path, lambda ok, why: result.append((ok, why)))
    assert _spin(lambda: prompts)
    assert prompts[0].kind == "confirm" and prompts[0].code == "123456"
    assert prompts[0].device == "Pixel 9"
    _spin(lambda: result, 0.3)
    assert not result, "pairing finished before anybody confirmed the code"
    prompts[0].answer(True)
    assert _spin(lambda: result, 5)
    assert result[0] == (True, "paired and connected")
    dev = pairing.device(_by_path(bluez, "Pixel 9"))
    assert dev.paired and dev.connected
    assert bluez.objects[_by_path(bluez, "Pixel 9")]["Trusted"].unpack() is True
    pairing.close()


def _by_path(bluez, name):
    return fake_bluez._path(fake_bluez.DEVICES[name][0])


def test_declining_the_code_does_not_pair(bluez):
    pairing = _open(bluez, on_prompt=lambda p: p.answer(None))
    ok, why = _pair(pairing, "Pixel 9")
    assert not ok and why == "pairing was declined"
    assert not pairing.device(_by_path(bluez, "Pixel 9")).paired
    pairing.close()


def test_a_passkey_to_type_on_the_keyboard_is_shown(bluez):
    prompts = []
    pairing = _open(bluez, on_prompt=prompts.append)
    ok, _ = _pair(pairing, "K380 Keyboard")
    assert ok
    assert [(p.kind, p.code) for p in prompts] == [("show-passkey", "654321")]
    pairing.close()


def test_a_pin_is_entered(bluez):
    pairing = _open(bluez, on_prompt=lambda p: p.answer("0000") if p.kind == "enter-pin" else None)
    assert _pair(pairing, "Car kit")[0]
    pairing.close()


def test_just_works_needs_nobody(bluez):
    prompts = []
    pairing = _open(bluez, on_prompt=prompts.append)
    assert _pair(pairing, "WH-1000XM5") == (True, "paired and connected")
    assert prompts == []
    pairing.close()


def test_a_gatt_wearable_that_will_not_bond_is_connected_instead(bluez):
    pairing = _open(bluez)
    ok, why = _pair(pairing, "Band 8")
    assert not ok and "did not match" in why
    ok, why = _pair(pairing, "Band 8", connect_only=True)
    assert ok and why == "connected", why
    props = bluez.objects[_by_path(bluez, "Band 8")]
    assert props["Connected"].unpack() and props["Trusted"].unpack()
    assert not props["Paired"].unpack()
    assert "Band 8" not in {d.name for d in pairing.nearby()}, \
        "a connected, trusted wearable is still offered as new"
    pairing.close()


def test_out_of_reach_is_a_sentence(bluez):
    pairing = _open(bluez)
    ok, why = _pair(pairing, "Broken")
    assert not ok and "pairing mode" in why
    pairing.close()


def test_power_off_is_reported_and_can_be_fixed(bluez):
    bluez.powered = False
    pairing = bt_pair.Pairing(bluez.address)
    assert pairing.open()[0]
    assert not pairing.powered
    done = []
    pairing.power_on(lambda ok, why: done.append(ok))
    assert _spin(lambda: done) and done == [True]
    assert _spin(lambda: pairing.powered)
    pairing.close()


def test_no_bluez_is_a_sentence():
    bus = Gio.TestDBus.new(Gio.TestDBusFlags.NONE)
    bus.up()
    try:
        ok, why = bt_pair.Pairing(bus.get_bus_address()).open()
    finally:
        bus.down()
    assert not ok and "bluetoothd" in why


def test_the_gatt_skill_lists_trusted_devices(monkeypatch):
    from shani_chronoa.skills import bluetooth_gatt as gatt

    out = {"Paired": "Device AA:00:00:00:00:01 Pixel 9\n",
           "Trusted": "Device AA:00:00:00:00:01 Pixel 9\nDevice C1:00:00:00:00:05 Band 8\n"}

    class P:
        def __init__(self, s):
            self.stdout = s

    monkeypatch.setattr(gatt.subprocess, "run", lambda argv, **k: P(out[argv[-1]]))
    assert gatt.paired() == [("AA:00:00:00:00:01", "Pixel 9"), ("C1:00:00:00:00:05", "Band 8")]
