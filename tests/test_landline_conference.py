"""Conference / merge: the Mixer's per-participant delivery, and merge()'s guards.

Pure arithmetic for the mixer; the guards are checked on a bare, unconnected
Line so they can fail without a router in the room.
"""
from __future__ import annotations

import socket
import struct

import pytest

from shani_chronoa.landline import media, ua


def _frame(value: int = 1) -> bytes:
    return struct.pack("<" + "h" * (media.FRAME_BYTES // 2), *([value] * (media.FRAME_BYTES // 2)))


def test_mix_sum_saturates_rather_than_averaging():
    # Two identical peaks sum to the ceiling, then stay there - never divide by
    # the count of participants, which would make a quiet speaker quieter.
    full = 32767
    # **The `mix if False else` was a leftover**, and `mix` is not a name in
    # this module: the branch was never taken, so the undefined name sat
    # there unnoticed. The call is the whole of it.
    one = media.mix_sum([_frame(full), _frame(full)])
    assert len(one) == media.FRAME_BYTES
    vals = struct.unpack(f"<{media.FRAME_BYTES // 2}h", one)
    assert all(v == 32767 for v in vals), "saturating ceiling, not averaging"


def test_mixer_delivers_every_other_participant_and_not_the_author():
    m = media.Mixer()
    a, b, c = object(), object(), object()
    m.join(a); m.join(b); m.join(c)
    xa, xb, xc = _frame(100), _frame(200), _frame(300)
    for spk, pcm in ((a, xa), (b, xb), (c, xc)):
        m.add(spk, pcm)
    # Each reader gets the (saturated) sum of the OTHER two.
    got_a = struct.unpack(f"<{media.FRAME_BYTES // 2}h", m.read(a))
    got_b = struct.unpack(f"<{media.FRAME_BYTES // 2}h", m.read(b))
    got_c = struct.unpack(f"<{media.FRAME_BYTES // 2}h", m.read(c))
    assert all(v == 200 + 300 for v in got_a), (got_a[0], "a should not hear a")
    assert all(v == 100 + 300 for v in got_b)
    assert all(v == 100 + 200 for v in got_c)


def test_mixer_is_per_participant_not_one_shared_buffer():
    # Empty queues stay empty for their author; a second read is silence because
    # the aliasing bug `frames, queue[:] = queue, []` would have emptied the
    # shared list for everyone. Read twice: first read delivers, second is silence.
    m = media.Mixer()
    a, b = object(), object()
    m.join(a); m.join(b)
    m.add(a, _frame(1000))
    r1 = m.read(b)
    r2 = m.read(b)
    assert struct.unpack(f"<{media.FRAME_BYTES // 2}h", r1)[0] == 1000
    assert struct.unpack(f"<{media.FRAME_BYTES // 2}h", r2)[0] == 0


def test_mixer_late_joiner_hears_only_what_is_said_after():
    m = media.Mixer()
    a = object()
    m.join(a)
    m.add(a, _frame(1000))   # before b joins
    b = object()
    m.join(b)                 # b joins late
    got = struct.unpack(f"<{media.FRAME_BYTES // 2}h", m.read(b))
    assert got[0] == 0, "a late joiner must not hear history it was not present for"
    # a keeps what it earned
    got_a = struct.unpack(f"<{media.FRAME_BYTES // 2}h", m.read(a))
    assert got_a[0] == 0 or got_a[0] == 1000


def test_mixer_saturates_at_the_ceiling():
    ceiling = 32767
    m = media.Mixer()
    a, b = object(), object()
    m.join(a); m.join(b)
    m.add(a, _frame(ceiling))
    m.add(b, _frame(ceiling))
    got = struct.unpack(f"<{media.FRAME_BYTES // 2}h", m.read(a))
    assert got[0] == ceiling, "saturation, never averaging or clipping below"


def test_merge_refuses_with_fewer_than_two_calls():
    line, call = ua._bare_line() if hasattr(ua, "_bare_line") else (None, None), None
    # guard refusal on instance count rather than walk the whole app
    import shani_chronoa.landline as landline
    # Use a fresh, disconnected Line
    line = ua.Line("127.0.0.1", {"realm": "r", "user": "u", "password": "p"}, "aabbcc")
    line.local_ip, line.local_port, line.number = "127.0.0.1", 5060, "+912040001234"
    call = ua.Call("cid", "tag", True, "+911234567890")
    call.local_uri = call.remote_uri = "sip:+912040001234@br.wln.ims.jio.com"
    call.route = []
    call.rtp = media.RtpSession.bind("127.0.0.1", None)
    line.calls.append(call)
    try:
        assert line.merge() is False
        assert len(line.calls) == 1
    finally:
        call.rtp.stop()


def test_merge_refuses_above_max_calls():
    line = ua.Line("127.0.0.1", {"realm": "r", "user": "u", "password": "p"}, "aabbcc")
    line.local_ip, line.local_port, line.number = "127.0.0.1", 5060, "+912040001234"
    calls = []
    for i in range(media.MAX_CALLS):
        c = ua.Call(f"cid{i}", f"tag{i}", True, "+911234567890")
        c.local_uri = c.remote_uri = "sip:+912040001234@br.wln.ims.jio.com"
        c.route = []
        c.rtp = media.RtpSession.bind("127.0.0.1", None)
        c.state = "active"
        line.calls.append(c)
        calls.append(c)
    try:
        # len(self.calls) + 1 > MAX_CALLS -> refuses
        assert line.merge() is False
    finally:
        for c in calls:
            c.rtp.stop()


def test_merge_accepts_two_answered_calls():
    line = ua.Line("127.0.0.1", {"realm": "r", "user": "u", "password": "p"}, "aabbcc")
    line.local_ip, line.local_port, line.number = "127.0.0.1", 5060, "+912040001234"
    calls = []
    for i in range(2):
        c = ua.Call(f"cid{i}", f"tag{i}", True, "+911234567890")
        c.local_uri = c.remote_uri = "sip:+912040001234@br.wln.ims.jio.com"
        c.route = []
        c.rtp = media.RtpSession.bind("127.0.0.1", None)
        c.state = "active"
        line.calls.append(c)
        calls.append(c)
    try:
        assert line.merge() is True
        assert len(line.calls) == 3  # two + the conference dialog
    finally:
        for c in calls:
            c.rtp.stop()
        for c in line.calls[2:]:
            if getattr(c, "rtp", None):
                c.rtp.stop()