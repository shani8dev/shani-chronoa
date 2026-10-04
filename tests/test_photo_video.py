"""Photos and videos: what needs OpenCV, what needs nothing extra, and that each part does its job.

The split is the point. Video keyframes are ffmpeg's (every install has it) and
a look like sepia is ImageMagick's (`edit_image`); only identifying things and
the effects that depend on them need the OpenCV extra. So the ffmpeg and
routing tests run everywhere, and the OpenCV ones run where cv2 can be
imported - on the image after setup, which shani-testbed's chronoa-extras does,
or with OpenCV on PYTHONPATH here.
"""

import shutil
import subprocess

import pytest

from shani_chronoa import video_frames

needs_ffmpeg = pytest.mark.skipif(not shutil.which("ffmpeg"), reason="ffmpeg is not installed")


def _clip(tmp_path, *colours, seconds=3):
    """A video of solid colours, `seconds` each - so where the cuts are is known."""
    out = tmp_path / "clip.mp4"
    inputs, n = [], len(colours)
    for c in colours:
        inputs += ["-f", "lavfi", "-i", f"color={c}:size=160x120:duration={seconds}:rate=10"]
    subprocess.run(["ffmpeg", "-v", "error", *inputs, "-filter_complex",
                    "".join(f"[{i}]" for i in range(n)) + f"concat=n={n}:v=1", "-y", str(out)], check=True)
    return out


@needs_ffmpeg
def test_keyframes_are_the_cuts(tmp_path):
    frames = video_frames.keyframes(_clip(tmp_path, "red", "blue", "green"), tmp_path / "k")
    assert [round(f.seconds) for f in frames] == [0, 3, 6]
    assert all(f.path.is_file() and f.path.read_bytes()[:4] == b"\x89PNG" for f in frames)


@needs_ffmpeg
def test_one_long_shot_is_spread_out_instead(tmp_path):
    frames = video_frames.keyframes(_clip(tmp_path, "green", seconds=8), tmp_path / "k", limit=4)
    assert [round(f.seconds) for f in frames] == [0, 2, 4, 6]


@needs_ffmpeg
def test_keyframes_action_needs_no_opencv(tmp_path, monkeypatch):
    from shani_chronoa.opencv import runtime
    from shani_chronoa.skills import photo_video
    monkeypatch.setattr(runtime, "problem", lambda: "OpenCV is not installed")
    clip = _clip(tmp_path, "red", "blue")
    monkeypatch.setenv("HOME", str(tmp_path))
    out = photo_video._run({"action": "keyframes", "path": str(clip)})
    assert out.startswith("Saved 2 moments of clip.mp4") and "0:00.0, 0:03.0" in out
    folder = tmp_path / "clip-keyframes"
    assert sorted(p.name for p in folder.iterdir()) == ["00m00.0s.png", "00m03.0s.png"]
    # identify and effect do need it, and say so
    assert photo_video._run({"action": "identify", "path": str(clip)}) == "OpenCV is not installed"
    # describe needs the eyes, and says so
    assert "vision model" in photo_video._run({"action": "describe", "path": str(clip)})


def test_the_skill_refuses_what_it_cannot_work_on(tmp_path, monkeypatch):
    from shani_chronoa.skills import photo_video
    monkeypatch.setenv("HOME", str(tmp_path))
    doc = tmp_path / "notes.txt"
    doc.write_text("x")
    assert "not a photo or video" in photo_video._run({"action": "identify", "path": str(doc)})
    pic = tmp_path / "a.png"
    pic.write_bytes(b"\x89PNG")
    assert "is for videos" in photo_video._run({"action": "keyframes", "path": str(pic)})
    assert "action must be" in photo_video._run({"action": "nope", "path": str(pic)})


def test_plain_looks_are_imagemagick_not_opencv(tmp_path):
    from shani_chronoa.skills import edit_image
    src = tmp_path / "a.jpg"
    src.write_bytes(b"x")
    argv, _fmt, done = edit_image.build(src, {"look": "sepia"})
    assert argv[0] == "magick" and "-sepia-tone" in argv and done == ["gave it a sepia look"]
    with pytest.raises(ValueError, match="look must be"):
        edit_image.build(src, {"look": "glitter"})
    from shani_chronoa.opencv import effects
    assert not set(edit_image.LOOKS) & set(effects.EFFECTS), "one tool per effect"


def test_a_camera_scan_needs_the_camera_consent(monkeypatch):
    from shani_chronoa.skills import scan_document
    assert "not permitted" in scan_document._run({"source": "camera"})
    assert "source must be" in scan_document._run({"source": "fax"})


def test_the_zoo_models_are_pinned_and_permissive():
    from shani_chronoa.opencv import runtime
    for m in runtime.MODELS:
        assert len(m.sha256) == 64 and m.size_bytes > 100_000 and "huggingface.co/opencv/" in m.base_url
        assert any(lic in m.note for lic in ("MIT", "Apache-2.0"))
    assert 70_000_000 < runtime.DOWNLOAD_BYTES < 100_000_000


# --- with OpenCV ----------------------------------------------------------------

try:
    import cv2
    import numpy as np
except ImportError:
    cv2 = np = None
needs_cv2 = pytest.mark.skipif(cv2 is None, reason="OpenCV is not importable here (the slot test covers it)")


@needs_cv2
def test_outputs_are_paired_by_shape_whatever_their_order():
    from shani_chronoa.opencv import detect
    scores = [np.zeros((1, n * n, 80), np.float32) for n in (52, 26, 13)]
    boxes = [np.zeros((1, n * n, 32), np.float32) for n in (52, 26, 13)]
    for order in (scores + boxes, [x for pair in zip(scores, boxes) for x in pair], boxes[::-1] + scores):
        levels = detect._levels(order)
        assert [s for s, _c, _b in levels] == [8, 16, 32]
        assert all(c.shape[1] == 80 and b.shape[1] == 32 and c.shape[0] == b.shape[0] for _s, c, b in levels)


def _photographed_page():
    sheet = np.full((700, 500, 3), 245, np.uint8)
    for i in range(12):
        cv2.putText(sheet, f"Line {i + 1}: the quick brown fox", (30, 60 + i * 50), cv2.FONT_HERSHEY_SIMPLEX, 0.8,
                    (20, 20, 20), 2)
    desk = np.full((1000, 1200, 3), (40, 60, 80), np.uint8)
    src = np.float32([[0, 0], [499, 0], [499, 699], [0, 699]])
    dst = np.float32([[350, 120], [820, 200], [760, 880], [260, 790]])
    return cv2.warpPerspective(sheet, cv2.getPerspectiveTransform(src, dst), (1200, 1000), dst=desk,
                               borderMode=cv2.BORDER_TRANSPARENT), dst


@needs_cv2
def test_a_photographed_page_is_found_and_flattened():
    from shani_chronoa.opencv import page
    photo, corners = _photographed_page()
    r = page.flatten_page(cv2.imencode(".png", photo)[1].tobytes())
    assert r.found_page
    for (x, y), (ex, ey) in zip(r.corners, corners):
        assert abs(x - ex) <= 4 and abs(y - ey) <= 4
    assert abs(r.width / r.height - 500 / 700) < 0.05


@needs_cv2
def test_noise_is_not_a_page():
    from shani_chronoa.opencv import page
    noise = np.random.default_rng(1).integers(0, 255, (400, 400, 3), np.uint8)
    assert not page.flatten_page(cv2.imencode(".png", noise)[1].tobytes()).found_page
