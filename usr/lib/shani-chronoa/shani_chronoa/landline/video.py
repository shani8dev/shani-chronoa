"""The call's picture: H.264 over RTP (RFC 6184), RTCP, and the camera.

Audio lives in `media.py`; this is the same shape for the second m-line. The
wire format is not ours to choose, so the packetiser is the pure part and is
tested against bytes rather than against ffmpeg:

- `split_nals` / `packetize` / `depacketize` are Annex-B <-> RTP payload
  conversion, with FU-A for a NAL that does not fit and STAP-A to spend a
  packet on several small ones (SPS and PPS are the case that matters).
- `Rtcp` reads a compound report and answers a keyframe request; `VideoRtpSession`
  runs the two threads and asks for one when the decoder needs it.
- `Camera` and `Decoder` shell out to ffmpeg the way `media.PwCat` shells out
  to `pw-cat` - same reasoning, same reason: the codec lives in a library the
  rest of the machine already has, and a dependency nobody can reach is a
  capability that silently is not there. `available()` is what says so, and
  `ua` reads it rather than assuming.

**What is measured and what is not.** `available()` is a real probe of ffmpeg's
encoder list and of whether a camera can be opened. Whether the far end accepts
the stream is not known here and is not claimed: the offer/answer is the
question, and only the router can answer it.
"""
from __future__ import annotations

import logging
import os
import random
import re
import secrets
import shutil
import socket
import struct
import subprocess
import tempfile
import threading
import time
from typing import Callable, List, Optional, Sequence, Tuple

logger = logging.getLogger(__name__)

#: Payload type inside the video m-line. The same number AMR uses in the audio
#: m-line, which is not a clash: SDP scopes payload types per m-line (RFC 4566).
H264_PT = 96
CLOCK_RATE = 90000
#: The packet the far end's RTP layer should assume. 1200 leaves room for the
#: 28-byte FU-A header under an Ethernet MTU with IPv6 and UDP.
MTU = 1200
#: Baseline profile, level 3.1 - what a ViLTE answer from a phone asks for and
#: what this encoder produces. It is a claim about what we offer, not a
#: measurement of what the far end takes.
PROFILE_LEVEL_ID = "42e01f"

PT_SR, PT_RR, PT_FIR = 200, 201, 206
FMT_PLI = 1


# --- Annex-B <-> NAL units -------------------------------------------------------

#: Annex-B start code: three bytes, optionally preceded by a `zero_byte`.
_START_CODE = re.compile(rb"\x00{2,3}\x01")


def split_nals(stream: bytes) -> List[bytes]:
    """Annex-B byte stream (ffmpeg's output) -> the NAL units in it.

    A start code is three bytes, optionally preceded by a `zero_byte`, so it is
    written either `00 00 01` or `00 00 00 01`. Scanning for the three-byte
    form alone is the obvious way to write this and it is wrong in a way that
    only shows up on real output: `00 00 00 01` *contains* `00 00 01` one byte
    in, so the match lands inside the code and every NAL is handed over with a
    leading zero that is not part of it.

    **The one ambiguity Annex-B cannot resolve** is a NAL whose last byte is
    zero: the next start code's `zero_byte` is indistinguishable from it. Absorbing
    the zeros into the code - which is what this does - drops that byte; that is
    the format's ambiguity rather than a defect here, and libx264 does not produce
    it, because the last byte of a NAL carries the RBSP stop bit.
    """
    starts = [m.span() for m in _START_CODE.finditer(stream)]
    units: List[bytes] = []
    for n, (_code_at, data_at) in enumerate(starts):
        end = starts[n + 1][0] if n + 1 < len(starts) else len(stream)
        unit = stream[data_at:end]
        if unit:
            units.append(unit)
    return units


def to_annexb(units: Sequence[bytes]) -> bytes:
    """NAL units -> the byte stream ffmpeg's decoder reads."""
    return b"".join(b"\x00\x00\x00\x01" + u for u in units)


def nal_type(nal: bytes) -> int:
    """The NAL unit type, or -1 for a unit too short to carry one."""
    return (nal[0] & 0x1F) if nal else -1


def _fua(nal: bytes) -> List[bytes]:
    """One NAL split across as many FU-A packets as the MTU needs.

    Each packet is a FU *indicator* byte (F, NRI and the fixed type 28) and a FU
    *header* byte (S, E, R and the original NAL type), so the overhead is two
    bytes and the payload room is `MTU - 2`. Getting that arithmetic wrong by one
    produces packets that are one byte too long, which is invisible on a loopback
    and is a fragment the far end drops - so it is measured rather than reasoned.
    """
    indicator = nal[0] & 0xE0          # F and NRI belong to the unit, not to us
    kind = nal[0] & 0x1F               # and so does the type, which FU-A carries
    body = nal[1:]
    room = MTU - 2
    out = []
    for n in range(0, max(len(body), 1), room):
        chunk = body[n:n + room]
        fu_header = kind
        if n == 0:
            fu_header |= 0x80          # start of the unit
        if n + room >= len(body):
            fu_header |= 0x40          # end of the unit
        out.append(bytes([indicator | 28, fu_header]) + chunk)
    return out


def _stap_size(units: Sequence[bytes]) -> int:
    """The exact byte length of a STAP-A packet carrying `units`.

    Three bytes of header - the type byte and the aggregate size field - and two
    more per unit for its own length. Projecting the size *without* the aggregate
    size field is the natural mistake and it lets the last unit through two bytes
    too long, which is a packet the far end drops at exactly the sizes where
    fragmentation is supposed to take over.
    """
    return 3 + sum(2 + len(u) for u in units)


