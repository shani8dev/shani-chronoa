"""Cutting a subject out, and the check that the written file really is one.

The post-condition is the interesting half. A skill that writes a file and says
so is the failure `verification.py` was written about: the sentence comes from
the code that did the work, so it cannot witness its own effect. So every case
here is asserted in both directions - a real cut-out passes, and each way a
result can be wrong (a file that was never written, a PNG with no alpha, one
transparent everywhere, one opaque everywhere, one whose subject pixels are a
flat colour) fails, with the number that gave it away in the evidence.

The PNG header is read without OpenCV on purpose: "this file has no alpha
channel" and "OpenCV is not installed" are different failures and must not be
reported as the same sentence.
"""

import struct
import zlib

import pytest

from shani_chronoa import matting_cutout, verification
from shani_chronoa.verification import Verdict

try:
    import cv2
    import numpy as np
except ImportError:
    cv2 = np = None
needs_cv2 = pytest.mark.skipif(cv2 is None,
                               reason="OpenCV is not importable here; the slot test covers the cut-out itself")


def _png(alpha, colour_type=6):
    """A PNG of the given alpha mask, hand-written so no extra is needed.

    `colour_type` 6 is RGBA; 2 is the same picture with no alpha channel at all,
    which is what a jpg-shaped answer looks like once decoded.
    """
    channels = 4 if colour_type == 6 else 3
    h, w = len(alpha), len(alpha[0])
    rows = b"".join(
        b"\x00" + b"".join(bytes((20, 60, 200) + ((value,) if channels == 4 else ())) for value in row)
        for row in alpha)
    def chunk(kind, data):
        return (struct.pack(">I", len(data)) + kind + data
                + struct.pack(">I", zlib.crc32(kind + data) & 0xFFFFFFFF))
    return (b"\x89PNG\r\n\x1a\n"
            + chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, colour_type, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(rows))
            + chunk(b"IEND", b""))


def _beside(tmp_path, name="dog.png"):
    """A directory of its own: `tmp_path` holds the harness's own `home`."""
    folder = tmp_path / "work"
    folder.mkdir(exist_ok=True)
    return folder


def _photo(folder, alpha):
    """A source photo and the cut-out of it, written with cv2 (same pixels, alpha added)."""
    source = folder / "dog.jpg"
    h, w = len(alpha), len(alpha[0])
    base = np.zeros((h, w, 3), np.uint8)
    base[:, :, 0], base[:, :, 1], base[:, :, 2] = 200, 60, 20
    cv2.imwrite(str(source), base)
    out = np.dstack((base, np.array(alpha, np.uint8)))
    return source, out


