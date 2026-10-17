"""The call's picture: H.264 over RTP, RTCP, and the SDP that offers it.

The packetiser is tested against **bytes**, not against ffmpeg. The reason is
the same one the audio tests record for AMR: what has to interoperate with Jio's
network is the wire format, and an encoder that happens to agree with our own
decoder would pass a test that measures nothing about the format.

Four defects this file pins, all found by running the code rather than reading
it, and each of them is invisible on a loopback:

- `_fua` built packets one byte longer than the MTU, because the FU overhead is
  two bytes (indicator + header) and it budgeted for one.
- FU-A lost the original NAL type: the type was written into the FU header's low
  bits and then overwritten by the S/E/R byte. Every reassembled unit came back
  typed as FU-A rather than as the picture it was.
- The STAP-A reader skipped each unit's own 2-byte length field, so every
  aggregated unit came back with two leading bytes that were not part of it.
- The STAP-A reader then measured the aggregate from the type byte instead of
  from after the size field. That drops the *smallest* unit - a NAL carried
  alone, whose 6-byte aggregate never enters its loop - and it took a randomised
  sweep rather than a boundary sweep to find it: 1 failure in 400 streams.
"""
import os
import random
import socket
import subprocess
import time

import pytest

from shani_chronoa.landline import media, video


# --- Annex-B <-> NAL units -------------------------------------------------------

def test_a_four_byte_start_code_does_not_leave_a_zero_on_the_nal():
    """`00 00 00 01` contains `00 00 01` one byte in, so scanning for the short
    form finds a match *inside* the code."""
    stream = b"\x00\x00\x00\x01\x67\x42\x00\x00\x00\x01\x68\xce"
    assert [u.hex() for u in video.split_nals(stream)] == ["6742", "68ce"]


def test_both_start_code_lengths_parse_the_same_way():
    three = video.to_annexb([b"\x67\x42", b"\x68\xce"])
    four = b"\x00\x00\x01\x67\x42\x00\x00\x01\x68\xce"
    assert video.split_nals(three) == video.split_nals(four) == [b"\x67\x42", b"\x68\xce"]


def test_annexb_round_trips():
    units = [bytes([0x67, 0x42, 0x00]), bytes([0x68, 0xCE, 0x3C])]
    assert video.split_nals(video.to_annexb(units)) == units


def test_a_stream_with_no_start_code_has_no_nal_units():
    assert video.split_nals(b"\x00\x00") == []
    assert video.split_nals(b"") == []


def test_nal_type_reads_the_low_five_bits():
    assert video.nal_type(b"\x65\xff") == 5
    assert video.nal_type(b"\x09") == video.AUD
    assert video.nal_type(b"") == -1


# --- packetisation (RFC 6184) ----------------------------------------------------

@pytest.mark.parametrize("size", list(range(1, 24)) + list(range(1190, 1210)) + [4096, 65536])
def test_one_nal_unit_survives_the_round_trip_at_every_size(size):
    nal = bytes([0x65]) + bytes(size)
    payloads = video.packetize([nal])
    assert video.depacketize(payloads) == [nal]


@pytest.mark.parametrize("payload_size", [1200, 1201, 1202, 1400])
def test_no_packet_is_longer_than_the_mtu(payload_size):
    nal = bytes([0x65]) + bytes(payload_size)
    assert all(len(p) <= video.MTU for p in video.packetize([nal]))


def test_a_parameter_set_rides_in_one_packet_with_what_it_describes():
    sps, pps = b"\x67\x42\x00\x1e", b"\x68\xce\x3c\x80"
    payloads = video.packetize([sps, pps])
    assert len(payloads) == 1, "SPS and PPS should share a packet"
    assert payloads[0][0] & 0x1F == 24, "and that packet should be a STAP-A"


def test_a_unit_too_big_to_aggregate_is_fragmented():
    nal = bytes([0x65]) + bytes(5000)
    payloads = video.packetize([nal])
    assert len(payloads) > 1
    assert all(p[0] & 0x1F == 28 for p in payloads), "every part should be FU-A"
    assert video.depacketize(payloads) == [nal]


def test_the_fragments_of_one_unit_carry_its_type_and_not_only_the_fragments_type():
    nal = bytes([0x65]) + bytes(4000)
    payloads = video.packetize([nal])
    for index, payload in enumerate(payloads):
        header = payload[1]
        assert header & 0x1F == 0x65 & 0x1F, "the FU header carries the unit's type"
        assert bool(header & 0x80) == (index == 0), "only the first part starts"
        assert bool(header & 0x40) == (index == len(payloads) - 1), "only the last ends"