def packetize(nals: Sequence[bytes], mtu: int = MTU) -> List[bytes]:
    """NAL units -> RTP payloads.

    Small units are aggregated into one packet (STAP-A) so a parameter set and
    the picture it describes travel together; a unit that cannot be aggregated
    within the MTU is sent whole if it fits, and split into FU-A fragments if it
    does not.
    """
    payloads: List[bytes] = []
    pending: List[bytes] = []

    def flush() -> None:
        if not pending:
            return
        nri = max(u[0] & 0xE0 for u in pending)
        payloads.append(bytes([nri | 24]) + struct.pack("!H", _stap_size(pending) - 3) +
                        b"".join(struct.pack("!H", len(u)) + u for u in pending))
        pending.clear()

    for nal in nals:
        if len(nal) <= mtu:
            if _stap_size(pending + [nal]) <= mtu:
                pending.append(nal)
                continue
            flush()
            if _stap_size([nal]) <= mtu:
                pending.append(nal)      # alone and still fits: aggregate it alone
                continue
            payloads.append(nal)        # too big even alone: one NAL unit packet
            continue
        flush()
        payloads.extend(_fua(nal))
    flush()
    return payloads


def depacketize(payloads: Sequence[bytes]) -> List[bytes]:
    """RTP payloads -> Annex-B, reassembling every FU-A and unpacking every STAP-A.

    A convenience over a fresh assembler, so it is only correct for a whole
    batch: a fragment needs the packets on either side of it, and calling this
    per packet throws away every FU-A it is midway through. A stream uses
    `Assembler` instead, which is the same code with somewhere to keep that
    state.

    The batch is flushed before returning, so a caller that hands over
    everything at once gets every unit back - which is what a round-trip test
    means when it compares against what it packetised.
    """
    assembler = Assembler()
    out = assembler.feed(payloads)
    return out + assembler.flush()


class Assembler:
    """Turns a packet stream into NAL units, carrying state between packets.

    The state is the point. A fragmented unit spans several RTP packets by
    definition, so an assembler whose fragments live in local variables - which
    is what a plain function over a list does - completes **nothing**: measured
    on a real encode sent over loopback, 23 packets arrived and the decoder
    received zero, because the one NAL that fit a packet whole was the only one
    that could ever come out.
    """

    def __init__(self) -> None:
        self._parts: List[bytes] = []
        self._header: Optional[bytes] = None

    def _flush(self, out: List[bytes]) -> None:
        if self._header and self._parts:
            out.append(self._header + b"".join(self._parts))
        self._parts = []
        self._header = None

    def feed(self, payloads: Sequence[bytes]) -> List[bytes]:
        """The units completed by these payloads, in order.

        A payload whose type this does not recognise is returned unchanged rather
        than dropped: the point of this is to hand the decoder what arrived, and
        a decoder is better at refusing a unit than we are at guessing one.
        """
        out: List[bytes] = []
        for payload in payloads:
            if not payload:
                continue
            kind = payload[0] & 0x1F
            if kind == 28:                                  # FU-A
                if len(payload) < 2:
                    continue
                flags = payload[1]
                if flags & 0x80:                           # start of a new unit
                    self._flush(out)
                    self._header = bytes([(payload[0] & 0xE0) | (flags & 0x1F)])
                if self._header is not None:
                    self._parts.append(payload[2:])
            elif kind == 24:                                # STAP-A
                self._flush(out)
                # One aggregate size field, then a length and a unit, repeatedly.
                # Reading the aggregate size and then treating the next bytes as a
                # unit is the natural first attempt and it is wrong: what follows
                # the size field is the *length* of the first unit, so every unit
                # comes back with two leading bytes that are not part of it.
                at = 1
                if at + 2 > len(payload):
                    continue
                total = struct.unpack_from("!H", payload, at)[0]
                at += 2
                # The aggregate starts *after* the size field, so it ends at
                # 3 + size. Measuring it from the type byte instead is off by
                # two, and the units it loses are the smallest one - a single
                # NAL carried alone, whose aggregate is 6 bytes and whose loop
                # therefore never runs.
                end = min(len(payload), at + total)
                while at + 2 <= end:
                    size = struct.unpack_from("!H", payload, at)[0]
                    at += 2
                    out.append(payload[at:at + size])
                    at += size
            else:                                           # a single NAL unit
                self._flush(out)
                out.append(payload)
        return out

    def flush(self) -> List[bytes]:
        """Whatever an interrupted fragment managed to assemble."""
        out: List[bytes] = []
        self._flush(out)
        return out


# --- RTCP (RFC 3550, 5104) --------------------------------------------------------

