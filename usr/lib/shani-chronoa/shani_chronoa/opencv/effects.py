"""Effects that depend on what is in the picture - the faces, the person - for a photo or each video frame.

Only these need OpenCV. A look that is the same whatever the picture shows
(sepia, vignette, sketch, paint, enhance) is ImageMagick's, in `edit_image`,
and resizing or converting is `edit_image`'s and `convert_media`'s: tools every
ShaniOS install already has, with nothing to download. Each effect here is a
function from a BGR image to an image of the same size, so a video is the same
call per frame.
"""

from __future__ import annotations

from typing import Callable, Dict

from shani_chronoa.opencv import detect, runtime


def _faces_region(image, fn):
    cv2, _np = runtime.load()
    out = image.copy()
    for b in detect.faces(image, threshold=0.6):
        # a margin, so hair and the edge of the face go too
        mx, my = b.w // 5, b.h // 4
        x0, y0 = max(0, b.x - mx), max(0, b.y - my)
        x1, y1 = min(image.shape[1], b.x + b.w + mx), min(image.shape[0], b.y + b.h + my)
        if x1 > x0 and y1 > y0:
            out[y0:y1, x0:x1] = fn(out[y0:y1, x0:x1])
    return out


def blur_faces(image):
    cv2, _np = runtime.load()
    return _faces_region(image, lambda r: cv2.GaussianBlur(r, (0, 0), max(8, r.shape[1] / 6)))


def pixelate_faces(image):
    cv2, _np = runtime.load()

    def pixelate(r):
        small = cv2.resize(r, (max(1, r.shape[1] // 12), max(1, r.shape[0] // 12)), interpolation=cv2.INTER_LINEAR)
        return cv2.resize(small, (r.shape[1], r.shape[0]), interpolation=cv2.INTER_NEAREST)
    return _faces_region(image, pixelate)


def blur_background(image):
    cv2, np = runtime.load()
    mask = cv2.GaussianBlur(detect.people_mask(image), (21, 21), 0).astype(np.float32)[..., None] / 255.0
    blurred = cv2.GaussianBlur(image, (0, 0), 12)
    return (image * mask + blurred * (1.0 - mask)).astype(np.uint8)


def remove_background(image):
    """The people kept, everything else transparent: a BGRA image (photos only - video has no alpha)."""
    cv2, _np = runtime.load()
    out = cv2.cvtColor(image, cv2.COLOR_BGR2BGRA)
    out[..., 3] = cv2.GaussianBlur(detect.people_mask(image), (7, 7), 0)
    return out


def label_boxes(image, boxes):
    """Draw each detection with its label - the 'show me what you found' copy."""
    cv2, _np = runtime.load()
    out = image.copy()
    for b in boxes:
        cv2.rectangle(out, (b.x, b.y), (b.x + b.w, b.y + b.h), (40, 200, 40), 2)
        cv2.putText(out, f"{b.label} {b.score:.0%}", (b.x, max(14, b.y - 6)), cv2.FONT_HERSHEY_SIMPLEX, 0.5,
                    (40, 200, 40), 1, cv2.LINE_AA)
    return out


EFFECTS: Dict[str, Callable] = {
    "blur_faces": blur_faces, "pixelate_faces": pixelate_faces, "blur_background": blur_background,
    "remove_background": remove_background,
}
#: a video has no transparency, so a removed background is for photos only
PHOTO_ONLY = frozenset({"remove_background"})
