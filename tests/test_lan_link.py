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


class TestPairingStatusIsTheStoredPairing:
    """It said "not implemented" while `pair` existed; now it reports the
    stored pairing (`tests/test_lan_pairing.py` drives the pairing itself)."""

    def test_not_paired_points_at_connect_a_device(self, tmp_path, monkeypatch):
        monkeypatch.setattr(lan_link, "_paired_file", lambda: tmp_path / "paired.json")
        state, why = lan_link.pairing_status()
        assert state == "not paired" and "Connect a device" in why

    def test_paired_names_the_phone(self, tmp_path, monkeypatch):
        monkeypatch.setattr(lan_link, "_paired_file", lambda: tmp_path / "paired.json")
        lan_link._remember_pairing({"deviceId": "f" * 32, "deviceName": "Pixel"},
                                   "10.0.0.5", 1716)
        state, why = lan_link.pairing_status()
        assert state == "paired" and "Pixel" in why and "10.0.0.5" in why


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

class TestTheReadBufferIsBounded:
    """**A peer that never sends a newline used to fill this until memory ran out.**

    `Link._lines` accumulates into `self._buffer` and partitions on `\\n`. A peer
    sending `{"a":1}` and no newline, forever, grew it without limit: nothing in
    the loop checked a size, because every packet the protocol defines is small
    and well-formed and the loop had only ever seen those.

    Maze-Connect's `FrameParser` makes the same point better - it checks a length
    prefix *before* buffering any payload - but that needs a prefix to check, and
    these frames are newline-delimited so there is nothing to inspect first. What
    carries over is the bound itself.
    """

    def _link(self, chunk=b"more bytes"):
        """A Link whose socket and TLS layer are already primed.

        Built by hand rather than connected: the point is the buffer bound, and a
        real socket would test the network instead.

        **`chunk` must be non-empty, and the first version of this fixture used
        `b""`** - which makes `_pump` raise "the peer closed the link" and the
        loop break *before* it ever reaches the size check. Two tests then
        "passed" by not exercising the bound at all, which is the
        can't-fail-control trap in its purest form: the assertion existed, the
        path to it did not.
        """
        link = lan_link.Link("127.0.0.1")
        link._buffer = "x" * (lan_link.MAX_LINE_BYTES + 10)

        class _Tls:
            def read(self):
                return b""

            def write(self, data):
                pass

        class _Raw:
            def settimeout(self, seconds):
                pass

            def recv(self, size):
                return chunk

        link._tls, link._raw = _Tls(), _Raw()
        link._outgoing = type("_B", (), {"pending": 0, "read": lambda s: b""})()
        link._incoming = type("_B", (), {"write": lambda s, d: None})()
        return link

    def test_an_endless_frame_with_no_boundary_is_refused(self):
        with pytest.raises(RuntimeError) as caught:
            # **A finite deadline, so a removed bound fails rather than hangs.**
            # With `1e9` the loop spins until memory or patience runs out, and a
            # mutation that times out CI reads as a broken suite rather than as
            # the break it is. With the bound present the raise comes on the
            # first iteration, so this never ends the loop.
            self._link()._lines(deadline=_deadline())
        assert "boundary" in str(caught.value)
        assert str(lan_link.MAX_LINE_BYTES) in str(caught.value), (
            "the refusal must name the limit, or a person reading it cannot tell "
            "whether the limit is theirs or the peer's")

    def test_it_refuses_rather_than_truncating(self):
        """Truncating would leave the buffer holding half of two things and the
        next parse reading a spliced-together packet - which is how a length or
        delimiter attack becomes an injection.
        """
        link = self._link()
        with pytest.raises(RuntimeError):
            link._lines(deadline=_deadline())

    def test_a_normal_packet_still_works(self):
        """**The control.** A bound that refuses ordinary traffic is worse than
        no bound, and a test that only feeds absurd input cannot see it."""
        link = self._link()
        # **A trailing newline, because that is what makes it a complete line.**
        # The first version of this test omitted it and set deadline=1e9, so the
        # read loop waited forever for a boundary that was never coming and the
        # test hung rather than failed - which looks like a slow test and reads as
        # a broken fixture, and is neither: it is a correct loop given a wrong
        # input.
        link._buffer = '{"type": "kdeconnect.ping", "body": {}}\n'
        assert link._lines(deadline=_deadline()) == [
            '{"type": "kdeconnect.ping", "body": {}}']
        assert link._buffer == "", "the consumed line was not dropped"

    def test_the_limit_is_a_limit_not_a_feeling(self):
        """The number is a declared constant, so a test can pin it and a reader
        can find why it is that number."""
        assert lan_link.MAX_LINE_BYTES == 4 * 1024 * 1024
        assert lan_link.MAX_LINE_BYTES > 1024 * 1024, (
            "smaller than 1 MiB would refuse a legitimate multi-packet read")