def test_a_unit_just_under_the_mtu_is_not_aggregated_into_an_over_long_packet():
    """The size where a STAP-A wrapper would push it over is where the naive
    projection (missing the aggregate size field) lets a packet through."""
    nal = bytes([0x65]) + bytes(video.MTU - 5)
    payloads = video.packetize([nal])
    assert all(len(p) <= video.MTU for p in payloads)
    assert video.depacketize(payloads) == [nal]


def test_a_single_nal_carried_alone_survives():
    """The unit a STAP-A reader loses when it measures the aggregate from the type
    byte rather than from after the size field."""
    nal = bytes([video.AUD])
    assert video.depacketize(video.packetize([nal])) == [nal]


def test_a_mixed_stream_survives_intact():
    units = [b"\x67\x42\x00\x1e", b"\x68\xce", bytes([0x65]) + bytes(5000),
             bytes([0x41]) + bytes(30), bytes([video.AUD])]
    payloads = video.packetize(units)
    assert all(len(p) <= video.MTU for p in payloads)
    assert video.depacketize(payloads) == units


@pytest.mark.parametrize("seed", range(4))
def test_randomised_streams_round_trip(seed):
    """A boundary sweep finds the FU-A off-by-one and misses the STAP-A one; the
    randomised sweep is what found a single NAL carried alone."""
    rng = random.Random(seed)
    for _ in range(150):
        units = [bytes([rng.choice([1, 5, 7, 8, 9])]) + bytes(rng.randrange(0, 4000))
                 for _ in range(rng.randrange(1, 10))]
        payloads = video.packetize(units)
        assert all(len(p) <= video.MTU for p in payloads), "a packet went over the MTU"
        assert video.depacketize(payloads) == units


# --- frames ----------------------------------------------------------------------

def test_a_frame_is_handed_over_only_when_the_next_one_starts():
    frame = b"\x00\x00\x00\x01\x09\x10" + b"\x00\x00\x00\x01\x65\xff"
    assert video.take_frame(frame) == (b"", frame), "one delimiter is not a frame"
    two = frame + b"\x00\x00\x00\x01\x09\x10" + b"\x00\x00\x00\x01\x41\x99"
    ready, rest = video.take_frame(two)
    assert ready == frame, "the first whole frame should be ready"
    assert rest.endswith(b"\x41\x99"), "the rest should be what has not arrived yet"


def test_an_encoder_that_never_delimits_still_sends_something():
    """A stream with no access-unit delimiter is one ffmpeg has stopped producing;
    the fallback splits on start codes rather than sending nothing at all."""
    stream = b"\x00\x00\x00\x01\x65" + bytes(video.NO_AUD_AFTER) + \
        b"\x00\x00\x00\x01\x41\x99" + b"\x00\x00\x00\x01\x42"
    ready, rest = video.take_frame(stream)
    assert ready and video.split_nals(ready), "something complete should be ready"
    assert rest.startswith(b"\x00\x00\x00\x01\x42"), "the last unit is still arriving"


# --- RTCP -----------------------------------------------------------------------

def test_a_receiver_report_names_the_peer_and_the_losses():
    report = video.rtcp_report(our_ssrc=1, their_ssrc=2, packets=90, lost=4,
                               highest_seq=1000, jitter=12)
    ssrc, kinds, _keyframe = video.parse_rtcp(report)
    assert kinds == [video.PT_SR, video.PT_RR]
    assert ssrc == 1, "the first report is ours and carries our own SSRC"


def test_a_keyframe_request_is_recognised():
    _ssrc, kinds, keyframe = video.parse_rtcp(video.rtcp_keyframe(1))
    assert kinds == [video.PT_FIR]
    assert keyframe is True


def test_a_report_is_not_mistaken_for_a_keyframe_request():
    _ssrc, _kinds, keyframe = video.parse_rtcp(video.rtcp_report(1, 2, 1, 0, 1, 0))
    assert keyframe is False, "a report is not a request"


def test_a_short_packet_is_not_read_as_rtcp():
    assert video.parse_rtcp(b"") == (0, [], False)
    assert video.parse_rtcp(b"\x80") == (0, [], False)