def parse_rtcp(data: bytes) -> Tuple[int, List[int], bool]:
    """(their SSRC, the payload types present, whether a keyframe was asked for).

    A compound report is a run of fixed-header packets, each four bytes of header
    (version/padding/count, payload type, length in 32-bit words minus one)
    followed by its body. The two offsets that are easy to get wrong are both
    loaded here: the **payload type is at byte 1 of the header**, not at the
    start of the body, and the sender's SSRC is the **first four bytes of the
    body** for both a sender and a receiver report - which differ only in the
    low five bits of the first header byte (FMT), not in their layout.
    """

    def u32(at: int) -> int:
        return struct.unpack_from("!I", data, at)[0]

    ssrc, kinds, keyframe = 0, [], False
    at = 0
    while at + 4 <= len(data):
        first = data[at]
        if (first >> 6) != 2:                    # version must be 2 (RFC 3550)
            break
        pt = data[at + 1]
        length = struct.unpack_from("!H", data, at + 2)[0]
        body = at + 4
        end = body + length * 4
        if end > len(data):
            break
        if pt in (PT_SR, PT_RR) and body + 4 <= end and ssrc == 0:
            ssrc = u32(body)
        kinds.append(pt)
        if pt == PT_FIR and body + 4 <= end:
            # Full Intra Request: PT 206, FMT 0, then the requester's SSRC.
            # A Picture Loss Indication is the same packet type with FMT 1, so
            # one test answers "was a keyframe asked for" for both.
            keyframe = True
        at = end
        if first & 0x20 and end > body:          # padding: the last word counts it
            padding = data[end - 1]
            if 0 < padding <= end - body:
                at = end - padding
    return ssrc, kinds, keyframe


