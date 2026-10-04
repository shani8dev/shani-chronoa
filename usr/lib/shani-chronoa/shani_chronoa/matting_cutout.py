"""Cutting the subject out of a picture, and the check that it really happened.

Two subjects, two models, and a deliberate wall between them:

- `person` - PP-HumanSeg, `opencv/effects.remove_background`, untouched. It
  answers "which pixels are a person", which is a different question from "which
  pixels are the thing this photo is of".
- `object` - u2net (`segmentation_u2net`), a general saliency model. It needs an
  optional extra that is not on a fresh install, and when it is not there
  `cut_out` raises `CutoutUnavailable` with the reason.

There is no third answer. Falling back to PP-HumanSeg for a dog would return a
cut-out of whatever person-shaped region it found, or an empty one, under a
sentence saying the dog's background was removed - a wrong answer delivered
confidently, which is the one failure mode this repo refuses everywhere else.

`check_cutout` is the other half: an effect that writes a file has to be checked
against that file, not against its own report. It reads the PNG header with the
standard library (so "it is not even a PNG with an alpha channel" needs no extra
installed) and the pixels with OpenCV, and compares the subject's pixels with
the source, so an output that is transparent everywhere, opaque everywhere, or
cleared to one flat colour is FAILED rather than reported as a cut-out.
"""

from __future__ import annotations

import logging
import struct
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

#: `person` keeps the model that has always answered it. `object` is the subject
#: the u2net extra is for; it is never chosen for a person unless asked for by
#: name, because the people path is the one that works without the extra.
SUBJECTS = ("person", "object")

#: Below this the picture is treated as a blank, above it as nothing removed.
#: A real cut-out of a tight subject can be near the top, so the bound is not 1.0.
MIN_OPAQUE, MAX_OPAQUE = 0.005, 0.995

#: Mean per-channel difference tolerated between the subject's pixels in the
#: output and the same pixels in the source, out of 255. PNG is lossless, so a
#: correct cut-out measures 0; anything larger means the subject was recomposed,
#: replaced or re-encoded rather than carried over.
PIXEL_TOLERANCE = 12

_PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"


class CutoutUnavailable(Exception):
    """The cut-out cannot be done here. The message is read by a person."""


def model_of(subject: str) -> str:
    return "u2net" if subject == "object" else "PP-HumanSeg"


def cut_out(image, subject: str = "person"):
    """A BGRA image of the same size: the subject kept, the background transparent.

    Raises `CutoutUnavailable` naming exactly what is missing rather than
    answering with the other model.
    """
    subject = (subject or "person").strip().lower()
    if subject not in SUBJECTS:
        raise ValueError(f"subject must be one of {', '.join(SUBJECTS)}")
    if subject == "person":
        from shani_chronoa.opencv import effects

        return effects.remove_background(image)
    from shani_chronoa import segmentation_u2net

    why = segmentation_u2net.problem()
    if why:
        raise CutoutUnavailable(why)
    # The loader, not `import cv2`: the OpenCV wheel is unpacked into the user's
    # home at run time, so an import placed ahead of it finds nothing at all.
    cv2, _np = segmentation_u2net.libraries()
    out = cv2.cvtColor(image, cv2.COLOR_BGR2BGRA)
    out[..., 3] = (segmentation_u2net.alpha_mask(image) * 255.0).astype("uint8")
    return out


# --- the post-condition ------------------------------------------------------

def png_alpha(path: Path) -> Optional[bool]:
    """Whether `path` is a PNG carrying an alpha channel; None when it is not a PNG.

    Read from IHDR rather than by decoding, so this answer does not depend on
    OpenCV being installed - which matters, because "the file has no alpha" is a
    different failure from "OpenCV is missing" and they must not be reported as
    the same thing.
    """
    try:
        head = path.open("rb").read(33)
    except OSError:
        return None
    if not head.startswith(_PNG_SIGNATURE) or head[12:16] != b"IHDR":
        return None
    width, height, depth, colour = struct.unpack(">IIBB", head[16:26])
    if depth not in (8, 16) or width == 0 or height == 0:
        return None
    return colour in (4, 6)  # grey+alpha, or RGBA


def _decoded(path: Path):
    try:
        from shani_chronoa.opencv import runtime as opencv_runtime
    except ImportError:
        return None
    try:
        cv2, np = opencv_runtime.load()
    except ImportError:
        return None
    image = cv2.imdecode(np.fromfile(str(path), dtype=np.uint8), cv2.IMREAD_UNCHANGED)
    return (cv2, np, image) if image is not None else None


def check_cutout(target: Path, source: Optional[Path] = None):
    """`(ok, evidence)` for a written cut-out, or None when it cannot be checked.

    None rather than True when the pixels cannot be read: a check that cannot
    look at the result has not verified it, and `verification.verify` reports
    that as UNVERIFIED - never as success.
    """
    target = Path(target)
    if not target.is_file():
        return False, f"no cut-out was written at {target}"
    has_alpha = png_alpha(target)
    if has_alpha is None:
        return False, f"{target.name} is not a PNG this can read an alpha channel from"
    if not has_alpha:
        return False, f"{target.name} is a PNG with no alpha channel, so nothing was made transparent"
    decoded = _decoded(target)
    if decoded is None:
        return None
    _cv2, np, image = decoded
    if image.ndim != 3 or image.shape[2] != 4:
        return False, f"{target.name} decoded to {image.shape} instead of a BGRA image"
    alpha = image[..., 3]
    opaque = float((alpha > 127).mean())
    if opaque < MIN_OPAQUE:
        return False, f"{target.name} is transparent everywhere ({opaque:.1%} opaque): there is no subject in it"
    if opaque > MAX_OPAQUE:
        return False, (f"{target.name} is opaque everywhere ({opaque:.1%}): the background was never "
                       f"made transparent")
    if source is not None:
        source = Path(source)
        before = _decoded(source)
        if before is not None and before[2].shape[:2] == image.shape[:2]:
            original = before[2]
            if original.ndim == 3 and original.shape[2] >= 3:
                keep = alpha > 127
                difference = float(np.abs(original[..., :3][keep].astype(np.int16) -
                                          image[..., :3][keep].astype(np.int16)).mean())
                if difference > PIXEL_TOLERANCE:
                    return False, (f"the subject's pixels in {target.name} differ from {source.name} by "
                                   f"{difference:.0f}/255, so the subject was not carried over")
                return True, (f"{target.name} is a BGRA PNG, {opaque:.1%} of its pixels opaque, and the "
                              f"subject's pixels match {source.name} (mean difference {difference:.1f}/255)")
    distinct = len(np.unique(image[..., :3][alpha > 127].reshape(-1, 3), axis=0))
    if distinct < 8:
        return False, (f"the opaque part of {target.name} is {distinct} colour(s): the subject was replaced "
                       f"with something flat")
    return True, f"{target.name} is a BGRA PNG, {opaque:.1%} of its pixels opaque, {distinct} colours in the subject"