def test_a_packet_that_is_not_version_two_is_refused():
    assert video.parse_rtcp(bytes([0x40]) + b"\x00\x00\x01" + bytes(8))[2] is False


# --- SDP -------------------------------------------------------------------------

def _line_index(sdp: str, needle: str) -> int:
    for number, line in enumerate(sdp.splitlines()):
        if line.strip().startswith(needle):
            return number
    raise AssertionError(f"{needle} is not in the offer:\n{sdp}")


def test_video_is_appended_after_the_audio_media_section():
    """`m=video` before `m=audio` is a different document, not a formatting choice -
    and the audio codec list is read from the offer rather than typed here, so a
    codec landing in the list does not fail a test that is about section order."""
    sdp = media.offer("127.0.0.1", 4000)
    both = video.with_video(sdp, "127.0.0.1", 4002)
    assert _line_index(both, "m=audio") < _line_index(both, "m=video")
    assert f"m=video 4002 RTP/AVP {video.H264_PT}" in both


def test_the_video_port_is_the_one_we_bound_not_a_typed_number():
    """The port comes from the session, so a change to it is caught rather than
    pinned to a literal here."""
    session = video.VideoRtpSession.bind("127.0.0.1")
    try:
        body = video.with_video(media.offer("127.0.0.1", 4000), "127.0.0.1", session.port)
        assert f"m=video {session.port} RTP/AVP" in body
    finally:
        session.stop()


def test_the_video_offer_asks_for_the_three_ways_to_get_a_keyframe():
    body = video.with_video(media.offer("127.0.0.1", 4000), "127.0.0.1", 4002)
    for line in ("a=rtcp-fb:96 nack pli", "a=rtcp-fb:96 ccm fir", "a=sendrecv"):
        assert line in body, f"{line} should be offered"


def test_the_video_section_is_read_back_from_an_offer():
    body = video.with_video(media.offer("127.0.0.1", 4000), "10.0.0.5", 4002)
    ip, port, codecs = video.parse_video(body)
    assert (ip, port) == ("127.0.0.1", 4000 + 2)
    assert codecs == [(video.H264_PT, f"H264/{video.CLOCK_RATE}")]


def test_adding_video_twice_does_not_add_it_twice():
    once = video.with_video(media.offer("127.0.0.1", 4000), "127.0.0.1", 4002)
    assert video.with_video(once, "127.0.0.1", 4002).count("m=video") == 1


def test_a_video_section_the_far_end_declined_is_read_as_no_port():
    """`m=video 0` is how a peer declines a stream (RFC 3264 8.2.2), and it must not
    be read as a port to send to."""
    declined = video.with_video(media.offer("127.0.0.1", 4000), "127.0.0.1", 4002)
    declined = declined.replace("m=video 4002", "m=video 0")
    _ip, port, codecs = video.parse_video(declined)
    assert port == 0 or codecs == []


def test_the_encoder_is_asked_for_access_unit_delimiters():
    """Without `-aud 1` ffmpeg emits none, and a delimiter is the only thing that
    says "that is one whole picture". Measured on this machine's own encoder: 30
    frames, zero delimiters without the flag and exactly 30 with it."""
    argv = video._capture_argv(video.next_camera(), 15)
    assert "-aud" in argv, "the encoder must be asked for delimiters"
    assert argv[argv.index("-aud") + 1] == "1"


def test_the_fallback_bound_is_under_half_a_second_of_this_bitrate():
    """It was a typed 64 kB, which is 2.6 seconds at this encoder's rate - a call
    that shows nothing for two and a half seconds. Derived now, and bounded."""
    assert video.NO_AUD_AFTER == video.BITRATE_BPS / 8 * video.NO_AUD_SECONDS
    assert video.NO_AUD_AFTER / (video.BITRATE_BPS / 8) <= 0.5


def test_an_encoder_that_would_not_delimit_still_sends_within_half_a_second():
    """The bound, measured on the stream the fallback actually sees: after half a
    second of this bitrate's output something complete must already be ready."""
    half_second = int(video.BITRATE_BPS / 8 * 0.5)
    stream = b"\x00\x00\x00\x01\x65" + bytes(half_second) + \
        b"\x00\x00\x00\x01\x41\x99" + b"\x00\x00\x00\x01\x42"
    ready, _rest = video.take_frame(stream)
    assert ready and video.split_nals(ready), "nothing was ready after half a second"


# --- availability ----------------------------------------------------------------