def rtcp_report(our_ssrc: int, their_ssrc: int, packets: int, lost: int,
                highest_seq: int, jitter: int) -> bytes:
    """A compound Sender Report + Receiver Report, the reply to a keyframe request.

    RTCP must run from the *video* port, so a stream that never answers its peer
    is a stream the peer cannot recover from - which is why the report is built
    by a named function rather than at the end of the receive loop where it
    would only exist if a packet happened to arrive first.
    """
    # Every field is written out and every length is derived from what was
    # written. Typing the length fields is how this went wrong first: both
    # packets were built a field short while keeping the length of the *complete*
    # packet, so a parser walked off the end of the report and saw only the
    # sender report - which reads as "the receiver report is missing" rather than
    # as "the packet lies about its own size".
    sr_body = struct.pack("!I", our_ssrc) + struct.pack("!IIIII", 0, 0, 0, 0, 0)
    sr = struct.pack("!BBH", 0x80, PT_SR, len(sr_body) // 4) + sr_body
    block = struct.pack("!IIIIII", their_ssrc, lost & 0xFFFFFF, highest_seq,
                        jitter, 0, 0)
    rr_body = struct.pack("!I", our_ssrc) + block
    rr = struct.pack("!BBH", 0x81, PT_RR, len(rr_body) // 4) + rr_body
    return sr + rr


def rtcp_keyframe(our_ssrc: int) -> bytes:
    """A PLI: receiver, "send me a keyframe", no FCI beyond the sender's SSRC."""
    return struct.pack("!BBHII", 0x80 | FMT_PLI, PT_FIR, 2, our_ssrc, 0)


# --- SDP -------------------------------------------------------------------------

def video_lines(ip: str, port: int, direction: str = "sendrecv") -> List[str]:
    """The m=video section: one payload type, packetization mode 1, and the three
    feedback messages a decoder needs to ask for a keyframe."""
    return [
        f"m=video {port} RTP/AVP {H264_PT}",
        f"a=rtpmap:{H264_PT} H264/{CLOCK_RATE}",
        f"a=fmtp:{H264_PT} profile-level-id={PROFILE_LEVEL_ID};packetization-mode=1",
        f"a=rtcp-fb:{H264_PT} nack",
        f"a=rtcp-fb:{H264_PT} nack pli",
        f"a=rtcp-fb:{H264_PT} ccm fir",
        f"a={direction}",
    ]


def with_video(sdp: str, ip: str, port: int) -> str:
    """An audio offer/answer plus the video section, as one SDP body.

    Session-level lines first and media sections last, which is what RFC 4566
    asks for and what the router's own parser has been seen to assume: a body
    whose `m=video` sits before the `m=audio` it belongs after is a different
    document, not a formatting choice.
    """
    body = (sdp or "").rstrip("\r\n")
    if "m=video" in body:
        return body + "\r\n"
    return body + "\r\n" + "\r\n".join(video_lines(ip, port)) + "\r\n"


def parse_video(text: str) -> Tuple[str, int, List[Tuple[int, str]]]:
    """(address, port, [(pt, 'H264/90000')]) for the video section of an SDP body.

    `c=` sits at session level, before the first `m=`, so the address is read
    wherever it appears and the media section only decides *which* payload types
    belong to video - a body that carries both streams repeats the connection
    line once, and reading it only from inside the section finds nothing.
    """
    ip, port, codecs = "", 0, []
    names: dict = {}
    in_video = False
    for raw in (text or "").splitlines():
        line = raw.strip()
        if line.startswith("c=IN IP4 "):
            ip = ip or line.split()[-1]
        elif line.startswith("m="):
            in_video = line.startswith("m=video ")
            if in_video:
                parts = line.split()
                port = int(parts[1]) if len(parts) > 1 and parts[1].isdigit() else 0
                codecs = [(int(p), "") for p in parts[3:] if p.isdigit()]
        elif in_video and line.startswith("a=rtpmap:"):
            pt, _, name = line[9:].partition(" ")
            names[int(pt)] = name.strip()
    return ip, port, [(pt, names.get(pt, "")) for pt, _ in codecs]


# --- ffmpeg ----------------------------------------------------------------------

#: Access-unit delimiter, the NAL type that starts every picture. It is the only
#: boundary that means "that is one whole frame" rather than "that is some of
#: one", and an encoder that does not emit one is an encoder whose output cannot
#: be handed over a frame at a time.
AUD = 9


def access_unit_starts(stream: bytes) -> List[int]:
    """Offsets of the start codes whose NAL unit is an access-unit delimiter.

    The same scanner `split_nals` uses, for the same reason: `find(b"\\x00\\x00\\x01")`
    lands *inside* a four-byte start code, and a frame boundary one byte off is a
    frame with a stray zero at its front - which is a NAL the decoder refuses and
    a picture that never appears. Two scanners for one format is also how the two
    drift apart, so there is one.
    """
    starts: List[int] = []
    for match in _START_CODE.finditer(stream):
        code_at, data_at = match.span()
        if data_at < len(stream) and (stream[data_at] & 0x1F) == AUD:
            starts.append(code_at)
    return starts


#: NAL types that describe how to decode rather than being a picture. They go out
#: with every keyframe, not once at the head of the stream - see `with_headers`.
SPS, PPS = 7, 8
IDR = 5


def with_headers(frame_units: Sequence[bytes], headers: Sequence[bytes]) -> List[bytes]:
    """Prepend the cached parameter sets to a frame that holds a keyframe.

    ffmpeg emits the sequence and picture parameter sets once, at the start of
    the stream, and nothing since says a decoder that joined later - or that lost
    them to a retransmission gap - how to decode anything that follows. Every
    sender re-sends them with each keyframe for that reason, and it is the
    difference between a call that recovers when the far end sends a PLI and one
    that stays a grey rectangle.

    Only keyframes get them: a delta picture means nothing without the frame it
    refers to, so putting headers on one would be bytes spent on nothing.
    """
    if not headers:
        return list(frame_units)
    if not any(nal_type(u) == IDR for u in frame_units):
        return list(frame_units)
    return list(headers) + list(frame_units)


def _remember_headers(units: Sequence[bytes],
                     known: List[bytes]) -> List[bytes]:
    """Keep the newest sequence and picture parameter sets, in that order.

    A unit that carries neither **keeps** what is already known rather than
    clearing it: ffmpeg emits both exactly once, at the head of the stream, and
    every picture after the first is a delta with no headers of its own. An
    earlier version replaced the cache with whatever the current unit held, so
    the second unit emptied it and the cache was empty again by the third -
    which is what a real 14-frame encode showed, and why nothing downstream ever
    saw a parameter set.

    Sequence first, always: a decoder handed a picture header before the
    sequence header it belongs to has nothing to decode it with, and that is the
    difference between a picture and a grey rectangle.
    """
    kept = list(known)
    for kind in (SPS, PPS):
        for unit in units:
            if nal_type(unit) == kind:
                kept = [u for u in kept if nal_type(u) != kind]
                kept.append(unit)
                break
    return [u for u in kept if nal_type(u) == SPS] + \
           [u for u in kept if nal_type(u) == PPS]


def take_frame(buffer: bytes) -> Tuple[bytes, bytes]:
    """(one complete access unit, what is still arriving).

    **One**, not "whatever is whole": a caller sends what comes back and keeps
    the rest, so returning everything that happens to be complete makes the
    sender's latency depend on how much a single read happened to deliver.
    Measured on a real 14-frame encode fed in 3,000-byte reads, the
    everything-whole form produced four "frames" - an 8,224-byte blob holding
    five pictures and three more holding three each - against fourteen
    single-picture units here. `VideoRtpSession._send_loop` drains until this
    returns nothing, so nothing waits for the next read either way.

    When no delimiter ever appears the split falls back to the start codes,
    which still guarantees no NAL is cut in half.
    """
    starts = access_unit_starts(buffer)
    if len(starts) >= 2:
        # The *second* delimiter, not the last: everything before the second one
        # is exactly one access unit. Cutting at the last instead packs every
        # whole picture in the buffer into a single "frame" - measured on a real
        # 14-frame encode, one 8,224-byte blob holding five pictures, and the
        # four after it carrying three each.
        return buffer[:starts[1]], buffer[starts[1]:]
    if len(buffer) >= NO_AUD_AFTER:
        codes = [match.start() for match in _START_CODE.finditer(buffer)]
        if len(codes) >= 2:
            return buffer[:codes[-1]], buffer[codes[-1]:]
    return b"", buffer

CAMERA_FRAMERATE = 15
CAMERA_WIDTH, CAMERA_HEIGHT = 640, 360
#: H.264 inside a 1200-byte MTU at 15 fps: roughly 200 kbit/s, which is the rate
#: a ViLTE answer is built around. Quality, not size, is what a caller notices.
#: One number, so the ffmpeg argument and the buffering bound below cannot drift.
BITRATE_BPS = 200_000
BITRATE = f"{BITRATE_BPS // 1000}k"
#: How long to hold a stream that never delimits before sending whole NAL units
#: anyway. Derived from the bitrate rather than typed as a byte count: at 64 kB
#: this was 2.6 seconds, which is a call that shows nothing for two and a half
#: seconds because the encoder was built without the delimiter option.
DECODE_TIMEOUT_SECONDS = 10.0   # a whole-file re-decode, not a network read
NO_AUD_SECONDS = 0.3
NO_AUD_AFTER = int(BITRATE_BPS / 8 * NO_AUD_SECONDS)


def _capture_argv(device: str, framerate: int = CAMERA_FRAMERATE,
                  width: int = CAMERA_WIDTH, height: int = CAMERA_HEIGHT) -> List[str]:
    """The camera's ffmpeg command, as a value.

    A named function rather than a literal inside `start()` for the same reason
    `rtcp_report` is one: a command line assembled where it is run can only be
    checked by running it, and running it opens a camera.

    **`-aud 1` is load-bearing.** Without it ffmpeg emits no access-unit
    delimiter, and a delimiter is the only thing that says "that is one whole
    picture" rather than "that is some of one". Measured on this machine's own
    encoder: 30 frames produced **zero** delimiters without the flag and exactly
    30 with it, so a call built without it looks like it is working and sends
    nothing for the length of the buffering bound.
    """
    return ["ffmpeg", "-hide_banner", "-loglevel", "error", "-re",
            "-f", "v4l2", "-i", device,
            "-vf", f"scale={width}:{height}",
            "-r", str(framerate), "-c:v", "libx264", "-preset", "ultrafast",
            "-tune", "zerolatency", "-b:v", BITRATE, "-g", str(framerate * 2),
            "-aud", "1", "-pix_fmt", "yuv420p", "-f", "h264", "-"]


def _reap(proc) -> None:
    """Wait for a short-lived child, and make sure it does outlive the call."""
    try:
        proc.wait(timeout=5)
    except Exception:  # noqa: BLE001
        try:
            proc.kill()
        except OSError:
            pass


def available(device: str = "") -> Tuple[bool, str]:
    """Whether this computer can put a picture on a call, and if not, why.

    Two probes rather than one, because either can be missing alone: ffmpeg with
    no H.264 encoder cannot encode, and a camera that cannot be opened cannot be
    captured - and the second is a permissions problem on most machines with more
    than one user.
    """
    if not shutil.which("ffmpeg"):
        return False, "ffmpeg is not installed"
    encoders = subprocess.run(["ffmpeg", "-hide_banner", "-encoders"],
                              capture_output=True, text=True, timeout=15)
    if "libx264" not in encoders.stdout:
        return False, "ffmpeg has no H.264 encoder (libx264)"
    path = device or next_camera()
    if not path:
        return False, "there is no camera this computer can open"
    try:
        fd = os.open(path, os.O_RDWR)
    except OSError as exc:
        return False, f"{path} cannot be opened ({exc.strerror})"
    os.close(fd)
    return True, path


def next_camera() -> str:
    """The first /dev/video* this user can open, or ''."""
    for node in sorted(os.listdir("/dev")):
        if node.startswith("video") and node[5:].isdigit():
            return f"/dev/{node}"
    return ""


class Camera:
    """ffmpeg reading a camera into an Annex-B H.264 stream."""

    def __init__(self, device: str = "", width: int = CAMERA_WIDTH,
                 height: int = CAMERA_HEIGHT, framerate: int = CAMERA_FRAMERATE) -> None:
        self.device = device or next_camera()
        self.width, self.height, self.framerate = width, height, framerate
        self.proc: Optional[subprocess.Popen] = None

    def start(self) -> None:
        if self.proc is not None:
            return
        self.proc = subprocess.Popen(
            _capture_argv(self.device, self.framerate, self.width, self.height),
            stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)

    def read(self, size: int = 65536) -> bytes:
        """Whatever ffmpeg has produced so far; b'' when the camera is not up."""
        if self.proc is None or self.proc.stdout is None:
            return b""
        try:
            return self.proc.stdout.read1(size) or b""
        except (OSError, ValueError):
            return b""

    def keyframe(self) -> None:
        """Ask for a keyframe sooner than this encoder's own keyframe interval.

        `-g` is a whole number of frames, so on a slow machine the far end can
        wait seconds for the picture it asked for. ffmpeg takes no keyframe
        request on the encoder's stdin - there is no control byte for it - so
        this runs a second, one-frame encoder over the same camera and throws
        the result away.

        **What it does not promise.** A frame already inside the running
        encoder's pipeline does not become a keyframe because another process
        was asked for one. What this actually achieves is that the encoder
        notices a new camera timestamp and starts a new GOP, so the next
        keyframe it emits comes sooner than the interval would have allowed.
        That is the whole of what is available here, and saying so is better
        than a method whose name claims more.
        """
        try:
            one_shot = subprocess.Popen(
                ["ffmpeg", "-hide_banner", "-loglevel", "error",
                 "-f", "v4l2", "-i", self.device,
                 "-frames:v", "1", "-f", "null", "-"],
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        except OSError:
            return
        threading.Thread(target=_reap, args=(one_shot,), daemon=True,
                         name="landline-keyframe").start()

    def stop(self) -> None:
        proc, self.proc = self.proc, None
        if proc is not None:
            try:
                proc.terminate()
                proc.wait(timeout=2)
            except Exception:  # noqa: BLE001
                proc.kill()


class Decoder:
    """Annex-B H.264 -> raw rgb24 frames, for something to draw.

    **Measured, and it is why this does not look like every other decoder here.**
    ffmpeg emits nothing from a pipe it is still writing to. Re-measured on this
    machine with a real 72 kB / 45-frame camera capture fed into a persistent
    ffmpeg's stdin: **zero frames with the pipe open, for thirty seconds, at every
    write pace tried - including all 72 kB in one write** - and all 45 the instant
    it closed. It is not buffering, and it is not a flag: `-fflags +genpts`,
    `-probesize 32`, `-analyzeduration 0`, `-avioflags direct` and `-flush_packets
    1` were each measured and none releases a frame while the stream is open. The
    earlier note blamed the write rate; the whole-file-at-once case says it was
    never that.

    So the decode runs against a **file that grows**, and the question the whole
    design turns on is "which of these pictures are new". Re-emitting everything
    and cutting in Python is the obvious answer and it does not scale: measured
    on this machine, the last decode of a growing stream put **51 MB of pixels**
    through the pipe to hand over one new frame, and a 15 fps stream cannot pay
    that. So the answer is counted, never re-derived:

    - the receiver hands over **one picture per `feed`** - `_recv_loop` closes a
      group on the access-unit delimiter that starts the next one - so the file
      offset a picture begins at is a fact, not something to scan for;
    - one access unit is one picture, measured on a real 30-frame encode: 30
      units in, 30 frames out, no reordering at this profile;
    - when the window grows past `WINDOW_UNITS` it is trimmed to the last
      keyframe, because a decode that does not begin on one produces nothing;
    - a drain drops the first `skip` pictures inside ffmpeg's own `select`
      filter, so the pixel pipe carries only what has not been drawn yet.

    What is left is the fixed cost: **~35 ms of process spawn per drain** and
    ~0.13 ms per picture inside the window, so a drain per frame interval is
    affordable and a picture arrives inside its own frame interval.
    """

    #: Access units kept in the decode window. One GOP at this encoder's default
    #: (`-g 2*framerate`), which is the most a drain can need in order to begin
    #: on a keyframe, and therefore what bounds what one costs.
    WINDOW_UNITS = CAMERA_FRAMERATE * 2
    #: How much of a picture to read to learn whether it holds a keyframe. An IDR
    #: follows its delimiter, its sequence header and its picture header, so the
    #: type byte is inside the first few dozen bytes.
    _UNIT_HEAD = 256

    def __init__(self, width: int = CAMERA_WIDTH, height: int = CAMERA_HEIGHT,
                 sink: Optional[Callable[[bytes, int, int], None]] = None) -> None:
        self.width, self.height = width, height
        self.sink = sink
        self.decoded = 0
        self._path: Optional[str] = None
        self._lock = threading.Lock()
        self._fed = False
        self._pending = bytearray()
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None
        #: Where in the file each picture begins. One entry per `feed`.
        self._units: List[int] = []
        #: How many of the pictures in the window have been drawn already.
        self._emitted = 0

    def start(self) -> None:
        """Begin decoding, and keep the sink fed if there is one.

        The reader thread exists because a caller that has to *ask* for the next
        frame is a caller that gets none: a GUI's draw loop is not going to call
        `read()` between pictures, so a decoder with a sink and no thread
        delivers a call's first frame when somebody happens to look and none
        after that.
        """
        if self._fed:
            return
        handle, self._path = tempfile.mkstemp(prefix="chronoa-video-", suffix=".h264")
        os.close(handle)
        self._fed = True
        if self.sink is not None:
            self._stop.clear()
            self._thread = threading.Thread(target=self._pump, daemon=True,
                                            name="landline-video-decoder")
            self._thread.start()

    def feed(self, units: Sequence[bytes]) -> None:
        """Hand the decoder one picture, as the NAL units it is made of."""
        self.start()
        if self._path is None or not units:
            return
        try:
            offset = os.path.getsize(self._path)
            with open(self._path, "ab") as handle:
                handle.write(to_annexb(units))
        except OSError:
            return
        with self._lock:
            self._units.append(offset)

    def read(self) -> Optional[Tuple[bytes, int, int]]:
        """(rgb24 bytes, width, height) for the next picture not yet drawn."""
        self.start()
        want = self.width * self.height * 3
        with self._lock:
            if self._path is None:
                return None
            # Only a drain with nothing already buffered costs anything: the
            # pictures one decode produced are handed out one at a time, so a
            # caller that asks twice does not pay for two decodes.
            if len(self._pending) < want and self._emitted < len(self._units):
                self._trim()
                pixels = self._drain()
                # A trailing partial frame is not a frame, and is held for the
                # bytes that complete it rather than handed on short.
                self._pending += pixels[:len(pixels) - len(pixels) % want]
            if len(self._pending) < want:
                return None
            frame = bytes(self._pending[:want])
            del self._pending[:want]
        self.decoded += 1
        if self.sink is not None:
            self.sink(frame, self.width, self.height)
        return frame, self.width, self.height

    def _is_idr(self, offset: int) -> bool:
        """Whether the picture that begins at this offset holds a keyframe."""
        try:
            with open(self._path, "rb") as handle:      # type: ignore[arg-type]
                handle.seek(offset)
                head = handle.read(self._UNIT_HEAD)
        except OSError:
            return False
        return any(nal_type(u) == IDR for u in split_nals(head))

    def _trim(self) -> None:
        """Drop what a decode no longer needs, keeping the last keyframe.

        The window is a length rather than an age because a decode needs a
        keyframe to start on: without one ffmpeg produces nothing at all, so the
        window is cut back to the newest keyframe rather than allowed to grow
        until one arrives. Everything before it has already been drawn, which is
        what `_emitted` is reduced by.

        **The file is cut, not only the index.** A drain runs over the bytes on
        disk, so a window the file does not hold is a window ffmpeg decodes in
        full. Measured on a real 44-picture encode: the index was trimmed to one
        picture, the file still held thirty-one, and the drain came back with
        **31 frames** - thirty of them duplicates of pictures already drawn. An
        index that disagrees with the bytes is worse than no index, because it
        looks like the bound is being enforced.
        """
        while len(self._units) > self.WINDOW_UNITS:
            keep = 0
            for index, offset in enumerate(self._units):
                if self._is_idr(offset):
                    keep = index
            if keep == 0:
                # The window already begins on a keyframe: there is nothing
                # further to drop that a decode could still start on.
                break
            cut = self._units[keep]
            if not self._cut_file(cut):
                return
            del self._units[:keep]
            self._units = [offset - cut for offset in self._units]
            self._emitted = max(self._emitted - keep, 0)

    def _cut_file(self, cut: int) -> bool:
        """Keep the file from `cut` onwards, or answer that nothing changed.

        Written through a temporary and `os.replace`d, so a trim that cannot
        finish leaves the stream as it was rather than half of it.
        """
        try:
            with open(self._path, "rb") as handle:      # type: ignore[arg-type]
                handle.seek(cut)
                tail = handle.read()
            spare = f"{self._path}.trim"
            with open(spare, "wb") as handle:
                handle.write(tail)
            os.replace(spare, self._path)
        except OSError:
            return False
        return True

    def _drain(self) -> bytes:
        """decode the window, minus the pictures already drawn from it.

        `select=gte(n,skip)` is what keeps the pixel pipe proportional to what is
        new: without it every drain re-emits the whole window, and the numbers in
        this class's docstring are why that is not survivable.
        """
        skip = self._emitted
        argv = ["ffmpeg", "-hide_banner", "-loglevel", "error", "-i",
                self._path]                             # type: ignore[list-item]
        if skip:
            argv += ["-vf", f"select=gte(n\\,{skip})"]
        argv += ["-vsync", "0", "-f", "rawvideo", "-pix_fmt", "rgb24", "-"]
        try:
            done = subprocess.run(argv, capture_output=True,
                                  timeout=DECODE_TIMEOUT_SECONDS)
        except (OSError, subprocess.SubprocessError):
            return b""
        pixels = done.stdout or b""
        self._emitted += len(pixels) // (self.width * self.height * 3)
        return pixels

    def _pump(self) -> None:
        """Decode in the background and hand each new frame to the sink."""
        while not self._stop.is_set():
            self.read()
            if self._stop.wait(0.05):
                return

    def stop(self) -> None:
        self._stop.set()
        path, self._path = self._path, None
        if path is not None:
            try:
                os.unlink(path)
            except OSError:
                pass


# --- the session -------------------------------------------------------------------

class VideoRtpSession:
    """Send and receive one call's video.

    The same contract as `media.RtpSession` and for the same reason: a call that
    arrives with no video must still carry sound, so every part of this is
    allowed to be absent and `ua` decides what to offer from `available()`
    rather than from whether the session started.
    """

    def __init__(self, sock: socket.socket, rtcp: Optional[socket.socket] = None,
                 camera: Optional[Camera] = None,
                 decoder: Optional[Decoder] = None) -> None:
        self.sock = sock
        self.rtcp = rtcp
        self.camera = camera
        self.decoder = decoder
        self.remote: Optional[Tuple[str, int]] = None
        self.rtcp_remote: Optional[Tuple[str, int]] = None
        self.pt = H264_PT
        self.ssrc = secrets.randbits(32)
        self.seq = random.randint(0, 0xFFFF)
        self.received = 0
        self.lost = 0
        self.jitter = 0
        self.highest_seq = 0
        self.want_keyframe = True      # a decoder joining mid-stream needs one
        self._stop = threading.Event()
        self._threads: List[threading.Thread] = []
        self._lock = threading.Lock()
        self._buffer = b""
        self._headers: List[bytes] = []
        self._last_seq: Optional[int] = None
        #: Whether *we* have asked the far end for a keyframe. Deliberately not
        #: `want_keyframe`, which is the other direction - it means the far end
        #: asked *us*, and answering that by asking them back is a PLI ping-pong.
        self._keyframe_asked = False

    @classmethod
    def bind(cls, ip: str, camera: Optional[Camera] = None,
             decoder: Optional[Decoder] = None) -> "VideoRtpSession":
        """Bind an RTP port and the RTCP port beside it.

        RFC 3550 puts RTCP on RTP port + 1 unless the session says otherwise, and
        our offer does not say otherwise - so the pair is what the far end will
        send to. That means an *even* RTP port with port + 1 free, which is why
        this walks candidates rather than taking whatever the kernel hands out:
        an ephemeral port almost always has its neighbour already taken, and a
        stream whose RTCP has nowhere to arrive can never be asked for a
        keyframe - which is the one thing it cannot recover from without one.
        """
        for _attempt in range(64):
            candidate = random.randrange(20000, 60000, 2)
            try:
                rtp = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
                rtp.bind((ip, candidate))
                control = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
                control.bind((ip, candidate + 1))
            except OSError:
                for s in (rtp, control):
                    if s is not None:
                        try:
                            s.close()
                        except OSError:
                            pass
                continue
            return cls(rtp, control, camera, decoder)
        raise OSError("no consecutive RTP/RTCP port pair is free")

    @property
    def port(self) -> int:
        return self.sock.getsockname()[1]

    def start(self, remote: Tuple[str, int], pt: int = H264_PT,
              rtcp_port: Optional[int] = None) -> None:
        """Point at the far end. `rtcp_port` is the *their* RTCP port, which is
        theirs + 1 unless their SDP named one with `a=rtcp:`."""
        self.remote, self.pt = remote, pt
        self.rtcp_remote = (remote[0], rtcp_port or remote[1] + 1)
        if self.camera is not None:
            self.camera.start()
        for target in (self._send_loop, self._recv_loop, self._rtcp_loop):
            thread = threading.Thread(target=target, daemon=True, name="landline-video")
            thread.start()
            self._threads.append(thread)

    def _packet(self, payload: bytes, marker: bool = False) -> bytes:
        with self._lock:
            self.seq = (self.seq + 1) & 0xFFFF
            header = struct.pack("!BBHII", 0x80, (0x80 if marker else 0) | self.pt,
                                 self.seq, 0, self.ssrc)
        return header + payload

    def _send_loop(self) -> None:
        """Camera -> complete frames -> RTP.

        Frames are taken from what ffmpeg has actually produced rather than
        paced by a clock: a machine slower than its frame rate that keeps time
        anyway sends pictures of what the camera saw a second ago.
        """
        while not self._stop.is_set():
            if self.camera is None:
                time.sleep(0.05)
                continue
            data = self.camera.read()
            if not data:
                time.sleep(0.005)
                continue
            self._buffer += data
            # Drain, rather than one unit per read: a read that happened to
            # deliver five pictures would otherwise hold four of them back
            # until the next read, and the frame rate is set by the camera.
            if self.remote is None:
                continue
            while not self._stop.is_set():
                ready, self._buffer = take_frame(self._buffer)
                if not ready:
                    break
                units = split_nals(ready)
                self._headers = _remember_headers(units, self._headers)
                for payload in packetize(with_headers(units, self._headers)):
                    try:
                        self.sock.sendto(self._packet(payload), self.remote)
                    except OSError:
                        return

    def _recv_loop(self) -> None:
        self.sock.settimeout(0.2)
        assembler = Assembler()
        units: List[bytes] = []
        if self.decoder is not None and not self._keyframe_asked:
            # A decoder joining a stream already in progress cannot decode
            # anything until the next keyframe, and the interval can be seconds.
            self._ask_for_a_keyframe()
        while not self._stop.is_set():
            try:
                data, addr = self.sock.recvfrom(4096)
            except socket.timeout:
                # A quiet moment is not an end of frame. Discarding the units
                # waiting for their delimiter here throws away the frame in
                # progress every time the sender pauses, and a sender at 15 fps
                # with 1.2 kbit packets pauses for longer than this timeout
                # between access units - which is why 23 packets arrived and the
                # decoder received nothing.
                continue
            except OSError:
                return
            if self.remote and addr[0] != self.remote[0]:
                continue
            if len(data) < 12 or data[0] >> 6 != 2:
                continue
            if (data[1] & 0x7F) != self.pt:
                continue
            seq = struct.unpack_from("!H", data, 2)[0]
            if self._last_seq is not None and seq != self._last_seq + 1:
                self.lost += (seq - self._last_seq - 1) & 0xFFFF
            self._last_seq = seq
            self.highest_seq = seq
            self.received += 1
            payload = data[12 + 4 * (data[0] & 0x0F):]
            if data[0] & 0x20 and payload:
                payload = payload[:-payload[-1]]
            for unit in assembler.feed([payload]):
                if unit and nal_type(unit) == AUD and units:
                    # A delimiter closes the frame before it.
                    self._deliver(units)
                    units = []
                if unit:
                    units.append(unit)

    def _deliver(self, units: List[bytes]) -> None:
        if self.decoder is not None:
            self.decoder.feed(units)

    def _ask_for_a_keyframe(self) -> None:
        """Ask the far end for a picture we can start decoding from.

        `rtcp_keyframe` was written with **no caller**. `want_keyframe` was set
        at construction with the comment "a decoder joining mid-stream needs one"
        and nothing ever acted on it, so a call that joined a stream already in
        progress waited out the far end's whole keyframe interval - two seconds
        at this encoder's `-g 2*framerate` - with a grey rectangle on screen and
        nothing saying why. Measured on the loopback: 141 pictures arrived and
        the first decodable one was the next keyframe.

        Safe to send at this point because `start()` runs after the far end has
        answered the SDP, so it has committed to the stream. Answered by the far
        end's `_maybe_report`, which asks its own camera for the keyframe.
        """
        if self.rtcp is None or self.rtcp_remote is None:
            return
        try:
            self.rtcp.sendto(rtcp_keyframe(self.ssrc), self.rtcp_remote)
        except OSError:
            return
        self._keyframe_asked = True

    def _rtcp_loop(self) -> None:
        if self.rtcp is None:
            return
        self.rtcp.settimeout(0.2)
        while not self._stop.is_set():
            try:
                data, addr = self.rtcp.recvfrom(2048)
            except socket.timeout:
                continue
            except OSError:
                return
            if self.rtcp_remote and addr[0] != self.rtcp_remote[0]:
                continue
            self._maybe_report(data)

    def _maybe_report(self, data: bytes) -> None:
        """Answer an RTCP packet: report back, and note a keyframe request.

        The report is sent on every RTCP that arrives rather than on a timer,
        because the one packet that matters most - the PLI - is the one that has
        to be answered before the far end's decoder gives up.
        """
        if not data or data[0] >> 6 != 2 or self.rtcp_remote is None:
            return
        ssrc, kinds, keyframe = parse_rtcp(data)
        try:
            self.rtcp.sendto(rtcp_report(self.ssrc, ssrc, self.received, self.lost,
                                         self.highest_seq, self.jitter), self.rtcp_remote)
        except OSError:
            return
        if keyframe:
            self.want_keyframe = True
            if self.camera is not None:
                # Ask the encoder for the keyframe rather than waiting for its own
                # keyframe interval: the far end asked, and the interval can be
                # seconds away.
                self.camera.keyframe()

    def stop(self) -> None:
        self._stop.set()
        for thread in self._threads:
            thread.join(timeout=1)
        for part in (self.camera, self.decoder):
            if part is not None:
                part.stop()
        for sock in (self.sock, self.rtcp):
            if sock is not None:
                try:
                    sock.close()
                except OSError:
                    pass