def _half(h=40, w=60):
    alpha = [[0] * w for _ in range(h)]
    for y in range(h // 4, h - h // 4):
        for x in range(w // 4, w - w // 4):
            alpha[y][x] = 255
    return alpha


# --- the container, read with nothing installed ------------------------------

def test_the_alpha_question_is_answered_without_opencv(tmp_path):
    rgba = tmp_path / "cut.png"
    rgba.write_bytes(_png(_half()))
    assert matting_cutout.png_alpha(rgba) is True
    jpg = tmp_path / "flat.jpg"
    jpg.write_bytes(b"\xff\xd8\xff" + b"x" * 40)
    assert matting_cutout.png_alpha(jpg) is None, "not a PNG is not 'no alpha'"
    assert matting_cutout.png_alpha(tmp_path / "missing.png") is None


# --- the check, both directions ----------------------------------------------

@needs_cv2
def test_a_real_cut_out_passes_and_a_broken_one_does_not(tmp_path):
    alpha = _half()
    folder = _beside(tmp_path)
    source, out = _photo(folder, alpha)
    good = folder / "dog-remove-background.png"
    cv2.imwrite(str(good), out)
    ok, evidence = matting_cutout.check_cutout(good, source)
    assert ok and "opaque" in evidence and "match" in evidence, evidence

    blank = folder / "blank.png"
    cv2.imwrite(str(blank), np.dstack((out[..., :3], np.zeros_like(out[..., 3]))))
    assert matting_cutout.check_cutout(blank, source)[0] is False
    assert "transparent everywhere" in matting_cutout.check_cutout(blank, source)[1]

    untouched = folder / "opaque.png"
    cv2.imwrite(str(untouched), np.dstack((out[..., :3], np.full_like(out[..., 3], 255))))
    assert "never" in matting_cutout.check_cutout(untouched, source)[1]

    flat = folder / "flat.png"
    flat_subject = out.copy()
    flat_subject[..., :3] = 0
    cv2.imwrite(str(flat), flat_subject)
    rejected, why = matting_cutout.check_cutout(flat, source)
    assert rejected is False and "94/255" in why, why
    # The same flat subject, checked with no source to compare against: caught by
    # the colour count instead, so both halves of the check are exercised.
    assert "replaced" in matting_cutout.check_cutout(flat)[1]

    absent, why = matting_cutout.check_cutout(folder / "never-written.png", source)
    assert absent is False and "no cut-out was written" in why


def test_a_png_with_no_alpha_channel_is_refused(tmp_path):
    """Answered from the IHDR alone, so it does not need OpenCV to be told apart."""
    folder = _beside(tmp_path)
    flat = folder / "opaque-rgb.png"
    flat.write_bytes(_png(_half(), colour_type=2))
    ok, why = matting_cutout.check_cutout(flat)
    assert ok is False and "no alpha channel" in why


@needs_cv2
def test_the_skill_post_condition_verifies_and_fails_on_the_real_files(tmp_path):
    """`verification.verify`, not the check called directly: the dispatch path is the point."""
    alpha = _half()
    folder = _beside(tmp_path)
    source, out = _photo(folder, alpha)
    arguments = {"action": "effect", "effect": "remove_background", "path": str(source)}

    verified = verification.verify("shani_chronoa.skills.photo_video", arguments, tool="photo_video")
    assert verified.verdict is Verdict.FAILED, "nothing was written, so nothing is verified"

    cv2.imwrite(str(folder / "dog-remove-background.png"), out)
    verified = verification.verify("shani_chronoa.skills.photo_video", arguments, tool="photo_video")
    assert verified.verdict is Verdict.VERIFIED, verified.evidence

    cv2.imwrite(str(folder / "dog-remove-background.png"),
                np.dstack((out[..., :3], np.zeros_like(out[..., 3]))))
    assert verification.verify("shani_chronoa.skills.photo_video",
                               arguments, tool="photo_video").verdict is Verdict.FAILED


def test_actions_that_wrote_no_cut_out_are_unverified_not_failed(tmp_path):
    from shani_chronoa.skills import photo_video

    picture = _beside(tmp_path) / "a.png"
    picture.write_bytes(_png(_half()))
    for arguments in ({"action": "identify", "path": str(picture)},
                      {"action": "keyframes", "path": str(picture)},
                      {"action": "effect", "effect": "blur_faces", "path": str(picture)}):
        assert photo_video.POST_CONDITION(arguments) is None, arguments
    assert photo_video.POST_CONDITION({"action": "effect", "effect": "remove_background",
                                       "path": str(picture)})[0] is False


# --- routing: the refusal, and the shape of the answer ----------------------

def test_the_skill_refuses_by_name_and_writes_nothing(tmp_path, monkeypatch):
    from shani_chronoa.opencv import runtime
    from shani_chronoa.skills import photo_video

    monkeypatch.setattr(runtime, "problem", lambda: "")
    monkeypatch.setattr("shani_chronoa.segmentation_u2net.problem",
                        lambda: "onnxruntime for Python is not installed (Arch: pacman -S "
                                "python-onnxruntime-cpu)")
    picture = _beside(tmp_path) / "dog.png"
    picture.write_bytes(_png(_half()))
    # The reader and the writer are stubbed so this asserts the refusal, not the
    # decoding - and the writer records, so "nothing was written" is observed.
    read, written = [], []
    monkeypatch.setattr(photo_video, "_read_photo", lambda path: read.append(path) or object())
    monkeypatch.setattr(photo_video, "_write_photo", lambda image, target: written.append(target))
    said = photo_video._run({"action": "effect", "effect": "remove_background", "subject": "object",
                             "path": str(picture)})
    assert "onnxruntime" in said and "pacman -S python-onnxruntime-cpu" in said
    assert "behind people still works" in said
    assert len(read) == 1 and written == [], "a refusal wrote a file"


def test_a_subject_that_does_not_apply_is_refused_rather_than_ignored(tmp_path, monkeypatch):
    from shani_chronoa.opencv import runtime
    from shani_chronoa.skills import photo_video

    monkeypatch.setattr(runtime, "problem", lambda: "")
    picture = _beside(tmp_path) / "dog.png"
    picture.write_bytes(_png(_half()))
    for arguments, said in (
        ({"action": "effect", "effect": "remove_background", "subject": "dog", "path": str(picture)},
         "subject must be person or object"),
        ({"action": "effect", "effect": "blur_background", "subject": "object", "path": str(picture)},
         "only remove_background takes one"),
    ):
        assert said in photo_video._run(arguments), photo_video._run(arguments)
    assert sorted(p.name for p in _beside(tmp_path).iterdir()) == ["dog.png"]


@needs_cv2
def test_a_cut_out_of_a_jpg_is_saved_as_a_png(tmp_path, monkeypatch):
    """A jpg has no alpha channel, so a cut-out saved as one is a confident wrong answer."""
    from shani_chronoa.opencv import runtime
    from shani_chronoa.skills import photo_video

    monkeypatch.setattr(runtime, "problem", lambda: "")
    folder = _beside(tmp_path)
    source = folder / "dog.jpg"
    gradient = np.dstack([np.full((40, 60, c), v, np.uint8) for c, v in ((0, 200), (1, 60), (2, 20))])
    gradient[:, :, 0] = np.linspace(0, 255, 60, dtype=np.uint8)[None, :]
    cv2.imwrite(str(source), gradient)

    def _people(image):
        out = cv2.cvtColor(image, cv2.COLOR_BGR2BGRA)
        out[..., 3] = 0
        out[10:30, 15:45, 3] = 255
        return out

    monkeypatch.setattr("shani_chronoa.opencv.effects.remove_background", _people)
    said = photo_video._run({"action": "effect", "effect": "remove_background", "path": str(source)})
    assert said.startswith("Saved dog.jpg with the background removed")
    assert "PP-HumanSeg" in said
    written = folder / "dog-remove-background.png"
    ok, evidence = matting_cutout.check_cutout(written, source)
    assert ok, evidence


@needs_cv2
def test_the_object_path_end_to_end_with_a_stubbed_model(tmp_path, monkeypatch):
    """The whole skill path for subject=object, with only the model stubbed out."""
    from shani_chronoa.opencv import runtime
    from shani_chronoa.skills import photo_video

    monkeypatch.setattr(runtime, "problem", lambda: "")
    monkeypatch.setattr("shani_chronoa.segmentation_u2net.problem", lambda: "")

    def _mask(image):
        mask = np.zeros(image.shape[:2], np.float32)
        mask[10:30, 15:45] = 1.0
        return mask

    monkeypatch.setattr("shani_chronoa.segmentation_u2net.alpha_mask", _mask)
    folder = _beside(tmp_path)
    source = folder / "book.png"
    book = np.dstack([np.full((40, 60, c), v, np.uint8) for c, v in ((0, 10), (1, 10), (2, 240))])
    book[:, :, 2] = np.linspace(0, 255, 60, dtype=np.uint8)[None, :]
    cv2.imwrite(str(source), book)
    said = photo_video._run({"action": "effect", "effect": "remove_background", "subject": "object",
                             "path": str(source)})
    assert "u2net" in said, said
    written = folder / "book-remove-background.png"
    ok, evidence = matting_cutout.check_cutout(written, source)
    assert ok, evidence
    assert verification.verify("shani_chronoa.skills.photo_video",
                               {"action": "effect", "effect": "remove_background", "subject": "object",
                                "path": str(source)}, tool="photo_video").verdict is Verdict.VERIFIED