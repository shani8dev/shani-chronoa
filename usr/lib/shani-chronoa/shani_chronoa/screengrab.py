"""Bounded screen and camera capture, as PNG bytes. No dependencies.

This is the capture half of the `vision` sense; `senses/vision.py` owns the
sense itself (consent, model choice, lifetime). The split mirrors `webtext.py`
for the `web` sense: the thing that touches the outside world lives outside the
sensory layer, so a test of the consent gate never has to touch a camera.

**Every failure here is a `ScreenCaptureError` with a message meant to be shown
to a user.** A screenshot on a headless box - a container, a systemd unit, an
ssh session with no X forwarding - is a normal state, not an exception, and the
one thing that must never happen is a traceback where a sentence would do.
`tests/test_vision_sense.py` drives the no-display case for real.

**Everything is bounded, because a capture that can hang is worse than no
capture.** `xwd`, `grim` and `gst-launch` are all run as argv lists with
`timeout=`, never `shell=True`; a raw capture is refused above
`MAX_CAPTURE_BYTES`; the encoded image is refused above `MAX_IMAGE_BYTES`; and
the decoded picture is downscaled to `MAX_CAPTURE_PIXELS` before it is encoded,
because a 4K desktop screenshot is tens of megabytes of base64 to send to a
local model for a description that needs far less detail.

Capture backends, resolved rather than assumed
----------------------------------------------
A wrong binary name shipped `whisper.cpp` broken for months in this repo, so
nothing here is derived from a package name: each backend is resolved with
`shutil.which` and the capture falls through to the next one, and a machine
with none of them installed gets a refusal naming what was looked for.

- **X11**: `import` (ImageMagick) if present, else `xwd`. `xwd` is in
  `x11-apps` and emits a raw ZPixmap dump, which is why this module carries an
  XWD decoder: without one, a machine with *only* `xwd` installed - a minimal
  container, a bare X session - simply cannot be seen, and the honest answer
  there is not a broken image. The decoder is verified against a real `xwd
  -root` capture of a real 1920x1080 X server.
- **Wayland**: `grim` (wlroots), then `gnome-screenshot`, then `spectacle`. A
  Wayland client cannot read another client's screen, so a compositor- or
  portal-specific tool is the only route; there is no generic fallback and this
  module does not pretend to have one.
- **Camera**: a GStreamer one-shot pipeline (`v4l2src ! videoconvert !
  pngenc`). GStreamer is a hard runtime dependency of a PipeWire/Wayland
  desktop, which is the same assumption `audio.py`'s `pw-record` fallback
  makes, and it needs nothing beyond what PipeWire already pulled in.

Colormapped visuals, non-contiguous channel masks, and 1- or 2-byte pixels are
**refused**, not guessed at. A vision model fed a misread frame buffer
confidently describes the wrong image, which is worse than saying "this X
server's pixel format is not supported here".
"""

import binascii
import logging
import os
import shutil
import struct
import subprocess
import tempfile
import time
import zlib
from pathlib import Path
from typing import NamedTuple, Optional, Sequence

logger = logging.getLogger(__name__)

# Generous for a cold page cache on a busy machine, and short enough that a
# wedged compositor cannot hold a turn open. A capture that takes longer than
# this is not going to succeed.
DEFAULT_TIMEOUT_SECONDS = 20.0

# A raw X11 dump is uncompressed: 1920x1080x4 is 8.3 MB and 4K is ~33 MB. This
# ceiling is about refusing an implausible capture before decoding it, not
# about memory pressure.
MAX_CAPTURE_BYTES = 64 * 1024 * 1024

# What the model is actually asked to look at. 1.2M pixels is about 1280x880,
# inside the pixel budget of every vision model in the default set while
# keeping a dialog, a URL in the address bar and a line of error text legible.
# Overridable per call, never silently.
MAX_CAPTURE_PIXELS = 1_200_000

