"""Skill: when and where was this photo taken, and with what camera?

`read_image` reports a picture's size and format; `photos` indexes a library.
Neither answers the one-photo question - the camera, the date it was *taken*
(not the file's date, which changes on every copy), the exposure, and the GPS
position embedded in it. That last one matters for privacy as much as for
curiosity: a photo about to be shared may say exactly where someone lives.

Read with a small EXIF reader in this module, not `exiv2` (not on the image)
or Pillow (not a dependency): JPEG's APP1 segment, or a bare TIFF, then IFD0,
the Exif sub-IFD and the GPS sub-IFD. Only the tags below are decoded; the rest
are counted, so a photo with metadata this reader does not name is not reported
as having none.

Honesty rules: a format this reader does not parse (PNG, HEIC, WebP) is said to
be unsupported, never "no metadata"; a JPEG with no EXIF segment is reported as
having none, because that is what the file says; GPS is only quoted when both
coordinates and their hemisphere references are present.
"""

from __future__ import annotations

import struct

from shani_chronoa import files
from shani_chronoa.skills import Skill

_MAX_SCAN = 256 * 1024  # EXIF lives in the first APP1 segment; never read a whole RAW

_IFD0 = {0x010F: "Camera make", 0x0110: "Camera model", 0x0112: "Orientation",
         0x0131: "Software", 0x0132: "File date"}
_EXIF = {0x9003: "Taken", 0x829A: "Exposure", 0x829D: "Aperture", 0x8827: "ISO",
         0x920A: "Focal length", 0xA434: "Lens", 0x9209: "Flash"}
_EXIF_POINTER, _GPS_POINTER = 0x8769, 0x8825
_SIZES = {1: 1, 2: 1, 3: 2, 4: 4, 5: 8, 7: 1, 9: 4, 10: 8}

SCHEMA = {
    "type": "function",
    "function": {
        "name": "photo_metadata",
        "description": (
            "Read a photo's embedded EXIF metadata: the camera and lens, the "
            "date and time it was taken, exposure, aperture, ISO, focal length, "
            "and the GPS location if one is recorded. Use for 'when was this "
            "photo taken', 'does this picture contain my location', 'which "
            "camera took it'. JPEG and TIFF. Read-only; the file is not changed."
        ),
        "parameters": {
            "type": "object",
            "properties": {"path": {"type": "string", "description": "The photo file."}},
            "required": ["path"],
        },
    },
}


class _Tiff:
    def __init__(self, data: bytes):
        if data[:2] == b"II":
            self.e = "<"
        elif data[:2] == b"MM":
            self.e = ">"
        else:
            raise ValueError("not a TIFF header")
        if struct.unpack(self.e + "H", data[2:4])[0] != 42:
            raise ValueError("bad TIFF magic")
        self.data = data
        self.first = struct.unpack(self.e + "I", data[4:8])[0]

    def ifd(self, offset: int) -> "dict[int, object]":
        d, e = self.data, self.e
        if offset + 2 > len(d):
            return {}
        count = struct.unpack(e + "H", d[offset:offset + 2])[0]
        out = {}
        for i in range(min(count, 512)):
            p = offset + 2 + i * 12
            if p + 12 > len(d):
                break
            tag, typ, n = struct.unpack(e + "HHI", d[p:p + 8])
            size = _SIZES.get(typ, 0) * n
            if size == 0:
                continue
            raw = d[p + 8:p + 12] if size <= 4 else None
            if raw is None:
                ptr = struct.unpack(e + "I", d[p + 8:p + 12])[0]
                if ptr + size > len(d):
                    continue
                raw = d[ptr:ptr + size]
            out[tag] = self._value(typ, n, raw)
        return out

    def _value(self, typ: int, n: int, raw: bytes):
        e = self.e
        if typ == 2:
            return raw.split(b"\0", 1)[0].decode("utf-8", "replace").strip()
        if typ in (1, 7):
            return list(raw[:n])
        if typ == 3:
            vals = list(struct.unpack(e + "H" * n, raw[:2 * n]))
        elif typ in (4, 9):
            vals = list(struct.unpack(e + ("I" if typ == 4 else "i") * n, raw[:4 * n]))
        else:  # rationals
            fmt = "I" if typ == 5 else "i"
            pairs = struct.unpack(e + fmt * (2 * n), raw[:8 * n])
            vals = [(pairs[i], pairs[i + 1]) for i in range(0, len(pairs), 2)]
        return vals[0] if n == 1 else vals


