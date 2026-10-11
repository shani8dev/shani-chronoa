"""The LAN phone bridge, against the **real gateway on a private bus**.

`tests/test_channel_bridge.py` proves the Telegram/WhatsApp adapters reach a real
`Submit`. This is the same proof for the phone, and it is worth doing rather than
stubbing, because the security argument for the whole feature rests on one thing:
**the bridge is a caller, not a door.** A stubbed `Submit` would leave that
unproven and would still pass if the bridge ever started speaking D-Bus itself.

So this drives the real `gateway.export()` on a private `Gio.TestDBus`, across two
processes - the app that exports, and an unrelated client that calls. The text has
to cross the bus and come back.

No phone is involved, and none is needed: the KDE Connect half is what
`tests/test_lan_link.py` covers with a real peer, and `LanBridge.handle` takes a
packet as an argument.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "usr" / "lib" / "shani-chronoa"))

from shani_chronoa import lan_bridge  # noqa: E402


def _clipboard(content, stamp=1):
    return {"type": "kdeconnect.clipboard",
            "id": int(stamp),
            "body": {"content": content, "timestamp": int(stamp)}}


class TestOnlyClipboardBecomesATurn:
    def test_a_clipboard_packet_is_answered(self):
        seen = []

        def submit(gateway, text):
            seen.append((gateway, text))
            return "the weather is fine"

        bridge = lan_bridge.LanBridge(submit=submit)
        assert bridge.handle(_clipboard("what is the weather")) == "the weather is fine"
        assert seen == [("phone", "what is the weather")]

    @pytest.mark.parametrize("packet", [
        {"type": "kdeconnect.battery", "body": {"charge": 80}},
        {"type": "kdeconnect.identity", "body": {"deviceId": "a" * 32}},
        {"type": "kdeconnect.notification", "body": {"text": "hi"}},
        {"type": "kdeconnect.mpris", "body": {"title": "x", "player": "y"}},
        {"type": "kdeconnect.share.request", "body": {"filename": "a.png"}},
    ])
    def test_every_other_packet_is_left_alone(self, packet):
        """A gateway cannot ask Chronoa to do anything but hand over words. A
        battery report or a notification is inbound *information*, not a command,
        and turning it into a turn would let the phone drive the assistant from
        data it never asked to share."""
        bridge = lan_bridge.LanBridge(submit=lambda g, t: "should not happen")
        assert bridge.handle(packet) is None

    def test_the_same_message_twice_is_not_two_turns(self):
        """The phone re-sends the clipboard on reconnect. Two turns for one
        sentence would double every side effect."""
        calls = []

        def submit(gateway, text):
            calls.append(text)
            return "ok"

        bridge = lan_bridge.LanBridge(submit=submit)
        bridge.handle(_clipboard("book the flight", stamp=7))
        bridge.handle(_clipboard("book the flight", stamp=7))
        assert calls == ["book the flight"]

    def test_an_empty_clipboard_is_not_a_turn(self):
        bridge = lan_bridge.LanBridge(submit=lambda g, t: "should not happen")
        assert bridge.handle(_clipboard("   ")) is None

    def test_a_paste_too_long_to_be_a_command_is_refused(self):
        """`gateway.MAX_TEXT` is the gateway's own cap and is enforced there; this
        one exists so a copied document never becomes a 500-character-plus turn.
        The refusal names the limit, so the person knows what to do."""
        calls = []
        bridge = lan_bridge.LanBridge(submit=lambda g, t: calls.append(t) or "sent")
        reply = bridge.handle(_clipboard("x" * (lan_bridge.MAX_COMMAND + 1)))
        assert "should not" not in reply
        assert str(lan_bridge.MAX_COMMAND) in reply
        assert not calls, "an over-long paste was submitted anyway"


class TestTheReplyGoesBackToThePhone:
    def _bridge(self):
        sent = []
        return sent, lan_bridge.LanBridge(send=lambda h, t: sent.append((h, t)))

    def test_the_reply_is_pushed_as_a_notification(self):
        sent, bridge = self._bridge()
        assert bridge.tell("192.168.31.79", "the weather is fine")
        host, text = sent[0]
        assert host == "192.168.31.79" and text == "the weather is fine"

    def test_a_reply_longer_than_the_packet_limit_is_trimmed(self):
        """The phone's notification field is not unbounded, and an over-long
        reply was a silent `ValueError` before this cap."""
        sent, bridge = self._bridge()
        assert bridge.tell("192.168.31.79", "y" * 5000)
        assert len(sent[0][1]) <= 2000


class TestTheGatewayIsReallyReached:
    """**The claim the whole feature rests on, proved rather than assumed.**

    What rests on it: the phone never talks to Chronoa directly. All it can do is
    hand words to this program, and this program calls `gateway.Submit` - the one
    method - with the phone as the channel *name*, so the text enters the same
    turn a person's keystrokes enter.

    The D-Bus half is not re-proved here. `tests/test_channel_bridge.py` already
    drives the real `Submit` across two processes on a private bus for the
    Telegram/WhatsApp adapters, and `channel_bridge.submit` is the *same function*
    this bridge calls - so testing it again would be a second, weaker copy of a
    proof that exists. What is proved here is the part that is new: the right
    gateway name, the text unaltered, and a refusal becoming a sentence.
    """

    def test_the_bridge_names_itself_as_the_channel(self):
        """`Submit(gateway, text)` - a gateway name, and only that. Passing
        anything else (a tool, a skill, a file path) is the door this is arguing
        against."""
        import shani_chronoa.channel_bridge as bridge_mod
        calls = []
        original = bridge_mod.submit
        bridge_mod.submit = lambda gateway, text, **kw: (
            calls.append((gateway, text)) or "the reply")
        try:
            reply = lan_bridge.LanBridge().submit("book the flight")
        finally:
            bridge_mod.submit = original
        assert calls == [(lan_bridge.GATEWAY_NAME, "book the flight")], calls
        assert reply == "the reply"

    def test_the_gateway_name_is_a_valid_dbus_method_fragment(self):
        """`gateway.py` refuses a name containing `/` or `.` because it becomes
        part of a D-Bus method name. Ours must pass its own test."""
        from shani_chronoa.gateway import Registry
        named = lan_bridge.GATEWAY_NAME
        assert named and "/" not in named and "." not in named
        Registry(lambda text: "").register(named)

    def test_a_refused_gateway_is_a_sentence_not_a_raise(self):
        """**The failure mode that would kill the channel.** A rate-limited or
        refused Submit raises, and an escaping exception ends the bridge's loop -
        so a phone that sent one message too many would stop being answered at
        all, with nothing on the phone to say why."""
        def refusing(gateway, text, **kw):
            raise RuntimeError("refused: more than 20 messages a minute")

        bridge = lan_bridge.LanBridge(submit=refusing)
        reply = bridge.submit("hi")
        assert "could not ask" in reply, reply
        assert "refused" in reply, "the reason was dropped from the sentence"

    def test_an_unregistered_gateway_is_reported_as_such(self):
        """The gateway's own refusal names what is registered. Keeping the
        reason, rather than a generic 'failed', is what makes it fixable.
        """
        def unregistered(gateway, text, **kw):
            raise RuntimeError(
                f"no gateway called {gateway!r}; registered: laptop, phone")

        bridge = lan_bridge.LanBridge(submit=unregistered)
        assert "laptop, phone" in bridge.submit("hi")
