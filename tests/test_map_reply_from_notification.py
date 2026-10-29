"""A phone text answered from its notification: the "inline-reply" action is
offered, and a reply goes to the phone's outbox only while sending is allowed."""
from shani_chronoa import map_notify as mn


class Cfg:
    def __init__(self, send=True):
        self.on = {"phone-control-enabled": True, "phone-messages-read-enabled": True,
                   "phone-messages-send-enabled": send}

    def get_bool(self, k, default=False):
        return self.on.get(k, default)


class Bus:
    def __init__(self):
        self.calls = []

    def call_sync(self, name, path, iface, method, params, *a):
        self.calls.append((name, method, params.unpack()))

        class R:
            def unpack(self_inner):
                return (41,)
        return R()


def test_reply_is_offered_and_sent_while_sending_is_allowed():
    sent = []
    w = mn.MessageWatcher(Cfg(), send=lambda phone, number, text: sent.append((phone, number, text)))
    bus = Bus()
    nid = w.notify_replyable(bus, "Message from Asha", "Reached the station", "AA:BB:CC:DD:EE:FF", "+919811111111")
    name, method, (app, _r, _icon, title, body, actions, hints, _t) = bus.calls[0]
    assert name == mn.NOTIFY and method == "Notify" and nid == 41
    assert actions == ["inline-reply", "Reply"] and title == "Message from Asha"
    assert w.on_replied(41, " On my way ") is True
    assert sent == [("AA:BB:CC:DD:EE:FF", "+919811111111", "On my way")]
    assert w.on_replied(41, "again") is False          # one reply per notification


def test_no_send_when_sending_is_off_or_nothing_typed():
    sent = []
    w = mn.MessageWatcher(Cfg(send=False), send=lambda *a: sent.append(a))
    w.replies[7] = ("AA", "+91")
    assert w.on_replied(7, "hello") is False
    w = mn.MessageWatcher(Cfg(), send=lambda *a: sent.append(a))
    w.replies[8] = ("AA", "+91")
    assert w.on_replied(8, "   ") is False
    assert w.on_replied(999, "who?") is False           # not one of ours
    assert sent == []
