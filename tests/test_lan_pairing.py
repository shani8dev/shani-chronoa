"""KDE Connect pairing over a real TLS link, against `tools/fake_kdeconnect.py`.

Until this ran, **no LAN link of any kind could open**: `_link` passed
`expect_peer` to a constructor that did not take it (TypeError), and `connect`
read the peer's identity through a function that skips identities. Every test
before stubbed the identity, so neither was visible. These drive the real
handshake on loopback, with this computer's certificate in a temporary state
directory - never the real one, which a phone may have pinned.
"""
import pathlib
import sys
import time

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "usr" / "lib" / "shani-chronoa"))
sys.path.insert(0, str(ROOT / "tools"))

from shani_chronoa import lan_link  # noqa: E402
import fake_kdeconnect  # noqa: E402


@pytest.fixture(autouse=True)
def private_state(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state"))
    yield


def _phone(answer="accept"):  # noqa: D103
    return fake_kdeconnect.FakePhone(answer=answer, delay=0.1).start()


def _wait(cond, seconds=5):
    deadline = time.monotonic() + seconds
    while not cond() and time.monotonic() < deadline:
        time.sleep(0.02)
    return cond()


def test_a_link_opens_and_reads_the_peers_identity():
    phone = _phone()
    link, why = lan_link._link("127.0.0.1", timeout=5, tcp_port=phone.port, expect_peer=None)
    assert link is not None, why
    assert link.peer_id == fake_kdeconnect.PHONE_ID
    assert link.peer_identity.get("deviceName") == "Fake Pixel"
    link.close()
    assert phone.plain_identities[0]["body"]["deviceType"] == "desktop"


def test_accepted_pairing_is_confirmed_and_remembered():
    phone = _phone("accept")
    state, why = lan_link.pair("127.0.0.1", timeout=5, tcp_port=phone.port)
    assert state == "paired", why
    assert _wait(lambda: len(phone.got("kdeconnect.pair")) == 2), \
        "the computer never said its own pair: true back"
    stored = lan_link.paired_device()
    assert stored["deviceId"] == fake_kdeconnect.PHONE_ID
    assert stored["address"] == "127.0.0.1" and stored["tcpPort"] == phone.port
    assert lan_link.pairing_status()[0] == "paired"


def test_rejected_and_unanswered_store_nothing():
    for answer, state in (("reject", "rejected"), ("ignore", "waiting")):
        phone = _phone(answer)
        got, _why = lan_link.pair("127.0.0.1", timeout=2, tcp_port=phone.port)
        assert got == state
        assert lan_link.paired_device() == {}


def test_links_are_pinned_to_the_paired_phone():
    phone = _phone()
    assert lan_link.pair("127.0.0.1", timeout=5, tcp_port=phone.port)[0] == "paired"
    link, why = lan_link._link("127.0.0.1", timeout=5, tcp_port=phone.port)
    assert link is not None, why
    link.close()
    # another device answering the same address is refused
    impostor = fake_kdeconnect.FakePhone(device_id="e" * 32).start()
    link, why = lan_link._link("127.0.0.1", timeout=5, tcp_port=impostor.port)
    assert link is None and "PeerMismatch" in why, why


def test_no_pairing_means_no_link():
    phone = _phone()
    link, why = lan_link._link("127.0.0.1", timeout=5, tcp_port=phone.port)
    assert link is None and "no phone is paired" in why
    assert phone.links == 0, "a link was opened with nothing to pin it to"


def test_the_bridge_answers_the_paired_phone():
    from shani_chronoa.lan_bridge import LanBridge
    phone = _phone()
    assert lan_link.pair("127.0.0.1", timeout=5, tcp_port=phone.port)[0] == "paired"
    sent = LanBridge(submit=lambda gw, text: f"echo {text}")
    reply = sent.handle({"type": "kdeconnect.clipboard",
                         "body": {"content": "hello", "timestamp": 1}})
    assert reply == "echo hello"
    assert sent.tell("127.0.0.1", reply)
    assert _wait(lambda: phone.got("kdeconnect.notification"))
    assert phone.got("kdeconnect.notification")[0]["body"]["text"] == "echo hello"