def test_video_says_why_it_cannot_when_ffmpeg_is_absent(monkeypatch):
    monkeypatch.setattr(video.shutil, "which", lambda _name: None)
    usable, why = video.available()
    assert usable is False
    assert "ffmpeg" in why, f"the refusal should name what is missing, not {why!r}"


def test_video_says_why_it_cannot_when_the_camera_cannot_be_opened(monkeypatch):
    monkeypatch.setattr(video.shutil, "which", lambda _name: "/usr/bin/ffmpeg")
    monkeypatch.setattr(video.subprocess, "run", lambda *a, **k: type(
        "R", (), {"stdout": " V....D libx264  H.264"})())
    monkeypatch.setattr(video, "next_camera", lambda: "")
    usable, why = video.available()
    assert usable is False
    assert "camera" in why


def test_video_reports_a_real_camera_when_the_machine_has_one():
    """Not a stub of `available()` - the real probe against this machine's ffmpeg
    and its real /dev nodes, which is what makes the positive answer mean that a
    machine without either is correctly refused rather than always saying yes."""
    usable, why = video.available()
    assert usable is (bool(video.next_camera())), (
        f"a camera that exists ({video.next_camera()!r}) should answer for itself")
    assert why == (video.next_camera() or "there is no camera this computer can open")


# --- the session -----------------------------------------------------------------

def test_the_rtp_and_rtcp_ports_are_the_pair_rfc_3550_names():
    """RTCP goes to RTP + 1 unless the SDP says otherwise, and our offer does not
    say otherwise - so an ephemeral port with its neighbour taken cannot work."""
    session = video.VideoRtpSession.bind("127.0.0.1")
    try:
        assert session.port % 2 == 0, f"{session.port} must be even"
        assert session.rtcp is not None and session.rtcp.getsockname()[1] == session.port + 1
    finally:
        session.stop()

# --- the decoder -----------------------------------------------------------------

@pytest.fixture(scope="module")
def pictures():
    """Real access units from this machine's own encoder, as NAL units.

    Built rather than replayed from captured bytes because a decoder test needs
    pictures a decoder can *actually* decode, and Annex-B hand-written well
    enough to decode is a hand-written defect waiting to happen. 64x36 so a
    decode costs nothing, 3 seconds at 15 fps so the keyframe interval is
    crossed at least twice - which is what exercises the window bound.
    """
    encoded = subprocess.run(
        ["ffmpeg", "-hide_banner", "-loglevel", "error",
         "-f", "lavfi", "-i", "testsrc2=size=64x36:rate=15",
         "-t", "3", "-c:v", "libx264", "-preset", "ultrafast",
         "-tune", "zerolatency", "-g", "30", "-aud", "1",
         "-pix_fmt", "yuv420p", "-f", "h264", "-"],
        capture_output=True, timeout=120)
    assert encoded.stdout, "the encoder produced nothing to decode"
    out, rest = [], encoded.stdout
    while True:
        frame, rest = video.take_frame(rest)
        if not frame:
            break
        out.append(video.split_nals(frame))
    assert out, "no whole picture came out of the encoder"
    return out


def _has_keyframe(units) -> bool:
    return any(video.nal_type(u) == video.IDR for u in units)


def test_every_picture_fed_is_a_frame_handed_back(pictures):
    """`read()` answered `None` for every call after the first, because `decoded`
    is a *count* and a count is truthy - so it decoded the whole file once and
    stopped. Measured on the real loopback path: 141 pictures in, **1** frame
    out, and the stream looked like it was working."""
    dec = video.Decoder(64, 36)
    try:
        for units in pictures:
            dec.feed(units)
            assert dec.read() is not None, "a picture fed is a frame handed back"
        assert dec.decoded == len(pictures), (
            f"{len(pictures)} pictures in and {dec.decoded} frames out")
    finally:
        dec.stop()


def test_a_window_over_the_bound_is_cut_in_the_file_not_only_in_the_index(pictures):
    """Measured: the *index* was trimmed to one picture while the *file* still
    held thirty-one, so the next drain decoded all thirty-one and handed back
    thirty duplicates - 44 pictures in and 61 frames out. A drain runs over the
    bytes on disk, so a bound the file does not obey is not a bound."""
    dec = video.Decoder(64, 36)
    try:
        for units in pictures:
            dec.feed(units)
            while dec.read() is not None:
                pass
        assert dec.decoded == len(pictures), (
            f"{len(pictures)} pictures in, {dec.decoded} frames out - the window "
            "was not cut in the file")
        assert len(dec._units) <= dec.WINDOW_UNITS, (
            f"{len(dec._units)} units still in the window")
    finally:
        dec.stop()


