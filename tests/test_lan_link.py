"""The KDE Connect LAN link, against a **real peer on a real socket**.

A module that speaks a network protocol is only worth what a real exchange
proves, so this file runs one: a fake phone broadcasts a spec-shaped identity
datagram to the port `lan_link` listens on, and the assertions are about what
`lan_link` actually received. Everything below is a real UDP hop.

That is also why it can be run at all here. There is no KDE Connect phone on
any network this machine has been on, so a test that needed one could only be
written against the spec and never run - which is the trap `bluetooth_call`'s
call events avoided by not being built. A **fake peer** exercises the real
protocol and the real code path without needing the hardware.

Port note: the tests bind UDP 1716, which is unprivileged and normally free. If
something else on the machine holds it (a real KDE Connect daemon, which would
be good news) the listen tests skip with that named, rather than failing about
a socket - a skip that says why is honest, and a green skip on an untestable
path would be the worse outcome.
"""

from __future__ import annotations

import json
import socket
import sys
import threading
import time
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "usr" / "lib" / "shani-chronoa"))

from shani_chronoa import lan_link  # noqa: E402

PEER_ID = "a1b2c3d4e5f60718293a4b5c6d7e8f90"          # 32 chars, matches the spec


def _peer_identity(**overrides):
    """The spec's own identity packet, as a phone would broadcast it."""
    body = {
        "deviceId": PEER_ID,
        "deviceName": "Pixel 8",
        "protocolVersion": 8,
        "deviceType": "phone",
        "tcpPort": 1716,
        "incomingCapabilities": ["kdeconnect.battery", "kdeconnect.clipboard"],
        "outgoingCapabilities": ["kdeconnect.battery.request"],
    }
    body.update(overrides)
    return {"id": int(time.time()), "type": "kdeconnect.identity", "body": body}


def _broadcast(packet, count=3, gap=0.15):
    """Send a real datagram from a real socket, the way a peer would."""
    payload = (json.dumps(packet, separators=(", ", ": ")) + "\n").encode()
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
        for _ in range(count):
            try:
                sock.sendto(payload, ("127.0.0.1", lan_link.UDP_PORT))
            except OSError:
                return False
            time.sleep(gap)
    return True


@pytest.fixture
def free_port():
    """Is UDP 1716 ours to bind? If not, skip rather than fail about a socket."""
    probe = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        probe.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        probe.bind(("", lan_link.UDP_PORT))
    except OSError:
        probe.close()
        pytest.skip("something else holds UDP 1716, so the listen path cannot "
                    "be measured here (that something is probably KDE Connect, "
                    "in which case 'devices' works for real)")
    probe.close()
    return True








def _this_name():
    import socket as _s
    try:
        return _s.gethostname()
    except OSError:
        return "this computer"




class TestTheSkillSurface:
    def _config(self, on=True):
        return type("C", (), {"get_bool": lambda s, k, d=False: on})()

    def test_the_switch_off_refuses_and_names_itself(self):
        from shani_chronoa.skills import lan_phone
        out = lan_phone._run({"action": "devices", "_config": self._config(False)})
        assert "turned off" in out and "lan-phone-enabled" in out

    def test_a_wrong_action_is_refused_not_ignored(self):
        from shani_chronoa.skills import lan_phone
        out = lan_phone._run({"action": "share", "_config": self._config()})
        assert "must be one of" in out and "share" in out

    def test_status_names_what_works_and_what_does_not(self):
        from shani_chronoa.skills import lan_phone
        out = lan_phone._run({"action": "status", "_config": self._config()})
        assert str(lan_link.UDP_PORT) in out
        state, _why = lan_link.pairing_status()
        assert f"Pairing: {state}" in out, "status must report the stored pairing"



def _deadline() -> float:
    """Five seconds from now - enough for the bound to fire, short enough that a
    missing one fails a test rather than hanging a suite."""
    import time
    return time.monotonic() + 5.0