class TestThePeerIsPinned:
    """**Any LAN peer used to be able to inject a turn.** The link set
    `verify_mode = CERT_NONE` - correct for trust-on-first-use - and nothing then
    checked *who* had completed the handshake. So any laptop on the network that
    presented any self-signed certificate reached `LanBridge.handle`, and a
    `kdeconnect.clipboard` packet from it became `gateway.Submit("phone", text)`.

    Maze-Connect names this as a threat row and states the rule: *"every later
    connection must match the pinned fingerprint exactly; on mismatch, hard-fail"*,
    and separately *"any unpaired peer could answer for a pairing in progress and
    have its own link marked trusted"*. The peer's full identity arrives on the
    link as a matter of course, so the check costs nothing.
    """

    def test_a_peer_claiming_someone_else_is_refused(self):
        """`expect_peer` set to one id, the peer answers with another: the link
        closes and `PeerMismatch` says which two differed."""
        link = lan_link.Link("127.0.0.1", timeout=5)
        link.expect_peer = "a" * 32
        link._peer_reply_body = {"deviceId": "b" * 32}   # read by the stub below
        link._raw = None
        closed = []

        class _Recording:
            def close(self):
                closed.append(True)

        link._tls = None
        link._raw = _Recording()
        with pytest.raises(lan_link.PeerMismatch) as caught:
            link._check_peer_identity()
        assert "expected" in str(caught.value) and "answered" in str(caught.value)
        assert "aaaaaaaaaaaa" in str(caught.value)
        # **The link must close, not just decline the message.** A peer claiming
        # to be somebody else is not a peer whose next packet is worth reading,
        # and leaving the socket open is an invitation. Asserted because a
        # mutation that removed the `close()` passed every other test here.
        assert closed, "the link stayed open after an identity mismatch"

    def test_the_pinned_peer_is_accepted_and_recorded(self):
        link = lan_link.Link("127.0.0.1", timeout=5)
        link.expect_peer = "a" * 32
        link._peer_reply_body = {"deviceId": "a" * 32}
        link._check_peer_identity()
        assert link.peer_id == "a" * 32

    def test_no_pin_means_no_check(self):
        """`expect_peer=None` is the pairing flow: nothing is pinned yet and a
        person is comparing a code on two screens. The link still records who
        answered, so a caller can pin afterwards."""
        link = lan_link.Link("127.0.0.1", timeout=5)
        link._peer_reply_body = {"deviceId": "z" * 32}
        link._check_peer_identity()
        assert link.peer_id == "z" * 32

    def test_a_peer_that_sends_no_identity_is_refused(self):
        """Not identified is worse than mis-identified: there is nothing to
        compare against, and a peer that will not say who it is has no claim to
        be let in."""
        link = lan_link.Link("127.0.0.1", timeout=5)
        link.expect_peer = "a" * 32
        link._peer_reply_body = {}
        with pytest.raises(RuntimeError) as caught:
            link._check_peer_identity()
        assert "no identity" in str(caught.value)

    def test_short_ids_are_not_compared_by_accident(self):
        """The message shows the first twelve characters, so a person can tell
        which device it is - but two different ids sharing a prefix must still
        be refused, which is what comparing the whole string does.
        """
        link = lan_link.Link("127.0.0.1", timeout=5)
        link.expect_peer = "a" * 31 + "b"
        link._peer_reply_body = {"deviceId": "a" * 31 + "c"}
        with pytest.raises(lan_link.PeerMismatch):
            link._check_peer_identity()