# The encoded image bounds the request, and this is the one number a user can
# actually reach on a machine showing photographic content: PNG is lossless, so
# an incompressible frame at the pixel cap is ~3.5 MB. Refusing above this is
# deliberate - quietly encoding a smaller image would describe a different
# picture from the one captured, without saying so.
MAX_IMAGE_BYTES = 6 * 1024 * 1024

# X11 fallback binary, in `x11-apps`. The hardcoded path exists for the same
# reason `stt.py` hardcodes /usr/bin/whisper-cli: a stripped PATH (a systemd
# unit, a bare `su`) must not make an installed binary look absent.
FALLBACK_XWD = "/usr/bin/xwd"

# Sources, as the sense names them. Anything else is refused rather than
# coerced: an unrecognised source that quietly became "screen" would be a
# capture nobody asked for.
SOURCE_SCREEN = "screen"
SOURCE_CAMERA = "camera"
SOURCES = (SOURCE_SCREEN, SOURCE_CAMERA)

# `/dev/video0` is the conventional first camera. Probed with `stat`, never
# opened speculatively, and overridable per call.
DEFAULT_CAMERA_DEVICE = "/dev/video0"

# The XWD header is 25 CARD32 fields, big-endian on the wire, in this order.
_XWD_HEADER_FIELDS = (
    "header_size",
    "file_version",
    "pixmap_format",
    "pixmap_depth",
    "pixmap_width",
    "pixmap_height",
    "xoffset",
    "byte_order",
    "bitmap_unit",
    "bitmap_bit_order",
    "bitmap_pad",
    "bits_per_pixel",
    "bytes_per_line",
    "visual_class",
    "red_mask",
    "green_mask",
    "blue_mask",
    "bits_per_rgb",
    "colormap_entries",
    "ncolors",
    "window_width",
    "window_height",
    "window_x",
    "window_y",
    "window_bdrwidth",
)
_XWD_HEADER_BYTES = 4 * len(_XWD_HEADER_FIELDS)
_XWD_VERSION = 7
_XWD_ZPIXMAP = 2
_XWD_COLORMAP_ENTRY_BYTES = 12
_XWD_LSB_FIRST = 0

_PNG_MAGIC = b"\x89PNG\r\n\x1a\n"


class ScreenCaptureError(Exception):
    """A capture could not be taken. The message is fit to show a user."""


class Capture(NamedTuple):
    """One captured image: PNG bytes, plus the provenance worth keeping."""

    data: bytes
    source: str
    backend: str
    width: int
    height: int
    native_width: int
    native_height: int
    captured_at: float

    def downscaled(self) -> bool:
        """True if the encoded image is smaller than what was on screen."""
        return (self.width, self.height) != (self.native_width, self.native_height)

    def describe(self) -> str:
        """A short provenance string for a percept's `source` field."""
        scaled = (
            f", scaled from {self.native_width}x{self.native_height}"
            if self.downscaled()
            else ""
        )
        return f"{self.source} via {self.backend} ({self.width}x{self.height}{scaled})"


# --- environment ------------------------------------------------------------


def display_environment() -> str:
    """Which display server this process can capture from: wayland/x11/none.

    Read from the environment rather than probed, and deliberately not a guess:
    a Wayland session commonly also has `DISPLAY` set for XWayland clients, and
    preferring X11 there would capture the XWayland root - usually one blank
    window - and report success.
    """
    if os.environ.get("WAYLAND_DISPLAY", "").strip():
        return "wayland"
    if os.environ.get("DISPLAY", "").strip():
        return "x11"
    return "none"


# --- PNG encoding -----------------------------------------------------------


def _png_chunk(tag: bytes, payload: bytes) -> bytes:
    return (
        struct.pack(">I", len(payload))
        + tag
        + payload
        + struct.pack(">I", binascii.crc32(tag + payload) & 0xFFFFFFFF)
    )