def tiff_block(data: bytes) -> "bytes | None":
    """The TIFF-structured EXIF block of a JPEG or TIFF file, or None."""
    if data[:2] in (b"II", b"MM"):
        return data
    if data[:2] != b"\xff\xd8":
        raise ValueError("unsupported")
    i = 2
    while i + 4 <= len(data):
        if data[i] != 0xFF:
            return None
        marker = data[i + 1]
        if marker in (0xD9, 0xDA):  # end of image / start of scan: no EXIF before it
            return None
        length = struct.unpack(">H", data[i + 2:i + 4])[0]
        if marker == 0xE1 and data[i + 4:i + 10] == b"Exif\0\0":
            return data[i + 10:i + 2 + length]
        i += 2 + length
    return None


def _ratio(v) -> "float | None":
    if isinstance(v, tuple) and len(v) == 2 and v[1]:
        return v[0] / v[1]
    return None


def _dms(v) -> "float | None":
    if not isinstance(v, list) or len(v) != 3:
        return None
    parts = [_ratio(x) for x in v]
    if any(p is None for p in parts):
        return None
    return parts[0] + parts[1] / 60 + parts[2] / 3600


def _fmt(label: str, v) -> str:
    if label == "Exposure":
        r = _ratio(v)
        if r:
            return f"1/{round(1 / r)} s" if r < 1 else f"{r:g} s"
    if label == "Aperture":
        r = _ratio(v)
        if r:
            return f"f/{r:.1f}"
    if label == "Focal length":
        r = _ratio(v)
        if r:
            return f"{r:g} mm"
    if label == "Flash" and isinstance(v, int):
        return "fired" if v & 1 else "did not fire"
    return str(v)


def read_metadata(data: bytes) -> "dict":
    block = tiff_block(data)
    if block is None:
        return {}
    tiff = _Tiff(block)
    ifd0 = tiff.ifd(tiff.first)
    result = {"_tags": len(ifd0)}
    for tag, label in _IFD0.items():
        if tag in ifd0:
            result[label] = _fmt(label, ifd0[tag])
    if isinstance(ifd0.get(_EXIF_POINTER), int):
        exif = tiff.ifd(ifd0[_EXIF_POINTER])
        result["_tags"] += len(exif)
        for tag, label in _EXIF.items():
            if tag in exif:
                result[label] = _fmt(label, exif[tag])
    if isinstance(ifd0.get(_GPS_POINTER), int):
        gps = tiff.ifd(ifd0[_GPS_POINTER])
        lat, lon = _dms(gps.get(2)), _dms(gps.get(4))
        lat_ref, lon_ref = gps.get(1), gps.get(3)
        if lat is not None and lon is not None and lat_ref in ("N", "S") and lon_ref in ("E", "W"):
            result["GPS"] = (-lat if lat_ref == "S" else lat, -lon if lon_ref == "W" else lon)
            alt = _ratio(gps.get(6))
            if alt is not None:
                result["Altitude"] = f"{alt:.0f} m"
        elif gps:
            result["GPS"] = "present but incomplete"
    return result


def _run(arguments: dict) -> str:
    try:
        src = files.resolve((arguments.get("path") or "").strip())
    except files.PathProblem as e:
        return str(e)
    if not src.is_file():
        return f"{src} is not a file."
    try:
        with open(src, "rb") as handle:
            data = handle.read(_MAX_SCAN)
    except OSError as exc:
        return f"Could not read {src.name}: {exc}"
    try:
        meta = read_metadata(data)
    except ValueError:
        return (f"{src.name} is not a JPEG or TIFF, so this reader cannot parse its "
                f"metadata. That is not the same as it having none - PNG, HEIC and "
                f"WebP can all carry a location too.")
    except (struct.error, IndexError):
        return f"{src.name} has an EXIF block I could not decode, so its metadata is UNKNOWN."
    if not meta:
        return f"{src.name} carries no EXIF metadata: no camera, date or location is embedded in it."
    lines = [f"{src.name}:"]
    for key, value in meta.items():
        if key.startswith("_") or key == "GPS":
            continue
        lines.append(f"  {key}: {value}")
    gps = meta.get("GPS")
    if isinstance(gps, tuple):
        lines.append(f"  Location: {gps[0]:.6f}, {gps[1]:.6f} - anyone you share this file with can see where it was taken.")
    elif gps:
        lines.append(f"  Location: {gps}")
    else:
        lines.append("  Location: none recorded.")
    named = len([k for k in meta if not k.startswith("_")])
    if meta.get("_tags", 0) > named:
        lines.append(f"  ({meta['_tags']} tags in all; only the ones above are decoded here.)")
    return "\n".join(lines)


SKILLS = [Skill(name="photo_metadata", schema=SCHEMA, run=_run)]
