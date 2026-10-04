"""A page in a photo, found and flattened - what makes a camera scan read like a scanner's.

A webcam photo of a page is skewed, has a desk around it and uneven light, and
OCR reads that badly. The page is the largest four-cornered outline covering
at least a fifth of the frame; it is warped flat, and an adaptive threshold
evens out a shadow across it. With no page found, the whole frame is cleaned
the same way and the caller says so.
"""

from __future__ import annotations

from typing import NamedTuple

from shani_chronoa.opencv import runtime

#: a page must cover at least this share of the frame to count as the page
MIN_PAGE_AREA = 0.2
#: a candidate spanning this much of both sides is the picture's border, not a page
MAX_PAGE_SPAN = 0.97


class PageResult(NamedTuple):
    png: bytes
    found_page: bool
    corners: "list[tuple[int, int]]"
    width: int
    height: int


def _order(points):
    """Corners as top-left, top-right, bottom-right, bottom-left."""
    s = [p[0] + p[1] for p in points]
    d = [p[1] - p[0] for p in points]
    return [points[s.index(min(s))], points[d.index(min(d))], points[s.index(max(s))], points[d.index(max(d))]]


def flatten_page(image_bytes: bytes, clean: bool = True) -> PageResult:
    """Find the page in a photo, warp it flat and even its light. Without a page, the whole frame, cleaned."""
    cv2, np = runtime.load()
    frame = cv2.imdecode(np.frombuffer(image_bytes, dtype=np.uint8), cv2.IMREAD_COLOR)
    if frame is None:
        raise ValueError("that is not an image OpenCV can read")
    h, w = frame.shape[:2]
    scale = 1000.0 / max(h, w) if max(h, w) > 1000 else 1.0
    small = cv2.resize(frame, (int(w * scale), int(h * scale))) if scale != 1.0 else frame
    gray = cv2.cvtColor(small, cv2.COLOR_BGR2GRAY)
    edges = cv2.Canny(cv2.GaussianBlur(gray, (5, 5), 0), 50, 150)
    edges = cv2.dilate(edges, np.ones((3, 3), np.uint8))
    contours, _ = cv2.findContours(edges, cv2.RETR_LIST, cv2.CHAIN_APPROX_SIMPLE)
    page = None
    for c in sorted(contours, key=cv2.contourArea, reverse=True)[:10]:
        approx = cv2.approxPolyDP(c, 0.02 * cv2.arcLength(c, True), True)
        x, y, bw, bh = cv2.boundingRect(approx)
        if bw >= MAX_PAGE_SPAN * small.shape[1] and bh >= MAX_PAGE_SPAN * small.shape[0]:
            continue  # the frame's own edge, not a page in it (random noise found "a page" this way)
        if len(approx) == 4 and cv2.contourArea(approx) >= MIN_PAGE_AREA * small.shape[0] * small.shape[1]:
            page = [(int(x / scale), int(y / scale)) for [[x, y]] in approx]
            break
    if page:
        tl, tr, br, bl = _order(page)
        width = int(max(np.hypot(*np.subtract(br, bl)), np.hypot(*np.subtract(tr, tl))))
        height = int(max(np.hypot(*np.subtract(tr, br)), np.hypot(*np.subtract(tl, bl))))
        matrix = cv2.getPerspectiveTransform(np.float32([tl, tr, br, bl]),
                                             np.float32([[0, 0], [width - 1, 0], [width - 1, height - 1],
                                                         [0, height - 1]]))
        flat = cv2.warpPerspective(frame, matrix, (width, height))
        corners = [tl, tr, br, bl]
    else:
        flat, corners, width, height = frame, [], w, h
    out = cv2.cvtColor(flat, cv2.COLOR_BGR2GRAY)
    if clean:
        # even out a shadow across the page, then black text on white
        out = cv2.adaptiveThreshold(out, 255, cv2.ADAPTIVE_THRESH_GAUSSIAN_C, cv2.THRESH_BINARY, 31, 15)
    ok, png = cv2.imencode(".png", out)
    if not ok:
        raise ValueError("OpenCV could not encode the result")
    return PageResult(png.tobytes(), bool(page), corners, width, height)