def encode_png(width: int, height: int, rows: "Sequence[bytes]") -> bytes:
    """Encode 8-bit truecolour RGB `rows` as a PNG.

    Filter type 0 (None) on every row. A smarter filter compresses a desktop
    screenshot a little better and costs extra passes over the data; bounding
    the payload is this module's job (see `MAX_IMAGE_BYTES`), and a filter
    heuristic is not a bound. Encoded here rather than through PIL because this
    project is packaged for pacman and DEB and must not grow a pip dependency -
    the same reasoning `senses/ocr.py` records for refusing `pytesseract`.
    """
    if width <= 0 or height <= 0 or len(rows) != height:
        raise ScreenCaptureError(
            f"internal error: a {width}x{height} image was described by {len(rows)} row(s)"
        )
    raw = bytearray()
    for row in rows:
        if len(row) != width * 3:
            raise ScreenCaptureError(
                f"internal error: a {len(row)}-byte row for a {width}px RGB image"
            )
        raw.append(0)  # PNG filter: None
        raw += row
    header = struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)
    return (
        _PNG_MAGIC
        + _png_chunk(b"IHDR", header)
        + _png_chunk(b"IDAT", zlib.compress(bytes(raw), 6))
        + _png_chunk(b"IEND", b"")
    )


def png_size(png: bytes) -> "tuple[int, int]":
    """(width, height) from a PNG's IHDR, or refuse if it is not a usable PNG."""
    if len(png) < 33 or not png.startswith(_PNG_MAGIC) or png[12:16] != b"IHDR":
        raise ScreenCaptureError("the captured image is not a PNG")
    width, height = struct.unpack(">II", png[16:24])
    if width <= 0 or height <= 0:
        raise ScreenCaptureError(f"the captured PNG reports a {width}x{height} image")
    return width, height


# --- XWD decoding -----------------------------------------------------------


def _mask_shift_width(mask: int) -> "tuple[int, int]":
    """Split a channel mask into its lowest set-bit offset and its width."""
    shift = (mask & -mask).bit_length() - 1
    return shift, (mask >> shift).bit_length()


