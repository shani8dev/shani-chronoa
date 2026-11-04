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