class TestACorruptCertificateRefuses:
    """**It used to silently regenerate**, which Maze-Connect's threat model
    names as the failure: *"a corrupt or unreadable key fails startup loudly
    rather than being silently regenerated, which would invalidate every pairing
    unnoticed"*. That is not theoretical - a new certificate looks to a paired
    phone like a different computer, and the pairing vanishes with nothing saying
    why. This session lost a pairing to exactly that and spent many turns blaming
    the phone's state machine.
    """

    def test_a_corrupt_certificate_is_refused_not_regenerated(self, tmp_path):
        directory = tmp_path / "kdeconnect"
        directory.mkdir()
        cert, key = directory / "tls.pem", directory / "tls.key"
        cert.write_bytes(b"definitely not a certificate")
        key.write_bytes(b"nor a key")
        out = lan_link._our_certificate(directory=directory)
        assert out is None, "a corrupt certificate was accepted"
        assert cert.read_bytes() == b"definitely not a certificate", (
            "the corrupt file was overwritten - the pairing is gone and nothing "
            "says so")

    def test_a_good_certificate_is_still_used(self, tmp_path):
        """**The control.** Refusing everything would look identical to fixing
        this - a certificate check that always says no is the same defect in the
        other direction, and only this test can tell them apart.
        """
        import subprocess
        directory = tmp_path / "kdeconnect"
        directory.mkdir()
        cert, key = directory / "tls.pem", directory / "tls.key"
        done = subprocess.run(
            ["openssl", "req", "-x509", "-newkey", "rsa:2048", "-keyout", str(key),
             "-out", str(cert), "-days", "3650", "-nodes", "-subj", "/CN=" + "a" * 32],
            capture_output=True, check=False)
        if done.returncode != 0 or not (cert.is_file() and key.is_file()):
            pytest.skip("openssl is not here, so there is no good certificate to test with")
        assert lan_link._our_certificate(directory=directory) == (cert, key), (
            "a certificate that loads was refused - the check is now a blanket "
            "refusal rather than a check")


class TestTheTlsVersionIsPinned:
    """**Python's default allows a downgrade to TLS 1.2**, which on this
    interpreter is `MINIMUM_SUPPORTED`. A protocol whose whole security story is
    "TLS protects the payload" has exactly one surface, and a version downgrade is
    the way to reach it: an attacker who can strip the TLS 1.3 offer from a
    ClientHello gets the weaker handshake and everything that comes with it.

    Maze-Connect pins `min == max` to 1.3 and states "no TLS 1.2 fallback, ever,
    on either stack". Both ends here are ours - the protocol speaks to
    `chronoa-cast`, this project's own app - so there is no legacy peer to stay
    compatible with, which is the only reason the pin is free.
    """

    def _context(self, tmp_path):
        import subprocess
        directory = tmp_path / "kdeconnect"
        directory.mkdir()
        cert, key = directory / "tls.pem", directory / "tls.key"
        done = subprocess.run(
            ["openssl", "req", "-x509", "-newkey", "rsa:2048", "-keyout", str(key),
             "-out", str(cert), "-days", "3650", "-nodes", "-subj", "/CN=" + "a" * 32],
            capture_output=True, check=False)
        if done.returncode != 0:
            pytest.skip("openssl is not here, so there is no certificate to load")
        original = lan_link._our_certificate
        lan_link._our_certificate = lambda directory=None: (cert, key)
        try:
            return lan_link.Link("127.0.0.1")._context()
        finally:
            lan_link._our_certificate = original

    def test_min_and_max_are_both_thirteen(self, tmp_path):
        context = self._context(tmp_path)
        assert context.minimum_version == context.maximum_version, (
            "a range, not a pin: a peer may still negotiate down")
        assert context.minimum_version.name == "TLSv1_3"

    def test_it_is_not_the_library_default(self, tmp_path):
        """**The control.** `ctx.minimum_version` left alone is the interpreter
        default, so this is what proves the pin is the code's doing rather than
        the platform's."""
        import ssl
        plain = ssl.create_default_context(ssl.Purpose.CLIENT_AUTH)
        context = self._context(tmp_path)
        assert context.minimum_version != plain.minimum_version, (
            "the context is just the default, so nothing was pinned")
        assert plain.minimum_version.name != "TLSv1_3", (
            "this interpreter already defaults to 1.3, so the pin is untested "
            "here - the claim would be vacuous")
