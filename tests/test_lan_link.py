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


class TestIdentityToTheSpec:
    def test_the_field_order_is_the_official_one(self):
        """`cosmic-ext-connect-core/Protocol.md` records the order as the one
        that "should be preserved for compatibility", and a peer may fingerprint
        the packet. A dict built in a different order is a different byte
        sequence even with identical content.
        """
        packet = lan_link.identity_packet(name="Box", tcp_port=1716)
        assert list(packet["body"]) == [
            "deviceId", "deviceName", "protocolVersion", "deviceType",
            "tcpPort", "incomingCapabilities", "outgoingCapabilities"]

    def test_a_device_id_is_in_the_specs_pattern(self):
        identifier = lan_link.device_id()
        assert lan_link.DEVICE_ID.match(identifier), (
            f"{identifier!r} does not match /^[a-zA-Z0-9_-]{{32,38}}$/")
        assert 32 <= len(identifier) <= 38

    def test_the_id_is_stable_between_calls(self):
        """A phone pairs *to* this string. A new uuid4 each call would
        un-pair the computer from itself every run."""
        assert lan_link.device_id() == lan_link.device_id()

    def test_a_target_turns_the_packet_into_a_reply(self):
        targeted = lan_link.identity_packet(name="Box", target=PEER_ID)
        assert targeted["body"]["targetDeviceId"] == PEER_ID
        assert "targetDeviceId" not in lan_link.identity_packet(name="Box")["body"]


class TestParseIdentity:
    def test_a_well_formed_peer_is_read(self):
        peer = lan_link.parse_identity(json.dumps(_peer_identity()).encode())
        assert peer["deviceName"] == "Pixel 8"
        assert peer["deviceType"] == "phone"
        assert peer["tcpPort"] == 1716
        assert "kdeconnect.battery" in peer["incomingCapabilities"]

    @pytest.mark.parametrize("datagram,reason", [
        (b"not json at all", "not JSON"),
        (b"{}", "no type"),
        # **A valid body under the wrong type.** The first version of this
        # fixture was `{"type": "kdeconnect.other"}` with no body, which the
        # missing-body check already refused - so dropping the type check left
        # this test green. The mutation only discriminates when the body is
        # otherwise perfect.
        (json.dumps({"type": "kdeconnect.other",
                     "body": _peer_identity()["body"]}).encode(),
         "a perfect body under a different packet type"),
        (b'{"type": "kdeconnect.identity"}', "no body"),
        # **Same fault the other way**: this one had no protocolVersion, so the
        # version check refused it and the deviceId pattern never mattered.
        (json.dumps({"type": "kdeconnect.identity",
                     "body": {"deviceId": "tooshort", "protocolVersion": 8}}).encode(),
         "a deviceId outside the spec's pattern"),
        (json.dumps({"type": "kdeconnect.identity",
                     "body": {"deviceId": PEER_ID, "protocolVersion": "eight"}}).encode(),
         "a protocolVersion that is not a number"),
    ])
    def test_anything_that_is_not_an_identity_is_none(self, datagram, reason):
        """**Each is a different reason, and each is ordinary noise.** UDP 1716
        is unprivileged and anything may be on it, so a malformed datagram is
        not an error - it is a reason, and none of them may be read as a peer.
        """
        assert lan_link.parse_identity(datagram) is None, (
            f"{reason} was accepted as a device")

    def test_the_control_is_not_vacuous(self):
        """A parser that returns None for everything would pass all of the
        above. This is the test that proves it can say yes.
        """
        assert lan_link.parse_identity(json.dumps(_peer_identity()).encode())


class TestARealPeerIsFound:
    def test_a_broadcasting_peer_is_discovered(self, free_port):
        seen = []

        def listen():
            seen.extend(lan_link.listen_once(3.0))

        thread = threading.Thread(target=listen, daemon=True)
        thread.start()
        time.sleep(0.4)
        assert _broadcast(_peer_identity()), "the fake peer could not send"
        thread.join(timeout=6)

        assert seen, "a peer broadcasting a spec-shaped identity was not found"
        peer = seen[0]
        assert peer["deviceId"] == PEER_ID
        assert peer["deviceName"] == "Pixel 8"
        assert peer["deviceType"] == "phone"
        assert peer["address"].startswith("127.")

    def test_our_own_broadcast_is_filtered_out(self, free_port):
        """A machine that lists itself as a phone is not an answer - and without
        this, `devices` would report this computer whenever `announce` had run.
        """
        seen = []
        thread = threading.Thread(
            target=lambda: seen.extend(lan_link.listen_once(2.5)), daemon=True)
        thread.start()
        time.sleep(0.4)
        own = lan_link.identity_packet(name=_this_name())
        assert _broadcast(own)
        thread.join(timeout=5)
        assert not [p for p in seen if p.get("deviceId") == own["body"]["deviceId"]], (
            "our own identity came back as a discovered device")

    def test_noise_on_the_port_is_not_a_device(self, free_port):
        seen = []
        thread = threading.Thread(
            target=lambda: seen.extend(lan_link.listen_once(2.0)), daemon=True)
        thread.start()
        time.sleep(0.4)
        assert _broadcast({"type": "kdeconnect.other", "body": {"x": 1}})
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as noise:
            noise.sendto(b"garbage not json", ("127.0.0.1", lan_link.UDP_PORT))
        thread.join(timeout=4)
        assert seen == [], f"port noise was reported as devices: {seen}"


def _this_name():
    import socket as _s
    try:
        return _s.gethostname()
    except OSError:
        return "this computer"


class TestPairingIsHonestlyAbsent:
    def test_it_says_so_rather_than_claiming_to_pair(self):
        state, why = lan_link.pairing_status()
        assert state == "not implemented"
        assert "spec" in why and "never run" in why, (
            f"the refusal must say why it is absent, not just that it is: {why!r}")

    def test_the_skill_answers_pair_with_the_same_truth(self):
        from shani_chronoa.skills import lan_phone
        config = type("C", (), {"get_bool": lambda s, k, d=False: True})()
        answer = lan_phone._run({"action": "pair", "_config": config})
        assert "not implemented" in answer or "not written" in answer
        assert "devices" in answer, "the refusal must point at what *does* work"


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
        assert "not implemented" in out or "not written" in out