def test_a_window_with_no_keyframe_in_it_is_not_cut(pictures):
    """Trimming to the last keyframe on a stream that has none would cut it to
    nothing - and a decode that does not begin on a keyframe produces nothing at
    all, so the bound yields to decodability rather than the other way round."""
    deltas = [u for u in pictures if not _has_keyframe(u)]
    dec = video.Decoder(64, 36)
    try:
        for units in deltas[:dec.WINDOW_UNITS + 6]:
            dec.feed(units)
            dec.read()
        assert dec.read() is None, "a stream with no keyframe has nothing to decode"
        assert len(dec._units) > dec.WINDOW_UNITS, (
            "the bound was enforced anyway, and the window was cut to nothing")
    finally:
        dec.stop()


def test_a_drain_asks_ffmpeg_to_drop_what_is_already_drawn(monkeypatch, pictures):
    """Without the drop, every drain re-emitted the whole window through the
    pixel pipe - measured, **51 MB** to hand over one new frame, which is what
    bounds a 15 fps stream on this machine. The drop has to happen inside
    ffmpeg, not in Python after the bytes have already crossed a pipe."""
    seen = []
    real = video.subprocess.run

    def spy(argv, **kw):
        if argv and argv[0] == "ffmpeg":
            seen.append(list(argv))
        return real(argv, **kw)

    monkeypatch.setattr(video.subprocess, "run", spy)
    dec = video.Decoder(64, 36)
    try:
        for units in pictures[:4]:
            dec.feed(units)
            dec.read()
    finally:
        dec.stop()
    skips = [int(element.split(",")[-1].rstrip(")")) for argv in seen
             for element in argv if element.startswith("select=gte(n")]
    assert skips == [1, 2, 3], (
        f"each drain should drop the pictures already drawn, got {skips}")
    assert all("-vsync" in argv for argv in seen), "the drain must not re-frame"


def test_the_temp_file_a_decode_runs_over_is_removed(pictures):
    dec = video.Decoder(64, 36)
    path = None
    dec.start()
    path = dec._path
    dec.feed(pictures[0])
    dec.stop()
    assert path and not os.path.exists(path), "the decode file outlived the decoder"


# --- the receiver's own bookkeeping ----------------------------------------------

def test_the_assembler_holds_a_unit_across_the_packets_that_carry_it():
    """The state is the point: an assembler whose fragments live in local
    variables completes nothing, which is what a real encode sent over loopback
    showed - 23 packets arrived and the decoder received zero."""
    one = video.Assembler()
    assert one.feed([b"\x65\x01\x02"]) == [b"\x65\x01\x02"], "a whole unit is a unit"
    two = video.Assembler()
    nal = b"\x65\x01\x02\x03\x04\x05"
    pieces = [bytes([0x1C | (nal[0] & 0xE0), 0x80 | (nal[0] & 0x1F)]) + nal[1:3],
              bytes([0x1C | (nal[0] & 0xE0), 0x40]) + nal[3:]]
    assert two.feed(pieces[:1]) == [], "half a unit is not a unit"
    assert two.feed(pieces[1:]) == [], (
        "the last fragment is complete only when something follows it")
    assert two.flush() == [nal], "an interrupted fragment is still assembled"
    assert two.feed([b"\x41\x99"]) == [b"\x41\x99"], "state must not leak forward"


def test_a_picture_with_no_headers_of_its_own_keeps_the_cache(pictures):
    """ffmpeg emits the parameter sets once, at the head of the stream. A cache
    replaced by whatever the current unit held is empty again by the third
    picture - measured on a real 14-frame encode - and every picture after the
    first was then undecodable."""
    cache = video._remember_headers(pictures[0], [])
    assert [video.nal_type(u) for u in cache] == [video.SPS, video.PPS], (
        "the keyframe's parameter sets should be the whole cache")
    for unit in pictures[1:]:
        cache = video._remember_headers(unit, cache)
        assert [video.nal_type(u) for u in cache] == [video.SPS, video.PPS], (
            "a picture with no headers of its own emptied the cache")


