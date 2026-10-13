"""Which cameras this machine has, and which of them can answer a question.

**This module is the V4L2 probe in `harness-study/linux-hello-probe/probe.py`,
promoted out of a harvest study and into the package** because Chronoa had no way
to tell one `/dev/video*` node from another beyond the kernel's name. Measured on
this box against two real nodes: `/dev/video0` advertises `MJPG` and `YUYV` and can
capture; `/dev/video1` answers `QUERYCAP` with the same driver and the same card
name and **cannot capture video at all** - it is a UVC metadata node, and
`ffmpeg -f v4l2 -i /dev/video1` fails with
`ioctl(VIDIOC_G_INPUT): Inappropriate ioctl for device`.

**A node that cannot capture still answers "who are you".** `QUERYCAP` returns a
sensible driver and card for it, so a probe that stops there reports a metadata node
as a working camera. The cheap thing that separates them is the
`V4L2_CAP_VIDEO_CAPTURE` bit, or a refusal from `VIDIOC_ENUM_FMT`.

The transfer is from `kennycoder/linux-hello` (`harness-study/HARVEST-LINUX-HELLO.md`),
which does the same thing in `linux-hello-camera/src/discover.rs`. **Not taken from
it: any of the streaming.** Chronoa captures with ffmpeg and that stays.

### Three V4L2 facts, each of which cost a run and each of which fails silently

- **A borrowed `VIDIOC_ENUM_FMT` value is wrong.** `0xC0505602` - the one in every
  tutorial - encodes an **80-byte** `v4l2_fmtdesc`. The kernel compares the request's
  size bits against its own struct and answers `ENOTTY` on a mismatch, which reads
  exactly like *"this device does not support the ioctl"* and not like *"you asked the
  wrong size"*. **A wrong size is indistinguishable from an absent capability.** The
  number is derived below from the header's field list, and the test asserts the
  derivation against `/usr/include/linux/videodev2.h` when that file is present.
- **`pixelformat` is at offset 44, not 36.** `index`, `type` and `flags` are 4 bytes
  each and `description[32]` follows them. A wrong offset returns plausible garbage
  rather than an error, which is worse.
- **A loopback node with no writer refuses `ENUM_FMT` too.** Measured: a
  `v4l2loopback` device reports `can_capture: False` and `formats: []` until
  something writes to it, then reports `can_capture: True` and the writer's formats.
  So an empty result with a refusal is not proof of a node that cannot capture -
  `formats_error` is reported separately so the two stay distinguishable.
"""

import fcntl
import os
import struct
from typing import Dict, List

#: The header's own field list, so the size is derived rather than remembered:
#: index, type, flags (u32 x3) | description[32] | pixelformat, mbus_code (u32 x2)
#: | reserved[3] (u32 x3). 8*4 + 32 = 64 bytes.
_FMT_FIELDS = (("u32", 3), ("u8", 32), ("u32", 5))
_FMT_SIZE = sum(4 * n if kind == "u32" else n for kind, n in _FMT_FIELDS)
_FMT_REQUEST = (3 << 30) | (_FMT_SIZE << 16) | (ord("V") << 8) | 2
_FMT_DESCRIPTION = 12
_FMT_PIXELFORMAT = 4 * 3 + 32

#: `_IOR('V', 0, struct v4l2_capability)`: driver[16] card[32] bus_info[32] and six
#: u32 (version, capabilities, device_caps, reserved[3]). 80 + 24 = 104.
_CAP_REQUEST = (2 << 30) | (104 << 16) | (ord("V") << 8) | 0

_VIDEO_CAPTURE = 0x1          # V4L2_CAP_VIDEO_CAPTURE
_IR_FOURCCS = ("GREY", "Y16 ", "Z16 ")   # linux-hello's own IR test
_LINEAR_FOURCCS = ("MJPG", "YUYV", "NV12", "H264")

_FLAGS = os.O_RDWR | os.O_NONBLOCK


def _decoded(raw: bytes) -> str:
    return raw.split(b"\0", 1)[0].decode(errors="replace").strip()


def is_ir(formats, card: str) -> bool:
    """Does this node look like an infrared camera?

    **A pure function, so it can be tested without a camera** - the same discipline
    linux-hello's `is_valid_human_face()` has and Chronoa's `opencv/detect.py` does
    not. The two tests are independent and a false positive from either one is
    visible: a pixel format the kernel itself calls greyscale, or a card name that
    says IR.

    **A card name containing "ir" is a weak test and is kept anyway**, because it is
    the only thing that catches a device whose driver reports no IR format. It is
    weak because "ir" is a substring of ordinary words - a card named
    "First Camera" matches, and this probe would call it infrared. That is a false
    positive in the *description*, not in the capture: the capture path asks for the
    pixel format and the device refuses if it does not have one. Stated rather than
    left to be discovered.
    """
    for code, _description in formats or []:
        if code in _IR_FOURCCS:
            return True
    lowered = (card or "").lower()
    return "ir" in lowered or "infra" in lowered