def _scale_factor(width: int, height: int, max_pixels: int) -> int:
    """The smallest whole downscale factor that fits inside `max_pixels`.

    A whole factor, so a scaled screenshot is a faithful sample of the real one
    rather than a resampling of a guess. Terminates because `max(1, width // f)`
    reaches 1, and 1*1 is below any sane cap.
    """
    factor = 1
    while max(1, width // factor) * max(1, height // factor) > max_pixels:
        factor += 1
    return factor


def _xwd_header(raw: bytes) -> dict:
    if len(raw) < _XWD_HEADER_BYTES:
        raise ScreenCaptureError("xwd produced a truncated header")
    return dict(
        zip(
            _XWD_HEADER_FIELDS,
            struct.unpack(f">{len(_XWD_HEADER_FIELDS)}I", raw[:_XWD_HEADER_BYTES]),
        )
    )


def _xwd_channels(fields: dict, pixel_bytes: int, little_endian: bool) -> "list[int]":
    """The byte offset within a pixel word that holds R, then G, then B.

    Every unsupported layout is refused here rather than guessed at: a
    colormapped visual declares no masks at all, a non-contiguous mask cannot be
    addressed with a byte offset, and a 4-bit channel has no byte to read.
    """
    offsets: list[int] = []
    for name, mask in zip(
        ("red", "green", "blue"),
        (fields["red_mask"], fields["green_mask"], fields["blue_mask"]),
    ):
        if mask == 0:
            raise ScreenCaptureError(
                "this X server's pixels are colormapped (the visual declares no "
                "channel masks); reading them as if they were TrueColor would "
                "invent colours"
            )
        shift, channel_width = _mask_shift_width(mask)
        if mask != ((1 << channel_width) - 1) << shift:
            raise ScreenCaptureError(
                f"the {name} channel mask 0x{mask:08x} is not one contiguous run of bits"
            )
        if channel_width not in (8, 16) or shift % 8:
            raise ScreenCaptureError(
                f"the {name} channel is {channel_width} bits wide; only 8- and "
                "16-bit channels are supported"
            )
        # For a 16-bit channel the high byte carries the significant 8 bits and
        # sits at this offset in either byte order, which is why byte order
        # never needs special-casing for one channel.
        index = shift // 8
        if not little_endian:
            index = pixel_bytes - 1 - index
        offsets.append(index)
    if len(set(offsets)) != 3:
        raise ScreenCaptureError(
            "this X server's channel masks share a byte of every pixel; the "
            "packed format is not supported"
        )
    return offsets


def decode_xwd(
    raw: bytes, max_pixels: int = MAX_CAPTURE_PIXELS
) -> "tuple[int, int, list[bytes]]":
    """Decode an XWD ZPixmap dump into downscaled 8-bit RGB rows.

    Returns `(width, height, rows)`, or the native geometry as
    `(native_width, native_height)` is recoverable from the same header.
    """
    fields = _xwd_header(raw)

    if fields["file_version"] != _XWD_VERSION:
        raise ScreenCaptureError(
            f"xwd wrote format version {fields['file_version']}, not {_XWD_VERSION}"
        )
    if fields["pixmap_format"] != _XWD_ZPIXMAP:
        raise ScreenCaptureError(
            "xwd wrote a colormapped dump rather than a ZPixmap; only "
            "TrueColor/DirectColor pixels are decoded here"
        )

    width, height = fields["pixmap_width"], fields["pixmap_height"]
    if width <= 0 or height <= 0:
        raise ScreenCaptureError(f"xwd reported a {width}x{height} image")

    # A pixel occupies `bitmap_unit` bits when it is narrower than the unit,
    # which is why a 24-bit-deep screen arrives with a 4-byte stride: xwd stores
    # each pixel in one 32-bit unit and leaves the top byte unused.
    bits_per_pixel = fields["bits_per_pixel"]
    unit = fields["bitmap_unit"]
    pixel_bytes = max(1, (unit if bits_per_pixel < unit else bits_per_pixel) // 8)
    if pixel_bytes > 4:
        raise ScreenCaptureError(
            f"xwd used {bits_per_pixel} bits per pixel, which is not supported"
        )

    stride = fields["bytes_per_line"]
    if stride < width * pixel_bytes:
        raise ScreenCaptureError(
            f"xwd claimed a {stride}-byte row stride for a {width}px image"
        )

    little_endian = fields["byte_order"] == _XWD_LSB_FIRST
    channels = _xwd_channels(fields, pixel_bytes, little_endian)

    offset = fields["header_size"] + fields["ncolors"] * _XWD_COLORMAP_ENTRY_BYTES
    needed = offset + stride * height
    if len(raw) < needed:
        raise ScreenCaptureError(
            f"xwd wrote {len(raw)} bytes but {needed} were needed for a {width}x{height} image"
        )

    factor = _scale_factor(width, height, max_pixels)
    out_width, out_height = max(1, width // factor), max(1, height // factor)
    step = factor * pixel_bytes

    rows: list[bytes] = []
    interleaved = bytearray(out_width * 3)
    for out_y in range(out_height):
        row_start = offset + (out_y * factor) * stride
        # Three strided slices, one per channel, starting at the first output
        # pixel of this row and stepping a whole factor of source pixels. This
        # is nearest-neighbour downscaling expressed as slicing, so a 2M-pixel
        # desktop does not become 2M Python loop iterations.
        for channel, byte_index in enumerate(channels):
            start = row_start + byte_index
            interleaved[channel::3] = raw[start : start + out_width * step : step]
        rows.append(bytes(interleaved))
    return out_width, out_height, rows


def xwd_size(raw: bytes) -> "tuple[int, int]":
    """The *native* geometry an XWD dump was captured at, before scaling."""
    fields = _xwd_header(raw)
    return fields["pixmap_width"], fields["pixmap_height"]


# --- running external capture tools -----------------------------------------


def _run_capture(
    argv: "Sequence[str]",
    timeout: float,
    backend: str,
    max_bytes: int,
    expect_stdout: bool = True,
) -> bytes:
    """Run a capture tool and return its raw stdout, bounded in every direction.

    An argv list, never `shell=True`: nothing here is user-supplied, and there
    is no reason to hand a display-capture tool a shell.

    `expect_stdout=False` is for the GStreamer path, which writes the frame to
    a file named on the pipeline and legitimately prints nothing. Requiring
    stdout there produced a real, verified failure: a correct camera capture
    reported "gst-launch-1.0 produced no image data" while the frame sat
    complete in the temp file.
    """
    try:
        completed = subprocess.run(list(argv), capture_output=True, timeout=timeout, check=False)
    except subprocess.TimeoutExpired:
        raise ScreenCaptureError(
            f"{backend} took longer than {timeout:.0f}s and was stopped; the display "
            "server may be unresponsive"
        ) from None
    except FileNotFoundError as e:
        raise ScreenCaptureError(f"{backend} disappeared while capturing: {e}") from e
    except OSError as e:
        raise ScreenCaptureError(f"could not run {backend}: {e}") from e

    if completed.returncode != 0:
        stderr = (completed.stderr or b"").decode("utf-8", "replace").strip().splitlines()
        detail = stderr[-1] if stderr else f"exit status {completed.returncode}"
        raise ScreenCaptureError(f"{backend} failed: {detail}")
    if expect_stdout and not completed.stdout:
        raise ScreenCaptureError(f"{backend} produced no image data on stdout")
    if len(completed.stdout) > max_bytes:
        raise ScreenCaptureError(
            f"{backend} produced {len(completed.stdout) // (1024 * 1024)} MiB, over the "
            f"{max_bytes // (1024 * 1024)} MiB capture limit"
        )
    return completed.stdout


def _require_png(data: bytes, backend: str) -> bytes:
    """Return `data` if it is a PNG, else refuse with what it actually was."""
    if data.startswith(_PNG_MAGIC):
        return data
    preview = data[:16].hex() or "(empty)"
    raise ScreenCaptureError(
        f"{backend} did not write PNG data to stdout (it began with {preview}); this "
        "version may not support writing an image to a pipe"
    )


def resolve_xwd() -> Optional[str]:
    """The xwd binary, or None if this machine has none."""
    found = shutil.which("xwd")
    if found:
        return found
    if os.path.isfile(FALLBACK_XWD) and os.access(FALLBACK_XWD, os.X_OK):
        return FALLBACK_XWD
    return None


# Wayland backends, in the order they are tried. Each writes a PNG to stdout; a
# Wayland client has no portable way to read another client's screen, so there
# is deliberately no "generic" entry here.
_WAYLAND_BACKENDS: "tuple[tuple[str, tuple[str, ...]], ...]" = (
    ("grim", ("grim", "-")),
    ("gnome-screenshot", ("gnome-screenshot", "-f", "-")),
    ("spectacle", ("spectacle", "--background", "--nonotify", "--output", "-")),
)

# ImageMagick's `import`, which writes a PNG to stdout on X11.
_X11_PNG_BACKEND = ("import", ("import", "-window", "root", "png:-"))


def _with_executable(argv: "Sequence[str]", executable: str) -> "list[str]":
    return [executable, *argv[1:]]


def _capture_png_backend(
    backend: str, argv: "Sequence[str]", timeout: float, max_capture_bytes: int
) -> Capture:
    executable = shutil.which(backend)
    if executable is None:
        raise ScreenCaptureError(f"{backend} is not installed")
    png = _require_png(
        _run_capture(_with_executable(argv, executable), timeout, backend, max_capture_bytes),
        backend,
    )
    width, height = png_size(png)
    return Capture(
        data=png,
        source=SOURCE_SCREEN,
        backend=backend,
        width=width,
        height=height,
        native_width=width,
        native_height=height,
        captured_at=time.time(),
    )


def _capture_wayland(timeout: float, max_capture_bytes: int) -> Capture:
    tried: list[str] = []
    for backend, argv in _WAYLAND_BACKENDS:
        if shutil.which(backend) is None:
            tried.append(backend)
            continue
        return _capture_png_backend(backend, argv, timeout, max_capture_bytes)
    raise ScreenCaptureError(
        "no Wayland screen capture tool is installed (looked for "
        f"{', '.join(tried) or 'no known backend'}); on Arch that is 'grim', on GNOME "
        "it is 'gnome-screenshot'"
    )


def _capture_x11(timeout: float, max_capture_bytes: int, max_image_bytes: int) -> Capture:
    if shutil.which(_X11_PNG_BACKEND[0]) is not None:
        try:
            return _capture_png_backend(
                _X11_PNG_BACKEND[0], _X11_PNG_BACKEND[1], timeout, max_capture_bytes
            )
        except ScreenCaptureError as e:
            # ImageMagick is frequently installed with a policy that forbids
            # reading the root window, and xwd may still work: a fall-through,
            # not a refusal.
            logger.info("Falling back to xwd: %s", e)

    xwd = resolve_xwd()
    if xwd is None:
        raise ScreenCaptureError(
            "no X11 screen capture tool is installed (looked for import, xwd); on Arch "
            "'xwd' is in the x11-apps package"
        )
    raw = _run_capture((xwd, "-root", "-silent"), timeout, "xwd", max_capture_bytes)
    width, height, rows = decode_xwd(raw)
    png = encode_png(width, height, rows)
    if len(png) > max_image_bytes:
        raise ScreenCaptureError(
            f"the captured image is {len(png) // 1024} KiB as PNG, over the "
            f"{max_image_bytes // 1024} KiB limit for a single request"
        )
    native_width, native_height = xwd_size(raw)
    return Capture(
        data=png,
        source=SOURCE_SCREEN,
        backend="xwd",
        width=width,
        height=height,
        native_width=native_width,
        native_height=native_height,
        captured_at=time.time(),
    )


def capture_screen(
    timeout: float = DEFAULT_TIMEOUT_SECONDS,
    max_pixels: int = MAX_CAPTURE_PIXELS,
    max_capture_bytes: int = MAX_CAPTURE_BYTES,
    max_image_bytes: int = MAX_IMAGE_BYTES,
) -> Capture:
    """Capture the screen of whatever display server is actually there.

    Raises `ScreenCaptureError` - never a traceback - when there is no display
    at all, which is the normal state inside a container, a systemd unit or an
    ssh session with no X forwarding.
    """
    environment = display_environment()
    if environment == "none":
        raise ScreenCaptureError(
            "there is no display to capture: neither WAYLAND_DISPLAY nor DISPLAY is set, "
            "so this is a headless session"
        )
    if environment == "wayland":
        return _capture_wayland(timeout, max_capture_bytes)
    return _capture_x11(timeout, max_capture_bytes, max_image_bytes)


# --- camera capture ---------------------------------------------------------


def camera_devices() -> "list[str]":
    """Every readable `/dev/video*` node, in name order."""
    found: list[str] = []
    for path in sorted(Path("/dev").glob("video*")):
        if os.access(str(path), os.R_OK | os.W_OK):
            found.append(str(path))
    return found


def capture_camera(
    device: Optional[str] = None,
    timeout: float = DEFAULT_TIMEOUT_SECONDS,
    max_capture_bytes: int = MAX_CAPTURE_BYTES,
    max_image_bytes: int = MAX_IMAGE_BYTES,
) -> Capture:
    """Capture one frame from a camera as a PNG.

    A GStreamer pipeline rather than a hand-rolled V4L2 mmap loop: GStreamer is
    already a hard dependency of a PipeWire desktop - which is what makes a
    camera exist here at all - and `pngenc` yields a real PNG without this
    project growing a video dependency. `num-buffers=1` is what makes this a
    single frame rather than a stream that never ends.
    """
    target = device or DEFAULT_CAMERA_DEVICE
    if not os.path.exists(target):
        available = camera_devices()
        raise ScreenCaptureError(
            f"no camera at {target}"
            + (f"; readable devices are {', '.join(available)}" if available else "")
        )
    gst = shutil.which("gst-launch-1.0") or shutil.which("gst-launch")
    if gst is None:
        raise ScreenCaptureError(
            "GStreamer's gst-launch-1.0 is not installed, so no camera can be read"
        )

    handle, path = tempfile.mkstemp(prefix="chronoa-camera-", suffix=".png")
    os.close(handle)
    try:
        _run_capture(
            (
                gst,
                "-q",
                "v4l2src",
                f"device={target}",
                "num-buffers=1",
                "!",
                "videoconvert",
                "!",
                "pngenc",
                "!",
                "filesink",
                f"location={path}",
            ),
            timeout,
            "gst-launch-1.0",
            max_capture_bytes,
            expect_stdout=False,
        )
        try:
            with open(path, "rb") as captured:
                png = captured.read()
        except OSError as e:
            raise ScreenCaptureError(f"could not read the captured camera frame: {e}") from e
    finally:
        try:
            os.unlink(path)
        except OSError:
            logger.debug("Could not remove the temporary camera frame at %s", path)

    if not png:
        raise ScreenCaptureError("the camera produced no frame")
    if len(png) > max_image_bytes:
        raise ScreenCaptureError(
            f"the camera frame is {len(png) // 1024} KiB, over the "
            f"{max_image_bytes // 1024} KiB limit for a single request"
        )
    width, height = png_size(png)
    return Capture(
        data=png,
        source=SOURCE_CAMERA,
        backend=f"gstreamer:{target}",
        width=width,
        height=height,
        native_width=width,
        native_height=height,
        captured_at=time.time(),
    )


def capture(
    source: str = SOURCE_SCREEN,
    device: Optional[str] = None,
    timeout: float = DEFAULT_TIMEOUT_SECONDS,
    max_pixels: int = MAX_CAPTURE_PIXELS,
    max_capture_bytes: int = MAX_CAPTURE_BYTES,
    max_image_bytes: int = MAX_IMAGE_BYTES,
) -> Capture:
    """Capture from `source`, which must be one of `SOURCES`.

    The eyes light for the duration of the capture. A camera being opened is the
    single event on this list that a person in the room could not otherwise
    know about, which is why it is announced from the one function every capture
    path - screen, camera, portal - goes through rather than from each.
    """
    activity = _light_eyes(source, device)
    try:
        return _capture(source, device, timeout, max_pixels, max_capture_bytes,
                        max_image_bytes)
    finally:
        _put_eyes(activity)


def _light_eyes(source: str, device: Optional[str]):
    try:
        from shani_chronoa import body

        what = "reading the screen" if source == SOURCE_SCREEN else "using the camera"
        return body.body.use("eyes", what, (device or "default")[:80],
                             deadline=max(30.0, DEFAULT_TIMEOUT_SECONDS * 2))
    except Exception:                                   # noqa: BLE001
        return None


def _put_eyes(activity) -> None:
    try:
        from shani_chronoa import body

        body.body.done(activity)
    except Exception:                                   # noqa: BLE001
        pass


def _capture(
    source: str,
    device: Optional[str],
    timeout: float,
    max_pixels: int,
    max_capture_bytes: int,
    max_image_bytes: int,
) -> Capture:
    if source == SOURCE_SCREEN:
        return capture_screen(
            timeout=timeout,
            max_pixels=max_pixels,
            max_capture_bytes=max_capture_bytes,
            max_image_bytes=max_image_bytes,
        )
    if source == SOURCE_CAMERA:
        return capture_camera(
            device=device,
            timeout=timeout,
            max_capture_bytes=max_capture_bytes,
            max_image_bytes=max_image_bytes,
        )
    raise ScreenCaptureError(
        f"{source!r} is not a capture source Chronoa knows; expected one of {', '.join(SOURCES)}"
    )
