"""look_at_image: a picture file, described by the local vision model.

What is pinned here is the order and the path, not a model's words: consent
first (nothing is read without the vision sense), then a picture normalised to
a bounded PNG, sent through the same backend choice and by-reference child the
vision sense uses. The child is replaced so no model is needed.
"""
from __future__ import annotations

import io
import pathlib
import sys

import pytest

_REPO = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_REPO / "usr" / "lib" / "shani-chronoa"))

from shani_chronoa import capabilities  # noqa: E402
from shani_chronoa.senses import vision  # noqa: E402
from shani_chronoa.skills import look_at_image as L  # noqa: E402

pytestmark = pytest.mark.skipif(L._Image is None, reason="Pillow is not installed")


@pytest.fixture
def home(tmp_path, monkeypatch):
    h = tmp_path / "home"
    h.mkdir(exist_ok=True)
    monkeypatch.setenv("HOME", str(h))
    return h


@pytest.fixture
def picture(home):
    p = home / "chart.jpg"
    L._Image.new("RGB", (4000, 3000), (127, 209, 193)).save(p, format="JPEG")
    return p


@pytest.fixture
def allowed(monkeypatch):
    monkeypatch.setattr(L.ChronoaConfig, "sense_allowed", lambda self, sense: sense == "vision")


def test_it_is_gated_on_the_vision_sense():
    assert capabilities.GATED["look_at_image"] == "vision-sense-enabled"


def test_without_consent_nothing_is_read(home, picture, monkeypatch):
    monkeypatch.setattr(L.ChronoaConfig, "sense_allowed", lambda self, sense: False)
    monkeypatch.setattr(L.ChronoaConfig, "sense_allowed_reason", lambda self, sense: "vision is off")
    opened = []
    monkeypatch.setattr(L, "_png", lambda raw: opened.append(raw) or b"")
    assert L._run({"path": str(picture)}) == "vision is off"
    assert opened == [], "the picture was read before consent was checked"


def test_the_picture_goes_through_the_vision_senses_own_path(home, picture, allowed, monkeypatch):
    sent = {}

    def child(image, model, host, prompt, timeout, backend):
        sent.update(image=image, model=model, host=host, prompt=prompt, backend=backend)
        return "A teal rectangle."
    monkeypatch.setattr(vision, "choose_backend", lambda args, config: ("llamacpp", "qwen-vl", "http://127.0.0.1:8766"))
    monkeypatch.setattr(vision, "_describe_via_child", child)

    out = L._run({"path": str(picture), "prompt": "What colour is it?"})

    assert out == "What chart.jpg shows, described by qwen-vl:\nA teal rectangle."
    assert sent["prompt"] == "What colour is it?" and sent["backend"] == "llamacpp"
    with L._Image.open(io.BytesIO(sent["image"])) as img:
        assert img.format == "PNG", "the model is always handed a PNG"
        assert max(img.size) == L.MAX_SIDE, f"a 4000 px picture was sent at {img.size}"


def test_a_file_that_is_not_a_picture_says_so(home, allowed):
    junk = home / "notes.png"
    junk.write_text("not a picture")
    assert L._run({"path": str(junk)}) == "notes.png is not a picture I can read."


def test_a_missing_file_says_so(home, allowed):
    assert "is not a file" in L._run({"path": str(home / "nope.png")})