def _formats_of(fd: int):
    """`(codes, error)` - `error` is None when the kernel answered, and set when it
    refused, because those two mean different things on a loopback node."""
    codes, error = [], None
    for index in range(32):
        try:
            fmt = bytearray(_FMT_SIZE)
            struct.pack_into("II", fmt, 0, index, 1)          # type = VIDEO_CAPTURE
            fcntl.ioctl(fd, _FMT_REQUEST, fmt, True)
        except OSError:
            if index == 0:
                error = "ENUM_FMT refused"
            break
        fourcc = struct.pack("<I", struct.unpack_from("I", fmt, _FMT_PIXELFORMAT)[0])
        codes.append((fourcc.decode("latin-1"), _decoded(fmt[_FMT_DESCRIPTION:
                                                             _FMT_PIXELFORMAT])))
    return codes, error


def probe(node: str) -> Dict[str, object]:
    """What this node is, and what it can do. `{}` when it cannot be asked at all.

    **Every field is either measured or absent, never a default.** `formats_error` is
    the one that keeps a refusal distinguishable from an empty answer.
    """
    try:
        fd = os.open(node, _FLAGS)
    except OSError as exc:
        return {"node": node, "error": f"could not open: {exc.strerror or exc.errno}"}
    try:
        try:
            cap = bytearray(104)
            fcntl.ioctl(fd, _CAP_REQUEST, cap, True)
            driver = _decoded(cap[0:16])
            card = _decoded(cap[16:48])
            _version, _caps, device_caps = struct.unpack_from("III", cap, 80)  # noqa: F841
        except OSError:
            return {"node": node, "error": "QUERYCAP refused - not a V4L2 device"}

        formats, formats_error = _formats_of(fd)
        info = {
            "node": node,
            "driver": driver,
            "card": card,
            #: The bit that matters: a metadata node passes QUERYCAP and fails this.
            "can_capture": bool(device_caps & _VIDEO_CAPTURE) or bool(formats),
            "device_caps": device_caps,
            "formats": [code for code, _description in formats],
            "formats_error": formats_error,
        }
        info["looks_ir"] = is_ir(formats, card)
        return info
    finally:
        os.close(fd)


def _nodes() -> List[str]:
    out = []
    for number in range(64):
        node = f"/dev/video{number}"
        if os.path.exists(node):
            out.append(node)
    return out


def collect() -> List[Dict[str, object]]:
    """Every `/dev/video*` node that could be asked, described. `[]` when none."""
    out = []
    for node in _nodes():
        info = probe(node)
        if "error" not in info:
            out.append(info)
    return out


def capture_nodes() -> List[str]:
    """Every node that can capture, by number.

    **Not "video0".** On this box `/dev/video1` exists and cannot capture, so a
    default of `/dev/video0` is right here by enumeration luck and wrong on a machine
    where the metadata node registers first. Node numbering follows registration
    order, so a replug or a capture card changes it.
    """
    return [str(info["node"]) for info in collect() if info.get("can_capture")]


def ir_nodes() -> List[str]:
    """Every node that looks like an infrared camera.

    **Ordered so a node that *can* capture comes first**, because a card name is a
    weak signal and a device that cannot capture is not a capture target however
    infrared it looks.
    """
    found = [info for info in collect() if info.get("looks_ir")]
    return [str(info["node"]) for info in
            sorted(found, key=lambda i: not i.get("can_capture"))]


def none_sentence() -> str:
    """The sentence for a machine with no infrared camera.

    **It names what was looked for**, because "no camera" and "no infrared camera"
    are different facts and only one of them is what was asked. And **it checks
    before it says so** - the first version asserted "advertise none of the infrared
    formats" unconditionally while `/dev/video9` advertised `Y16`, which is the
    absence-as-a-pass defect this repository records everywhere else, in a sentence
    nobody would read twice.
    """
    found = collect()
    infrared = [info for info in found if info.get("looks_ir")]
    if infrared:
        #: The caller asked when there *was* an infrared camera. Say so rather than
        #: delivering the sentence it expected.
        names = ", ".join(sorted({str(i.get("card") or i["node"]) for i in infrared}))
        return f"{len(infrared)} infrared camera(s): {names}"
    if not found:
        return "this machine has no camera node at all (no /dev/video*)"
    names = ", ".join(sorted({str(i.get("card") or i["node"]) for i in found}))
    return (f"no infrared camera is attached - the {len(found)} camera node(s) here "
            f"({names}) advertise none of the infrared formats")


def describe(node: str) -> str:
    """One line a person can read: what it is, and whether it can answer."""
    info = probe(node)
    if "error" in info:
        return f"{node}: {info['error']}"
    card = info.get("card") or "unnamed camera"
    if not info.get("can_capture"):
        return f"{node} ({card}): a node that cannot capture video"
    formats = ", ".join(info.get("formats") or []) or "no formats reported"
    band = "infrared" if info.get("looks_ir") else "ordinary"
    return f"{node} ({card}): {band}, formats {formats}"


if __name__ == "__main__":  # pragma: no cover - run by hand
    import sys

    for node in (sys.argv[1:] or _nodes()):
        print(describe(node))
    print("  capture nodes:", capture_nodes() or "none")
    print("  infrared nodes:", ir_nodes() or "none")