def test_the_newest_parameter_set_wins_and_sequence_still_comes_first(pictures):
    newer = b"\x67\x99" + bytes(8)
    cache = video._remember_headers(pictures[0], [])
    cache = video._remember_headers([b"\x09\x10", newer, pictures[0][2]], cache)
    assert cache[0] == newer, "the newest sequence header should win"
    assert [video.nal_type(u) for u in cache] == [video.SPS, video.PPS], (
        "a decoder handed a picture header before the sequence header it belongs "
        "to has nothing to decode it with")


def test_a_keyframe_rides_with_its_headers_and_a_delta_does_not(pictures):
    keyframe = [u for u in pictures if _has_keyframe(u)][0]
    delta = [u for u in pictures if not _has_keyframe(u)][0]
    headers = [u for u in keyframe if video.nal_type(u) in (video.SPS, video.PPS)]
    assert video.with_headers(keyframe, headers)[:2] == headers, (
        "a keyframe should carry what it is decoded with")
    assert video.with_headers(delta[:1], headers) == delta[:1], (
        "a delta means nothing without the frame it refers to, so headers on "
        "one are bytes spent on nothing")


def test_a_quiet_moment_between_packets_does_not_discard_the_picture():
    """`_recv_loop`'s 0.2 s timeout is not an end of frame. A sender at 15 fps
    with 1.2 kbit packets pauses for longer than it between access units, and
    discarding the units waiting for their delimiter is what once made 23
    packets arrive and the decoder receive nothing."""
    session = video.VideoRtpSession.bind("127.0.0.1")
    delivered = []
    try:
        peer = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        peer.bind(("127.0.0.1", 0))
        session.start(("127.0.0.1", peer.getsockname()[1]))
        original = session._deliver
        session._deliver = lambda units: (delivered.append(list(units)),
                                          original(units))
        slice_nal = b"\x65\x01\x02"
        # One NAL per RTP packet, which is what a sender does unless it is
        # aggregating them into a STAP-A.
        peer.sendto(session._packet(b"\x09\x10"), ("127.0.0.1", session.port))
        peer.sendto(session._packet(slice_nal), ("127.0.0.1", session.port))
        time.sleep(0.6)                    # wider than the reassembly timeout
        peer.sendto(session._packet(b"\x09\x10"), ("127.0.0.1", session.port))
        peer.sendto(session._packet(b"\x41\x99", marker=True),
                    ("127.0.0.1", session.port))
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline and not delivered:
            time.sleep(0.05)
    finally:
        session.stop()
    assert delivered, "the quiet moment threw the picture away"
    assert delivered[0][-1] == slice_nal, (
        f"the units that survived were {delivered[0]}, not the picture that arrived")


def test_a_receiving_session_asks_for_a_keyframe_it_cannot_decode_without():
    """`rtcp_keyframe` had no caller, so a decoder joining mid-stream waited out
    the far end's whole keyframe interval. Measured on the loopback: 141 pictures
    arrived before the first decodable one did."""
    decoder = video.Decoder(64, 36)
    session = video.VideoRtpSession.bind("127.0.0.1", decoder=decoder)
    try:
        peer = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        peer.bind(("127.0.0.1", 0))
        session.start(("127.0.0.1", peer.getsockname()[1]),
                    rtcp_port=peer.getsockname()[1])
        deadline = time.monotonic() + 3
        asked = None
        while time.monotonic() < deadline and asked is None:
            peer.settimeout(0.2)
            try:
                asked, _addr = peer.recvfrom(2048)
            except socket.timeout:
                continue
    finally:
        session.stop()
        decoder.stop()
    assert asked, "the receiving session never asked for a keyframe"
    assert asked[1] == video.PT_FIR, (
        f"a PLI arrives as payload type {video.PT_FIR}, this was {asked[1]}")


def test_a_session_with_no_decoder_never_asks_for_a_keyframe():
    """A sending session is the one that *answers* a keyframe request; asking for
    one itself is the ping-pong the two flags are kept apart to prevent."""
    session = video.VideoRtpSession.bind("127.0.0.1")
    try:
        peer = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        peer.bind(("127.0.0.1", 0))
        session.start(("127.0.0.1", peer.getsockname()[1]),
                    rtcp_port=peer.getsockname()[1])
        time.sleep(0.6)
        peer.settimeout(0.1)
        with pytest.raises(socket.timeout):
            peer.recvfrom(2048)
        assert not session._keyframe_asked, "asked anyway, with no decoder to feed"
    finally:
        session.stop()
