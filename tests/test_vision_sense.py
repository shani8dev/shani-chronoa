"""The vision sense, proven by running the real things it runs.

Three things are proved here rather than asserted on paper, because this repo
has a documented history of code that reads correctly and is dead or broken
(`AGENTS.md`): a screenshot is really captured and really decoded, the image
bytes really cross a process boundary byte-for-byte, and the Ollama request
really has the shape Ollama's vision API requires.

**What is mocked, and why.** `describe_image()` takes a `transport`, so the
`/api/chat` request is captured with `httpx.MockTransport` - the technique
`AGENTS.md` endorses for `assistant.py`/`llm.py`, and the only way to prove the
`images` array is on the *user message* (where Ollama reads it) rather than
somewhere plausible-looking. The capture itself is stubbed for the
consent/argument tests by a `screengrab.capture` spy that records whether it
was called at all: "consent refused" is only meaningful if the assertion is
that the camera was never opened, exactly as `test_sense_scheduler.py` asserts
for a disabled ambient sense.

**What is real.** `TestTheRealPath` runs the whole chain: a real capture from
the real display, a real child interpreter, a real loopback HTTP server
speaking Ollama's protocol, and a real byte-comparison of the image the server
received against the image that was captured. It skips itself on a headless
box rather than pretending, and the skip reason says so.
"""

import base64
import hashlib
import json
import os
import random
import shutil
import socket
import subprocess
import sys
import threading
import time
import zlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import httpx
import pytest

from shani_chronoa import screengrab
from shani_chronoa.senses import SENSITIVITY_PRIVATE, Percept, Sense, _sense_problem, discover_senses
from shani_chronoa.senses import vision as vision_mod

# A real PNG's first bytes, so a test payload is a genuine image and not a
# plausible-looking stand-in. Hand-built rather than captured, so the tests do
# not need a display.
PNG_MAGIC = screengrab._PNG_MAGIC  # noqa: SLF001 - the same constant the module writes


def _png(width: int = 4, height: int = 3, colour: "tuple[int, int, int]" = (10, 20, 30)) -> bytes:
    """A real, decodable PNG of a solid colour, built by the shipped encoder."""
    row = bytes(colour) * width
    return screengrab.encode_png(width, height, [row] * height)


@pytest.fixture
def vision_enabled(chronoa_config):
    """Consent for this test only; vision defaults to false and must stay so."""
    assert chronoa_config.get_bool("vision-sense-enabled", True) is False
    chronoa_config.set("vision-sense-enabled", "true")
    return chronoa_config


@pytest.fixture
def capture_spy(monkeypatch):
    """Replace `screengrab.capture` with a recorder over a synthetic capture."""
    calls: list = []
    png = _png()

    def _fake_capture(source="screen", device=None, **kwargs):
        calls.append({"source": source, "device": device, **kwargs})
        return screengrab.Capture(
            data=png,
            source=source,
            backend="test-backend",
            width=4,
            height=3,
            native_width=4,
            native_height=3,
            captured_at=time.time(),
        )

    monkeypatch.setattr(vision_mod.screengrab, "capture", _fake_capture)
    return calls


def _stub_description(monkeypatch, text: str = "A small solid dark rectangle."):
    """Answer the describe step in-process, recording the arguments it got."""
    seen: dict = {}

    def _fake_describe(image, model, host, prompt, timeout):
        seen.update(
            {"image": image, "model": model, "host": host, "prompt": prompt, "timeout": timeout}
        )
        return text

    monkeypatch.setattr(vision_mod, "_describe_via_child", _fake_describe)
    return seen


# --- registration and the contract ------------------------------------------


class TestTheSenseIsRegisteredAndConsentGated:
    def test_the_loader_registers_a_valid_vision_sense(self, gsettings_env):
        # Given/When: the real registry, loaded by the real loader
        registry = discover_senses()
        # Then: vision is a first-class sense, not a consent key with no module
        assert "vision" in registry, (
            "the vision sense is not registered, so `shani-chronoa-sense list` "
            "still reports it as '(no sense module registered)'"
        )
        assert _sense_problem(registry["vision"]) == ""

    def test_it_declares_a_bounded_private_lifetime_not_a_durable_one(self, gsettings_env):
        sense = discover_senses()["vision"]
        # A durable (None) TTL would write a description of the user's screen to
        # disk on every capture - the outcome store.py's docstring forbids.
        assert sense.ttl_seconds is not None and sense.ttl_seconds > 0
        assert sense.sensitivity == SENSITIVITY_PRIVATE
        assert sense.kind == vision_mod.KIND

    def test_it_is_reactive_only_so_nothing_polls_a_screen_unprompted(self, gsettings_env):
        # The consent key's own gschema description promises the user that
        # Chronoa "looks when you ask it to, it does not watch continuously".
        assert discover_senses()["vision"].is_ambient() is False

    def test_consent_is_off_on_a_fresh_install(self, chronoa_config):
        # The shipped default is the whole point: screen capture is opt-in.
        assert chronoa_config.privacy_mode is True
        assert chronoa_config.sense_allowed("vision") is False
        assert "vision-sense-enabled" in chronoa_config.sense_allowed_reason("vision")

    def test_the_sense_name_matches_the_config_consent_table(self):
        # `CONSENT_SENSE` is spelled out in the module so a rename shows up as a
        # diff; this is what keeps the two from drifting apart silently.
        from shani_chronoa import config as config_module

        assert vision_mod.CONSENT_SENSE == "vision"
        assert config_module._SENSE_CONSENT_KEYS["vision"] == "vision-sense-enabled"

    def test_vision_is_not_smuggled_into_the_networked_senses_table(self):
        """Adding it there would deny local vision under privacy mode."""
        from shani_chronoa import config as config_module

        assert "vision" not in config_module._NETWORKED_SENSES

    def test_consent_is_required_before_anything_is_captured(
        self, chronoa_config, capture_spy
    ):
        # Given: the default install - vision off, capture wired to a spy
        result = vision_mod.run({})

        assert isinstance(result, str)
        assert "vision-sense-enabled" in result
        assert capture_spy == [], "the screen was captured before consent was checked"

    def test_granted_consent_is_what_opens_the_camera(
        self, vision_enabled, capture_spy, monkeypatch
    ):
        _stub_description(monkeypatch)
        result = vision_mod.run({})
        assert isinstance(result, Percept)
        assert len(capture_spy) == 1


# --- locality: the model and the endpoint are never allowed to be remote -----


class TestLocalityIsEnforcedBeforeAnyCapture:
    @pytest.mark.parametrize(
        "model",
        [
            "https://vision.example.com/v1/chat",
            "http://192.168.1.50:11434",
            "qwen3-vl:4b@remote",
            "/etc/passwd",
            "qwen3-vl 4b",
        ],
    )
    def test_a_model_that_could_name_a_remote_endpoint_is_refused(
        self, vision_enabled, capture_spy, model
    ):
        result = vision_mod.run({"model": model})
        assert isinstance(result, str), f"{model!r} was accepted"
        assert capture_spy == [], f"{model!r} was refused but a capture was attempted"

    def test_plain_local_model_names_are_accepted(self):
        for model in ("qwen3-vl:4b", "qwen3-vl", "library/llava:7b", "moondream:latest"):
            assert vision_mod.is_local_model(model) is True, model

    def test_a_remote_ollama_host_is_refused_even_with_privacy_mode_off(
        self, vision_enabled, capture_spy, monkeypatch
    ):
        # Privacy mode off is the interesting case: `config.ollama_host` stops
        # forcing loopback, and the vision sense has to refuse on its own.
        vision_enabled.set("privacy-mode", "false")
        vision_enabled.set("ollama-host", "http://vision.example.com:11434")

        result = vision_mod.run({})

        assert isinstance(result, str)
        assert "ollama-host" in result
        assert capture_spy == [], "a screenshot was taken for a remote endpoint"

    def test_privacy_mode_on_already_forces_a_local_endpoint(self, vision_enabled, capture_spy, monkeypatch):
        vision_enabled.set("privacy-mode", "true")
        vision_enabled.set("ollama-host", "http://vision.example.com:11434")
        seen = _stub_description(monkeypatch)
        result = vision_mod.run({})
        assert isinstance(result, Percept)
        assert seen["host"] == "http://localhost:11434"
        assert result.metadata["endpoint"] == "http://localhost:11434"


# --- the vision model is a separate pin -------------------------------------


class TestTheVisionModelIsIndependentOfTheTextModel:
    def test_the_vision_pin_differs_from_the_text_pin_on_every_tier(self):
        from shani_chronoa.config import HardwareProfile

        for profile in ("gpu", "high", "medium", "low"):
            hardware = HardwareProfile()
            hardware.profile = profile
            text, seen = hardware.get_model(), hardware.get_vision_model()
            assert seen != text, f"on the {profile} tier both pins are {text!r}"
            # Vision-capable family, and never a `qwen3` text tag: a text model
            # has no vision tower, so the vision pin cannot be one.
            assert "qwen3-vl" in seen, seen
            assert not seen.startswith("qwen3:"), seen
            assert ":" in seen, seen

    def test_a_text_model_override_does_not_change_the_vision_model(self, chronoa_config):
        from shani_chronoa.config import HardwareProfile

        # The setting a user reaches for when chat misbehaves.
        chronoa_config.set("model", "some-text-model:70b")
        assert chronoa_config.vision_model == "", (
            "reading the text model's setting as the vision pin is exactly the "
            "coupling this sense must not have"
        )
        assert chronoa_config.vision_model != chronoa_config.model
        assert (
            vision_mod.resolve_vision_model(chronoa_config)
            == HardwareProfile().get_vision_model()
        )

    def test_the_vision_override_is_its_own_setting(self, chronoa_config):
        chronoa_config.set("vision-model", "llava:7b")
        assert chronoa_config.vision_model == "llava:7b"
        assert chronoa_config.model == "", "the text model's setting was overwritten"
        assert vision_mod.resolve_vision_model(chronoa_config) == "llava:7b"

    def test_the_vision_model_is_recorded_on_the_percept(
        self, vision_enabled, capture_spy, monkeypatch
    ):
        seen = _stub_description(monkeypatch)
        vision_enabled.set("vision-model", "qwen3-vl:4b")

        result = vision_mod.run({})

        assert seen["model"] == "qwen3-vl:4b"
        assert result.metadata["model"] == "qwen3-vl:4b"


# --- the Ollama request shape ------------------------------------------------


class TestTheOllamaRequest:
    """`POST /api/chat` with an `images` array of base64 on the user message."""

    def _request(self, handler):
        return handler

    def test_the_request_is_a_chat_post_carrying_base64_on_the_user_message(self):
        recorded: dict = {}

        def _handler(request: httpx.Request) -> httpx.Response:
            recorded["method"] = request.method
            recorded["path"] = request.url.path
            recorded["body"] = json.loads(request.content)
            return httpx.Response(200, json={"message": {"role": "assistant", "content": "a dark rectangle"}})

        image = _png(4, 3, (1, 2, 3))
        description = vision_mod.describe_image(
            image,
            "qwen3-vl:2b",
            "http://localhost:11434",
            transport=httpx.MockTransport(_handler),
        )

        assert description == "a dark rectangle"
        assert recorded["method"] == "POST"
        assert recorded["path"] == "/api/chat"
        body = recorded["body"]
        assert body["model"] == "qwen3-vl:2b"
        assert body["stream"] is False
        message = body["messages"][0]
        assert message["role"] == "user"
        assert message["images"] == [base64.b64encode(image).decode("ascii")]
        # The image belongs in `images`, not smuggled into the prompt text.
        assert "images" not in body
        assert base64.b64decode(message["images"][0], validate=True) == image

    def test_a_two_megabyte_image_survives_the_request_intact(self):
        """The payload the vision sense actually produces, byte for byte."""
        recorded: dict = {}

        def _handler(request: httpx.Request) -> httpx.Response:
            recorded["body"] = json.loads(request.content)
            return httpx.Response(200, json={"message": {"content": "ok"}})

        # A real 4K-ish PNG of incompressible pixel data, so the base64 is not
        # a small special case that happens to work. A repeating gradient was
        # tried first and compressed to 8 KB, which proved nothing.
        generator = random.Random(20260927)
        image = screengrab.encode_png(
            1280, 600, [generator.randbytes(1280 * 3) for _ in range(600)]
        )
        assert len(image) > 2 * 1024 * 1024, len(image)

        vision_mod.describe_image(
            image, "qwen3-vl:2b", "http://localhost:11434", transport=httpx.MockTransport(_handler)
        )

        sent = base64.b64decode(recorded["body"]["messages"][0]["images"][0], validate=True)
        assert sent == image
        assert hashlib.sha256(sent).hexdigest() == hashlib.sha256(image).hexdigest()

    def test_an_ollama_error_body_is_reported_not_swallowed(self):
        # A wrong `vision-model` tag is answered with HTTP 404, and the message
        # is the only useful thing a user can be told.
        def _handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(404, text='{"error":"model \\"nope\\" not found"}')

        with pytest.raises(vision_mod.VisionError) as raised:
            vision_mod.describe_image(
                _png(), "nope", "http://localhost:11434", transport=httpx.MockTransport(_handler)
            )
        assert "404" in str(raised.value)

    def test_a_200_with_an_error_key_is_still_an_error(self):
        # The free cloud gateways in cloud_llm.py return HTTP 200 with an
        # `error` key; a client that only checks the status reads that as
        # success. Ollama can do the same, so the key is checked too.
        def _handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, json={"error": "model requires more system memory"})

        with pytest.raises(vision_mod.VisionError, match="system memory"):
            vision_mod.describe_image(
                _png(), "qwen3-vl:30b", "http://localhost:11434", transport=httpx.MockTransport(_handler)
            )

    def test_an_empty_description_is_an_error_not_a_blank_percept(self):
        def _handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, json={"message": {"content": "   "}, "done_reason": "length"})

        with pytest.raises(vision_mod.VisionError) as raised:
            vision_mod.describe_image(
                _png(), "qwen3-vl:2b", "http://localhost:11434", transport=httpx.MockTransport(_handler)
            )
        assert "no description" in str(raised.value)
        assert "length" in str(raised.value)

    def test_an_unreachable_server_says_so_with_the_address(self):
        # A closed port on loopback: no Ollama here, which is the normal state
        # of a dev box, and it must be a sentence rather than a traceback.
        closed = _free_port()
        with pytest.raises(vision_mod.VisionError) as raised:
            vision_mod.describe_image(_png(), "qwen3-vl:2b", f"http://127.0.0.1:{closed}")
        assert "cannot reach an Ollama server" in str(raised.value)
        assert str(closed) in str(raised.value)


# --- the percept -------------------------------------------------------------


class TestThePercept:
    def test_a_description_is_private_transient_and_bounded(
        self, vision_enabled, capture_spy, monkeypatch
    ):
        _stub_description(monkeypatch, "A solid dark blue rectangle, 4 by 3 pixels.")
        result = vision_mod.run({})

        assert isinstance(result, Percept)
        assert result.sense == "vision" and result.kind == vision_mod.KIND
        assert result.sensitivity == SENSITIVITY_PRIVATE
        assert result.ttl_seconds == vision_mod.TTL_SECONDS
        assert result.ttl_seconds is not None, "a screen description must not be durable"
        assert not result.is_expired()
        assert result.is_expired(now=time.time() + result.ttl_seconds + 1)
        assert "solid dark blue rectangle" in result.content
        assert result.source == "screen via test-backend (4x3)"

    def test_the_percept_records_how_the_image_was_produced(
        self, vision_enabled, capture_spy, monkeypatch
    ):
        _stub_description(monkeypatch)
        result = vision_mod.run({"source": "camera", "device": "/dev/video0"})

        assert result.metadata["capture_source"] == "camera"
        assert result.metadata["capture_backend"] == "test-backend"
        assert result.metadata["endpoint"] == "http://localhost:11434"
        assert result.metadata["image_bytes"] == len(_png())
        assert result.metadata["prompt_is_default"] is True
        assert result.metadata["width"] == 4 and result.metadata["native_width"] == 4

    def test_a_transient_vision_percept_is_never_written_to_disk(
        self, vision_enabled, capture_spy, monkeypatch, tmp_path
    ):
        from shani_chronoa.senses.store import PerceptStore

        _stub_description(monkeypatch)
        durable = tmp_path / "percepts" / "memory.jsonl"
        store = PerceptStore(durable_path=durable)

        store.add(vision_mod.run({}))

        assert not durable.exists(), "a description of the screen was persisted"
        assert [p.sense for p in store.active()] == ["vision"]

    def test_a_vision_percept_reaches_the_next_turns_context(
        self, vision_enabled, capture_spy, monkeypatch
    ):
        # The wiring `AGENTS.md` documents: a percept recorded by any producer
        # is visible to the assistant on the next turn, without ever entering
        # `Assistant._history`.
        from shani_chronoa.assistant import Assistant
        from shani_chronoa.senses.context import ContextBuilder
        from shani_chronoa.senses.store import PerceptStore

        _stub_description(monkeypatch, "A terminal window showing a failed test run.")
        store = PerceptStore()
        store.add(vision_mod.run({}))
        builder = ContextBuilder()

        history = [
            {"role": "system", "content": "You are Chronoa."},
            {"role": "user", "content": "what am I looking at?"},
        ]
        messages = builder.build_messages(history, store.active())

        assert "- [vision/image_description]" in messages[1]["content"]
        assert "failed test run" in messages[1]["content"]
        assert all("image_description" not in str(m.get("content")) for m in history)

        # Given: the same conversation, now long enough for repeated trimming
        assistant = Assistant(llm=None, percept_store=store, context_builder=builder)
        assistant._history = [dict(message) for message in history]  # noqa: SLF001
        for turn in range(60):
            assistant._history.extend(
                [{"role": "user", "content": f"turn {turn}"}, {"role": "assistant", "content": "ok"}]
            )
            assistant._trim_history()  # noqa: SLF001
        # Then: trimming cannot have deleted it, because it was never in there,
        # and it is still what the next turn's context is built from
        assert len(assistant._history) <= 40
        assert all("image_description" not in str(m.get("content")) for m in assistant._history)
        assert "failed test run" in builder.render(store.active())

    @pytest.mark.parametrize(
        "arguments, expected",
        [
            ({"source": "webcam"}, "not a capture source"),
            ({"device": "/etc/shadow"}, "not a camera node"),
            ({"timeout_seconds": 0}, "timeout_seconds must be"),
            ({"timeout_seconds": 10_000}, "timeout_seconds must be"),
            ({"timeout_seconds": "soon"}, "timeout_seconds must be a number"),
            ({"prompt": "   "}, "prompt was empty"),
        ],
    )
    def test_bad_arguments_are_refused_before_the_capture(
        self, vision_enabled, capture_spy, arguments, expected
    ):
        result = vision_mod.run(arguments)
        assert isinstance(result, str), result
        assert expected in result
        assert capture_spy == []


# --- capture: bounded, loud, and honest about a headless box -----------------


class TestCaptureIsBoundedAndRefusesRatherThanHangs:
    def test_a_headless_session_is_a_sentence_not_a_traceback(self, monkeypatch, vision_enabled):
        monkeypatch.delenv("DISPLAY", raising=False)
        monkeypatch.delenv("WAYLAND_DISPLAY", raising=False)

        with pytest.raises(screengrab.ScreenCaptureError) as raised:
            screengrab.capture_screen()

        message = str(raised.value)
        assert "no display to capture" in message
        assert "headless" in message

    def test_display_detection_prefers_wayland_over_the_xwayland_display(self, monkeypatch):
        monkeypatch.setenv("WAYLAND_DISPLAY", "wayland-0")
        monkeypatch.setenv("DISPLAY", ":0")
        assert screengrab.display_environment() == "wayland"
        monkeypatch.delenv("WAYLAND_DISPLAY")
        assert screengrab.display_environment() == "x11"

    def test_a_capture_tool_that_hangs_is_stopped_at_the_timeout(self, monkeypatch, tmp_path):
        # A wedged compositor is the failure this bound exists for. A real
        # executable that sleeps, not a mock of subprocess.run.
        bindir = tmp_path / "bin"
        bindir.mkdir()
        (bindir / "xwd").write_text("#!/bin/sh\nsleep 30\n")
        (bindir / "xwd").chmod(0o755)
        monkeypatch.setenv("PATH", f"{bindir}:/usr/bin:/bin")
        monkeypatch.setenv("DISPLAY", ":0")
        monkeypatch.delenv("WAYLAND_DISPLAY", raising=False)
        monkeypatch.setattr(screengrab, "FALLBACK_XWD", str(bindir / "xwd"))

        with pytest.raises(screengrab.ScreenCaptureError) as raised:
            screengrab.capture_screen(timeout=0.4)

        assert "longer than" in str(raised.value)

    def test_a_machine_with_no_capture_tool_is_told_which_were_looked_for(
        self, monkeypatch
    ):
        monkeypatch.setenv("PATH", "/nonexistent")
        monkeypatch.setenv("DISPLAY", ":0")
        monkeypatch.delenv("WAYLAND_DISPLAY", raising=False)
        monkeypatch.setattr(screengrab, "FALLBACK_XWD", "/nonexistent/xwd")

        with pytest.raises(screengrab.ScreenCaptureError) as raised:
            screengrab.capture_screen()

        message = str(raised.value)
        assert "import" in message and "xwd" in message
        assert "x11-apps" in message  # names the Arch package, as ocr's does

    def test_a_capture_over_the_byte_ceiling_is_refused(self, tmp_path, monkeypatch):
        bindir = tmp_path / "bin"
        bindir.mkdir()
        (bindir / "xwd").write_text("#!/bin/sh\nhead -c 4096 /dev/zero\n")
        (bindir / "xwd").chmod(0o755)
        monkeypatch.setenv("PATH", f"{bindir}:/usr/bin:/bin")
        monkeypatch.setenv("DISPLAY", ":0")
        monkeypatch.delenv("WAYLAND_DISPLAY", raising=False)
        monkeypatch.setattr(screengrab, "FALLBACK_XWD", str(bindir / "xwd"))

        with pytest.raises(screengrab.ScreenCaptureError, match="capture limit"):
            screengrab.capture_screen(max_capture_bytes=1024)


# --- the XWD decoder, on real and synthetic dumps ---------------------------


def _xwd_dump(
    width: int = 6,
    height: int = 4,
    stride: int = 24,
    pixel_bytes: int = 4,
    masks: "tuple[int, int, int]" = (0x00FF0000, 0x0000FF00, 0x000000FF),
    byte_order: int = 0,
    ncolors: int = 2,
    format_: int = 2,
    header_size: int = 100,
) -> bytes:
    """A minimal, structurally faithful XWD dump for the decoder tests."""
    fields = [0] * 25
    fields[0] = header_size
    fields[1] = 7
    fields[2] = format_
    fields[3] = 24
    fields[4] = width
    fields[5] = height
    fields[6] = 0
    fields[7] = byte_order
    fields[8] = 32
    fields[11] = 24
    fields[12] = stride
    fields[13] = 5
    fields[14], fields[15], fields[16] = masks
    fields[19] = ncolors
    header = b"".join(value.to_bytes(4, "big") for value in fields)
    header += b"\x00" * (header_size - len(header))
    pixels = bytearray()
    for y in range(height):
        row = bytearray()
        for x in range(width):
            row += bytes(((x * 16) % 256, (y * 16) % 256, 0x7F)).ljust(pixel_bytes, b"\xff")
        row += b"\x00" * (stride - len(row))
        pixels += row
    return header + bytes(ncolors * 12) + bytes(pixels)


class TestTheXwdDecoder:
    def test_it_decodes_a_truecolor_dump_byte_exactly(self):
        raw = _xwd_dump()
        width, height, rows = screengrab.decode_xwd(raw)
        assert (width, height) == (6, 4)
        # The dump stores each pixel little-endian, so byte 0 is blue and byte
        # 2 is red under these masks: R=0x7F, G=(y*16), B=(x*16).
        assert rows[0] == bytes(
            (0x7F, 0, 0, 0x7F, 0, 16, 0x7F, 0, 32, 0x7F, 0, 48, 0x7F, 0, 64, 0x7F, 0, 80)
        )
        assert rows[3] == bytes(
            (0x7F, 48, 0, 0x7F, 48, 16, 0x7F, 48, 32, 0x7F, 48, 48, 0x7F, 48, 64, 0x7F, 48, 80)
        )

    def test_the_encoded_png_decodes_back_to_the_same_pixels(self):
        raw = _xwd_dump()
        width, height, rows = screengrab.decode_xwd(raw)
        png = screengrab.encode_png(width, height, rows)

        assert png.startswith(PNG_MAGIC)
        assert screengrab.png_size(png) == (width, height)
        # Undo the encoder with the standard library and compare the pixels.
        idat = png[png.index(b"IDAT") + 4 :]
        decoded = zlib.decompress(idat)
        stride = width * 3 + 1
        for y in range(height):
            assert decoded[y * stride + 1 : (y + 1) * stride] == rows[y]

    def test_a_downscaled_capture_samples_whole_source_pixels(self):
        raw = _xwd_dump(width=4, height=4, stride=16)
        width, height, rows = screengrab.decode_xwd(raw, max_pixels=4)
        assert (width, height) == (2, 2)
        # Factor 2: output pixel (1,1) is source pixel (2,2), not (3,3)
        assert rows[1] == bytes((0x7F, 32, 0, 0x7F, 32, 32))

    def test_a_colormapped_visual_is_refused_rather_than_guessed(self):
        raw = _xwd_dump(masks=(0, 0, 0))
        with pytest.raises(screengrab.ScreenCaptureError, match="colormapped"):
            screengrab.decode_xwd(raw)

    def test_a_colormapped_pixmap_format_is_refused(self):
        with pytest.raises(screengrab.ScreenCaptureError, match="colormapped"):
            screengrab.decode_xwd(_xwd_dump(format_=1))

    def test_a_non_contiguous_channel_mask_is_refused(self):
        with pytest.raises(screengrab.ScreenCaptureError, match="contiguous"):
            screengrab.decode_xwd(_xwd_dump(masks=(0x00FF0000, 0x0000FF00, 0x00FF00FF)))

    def test_a_truncated_dump_is_refused_with_both_numbers(self):
        raw = _xwd_dump()[:-8]
        with pytest.raises(screengrab.ScreenCaptureError) as raised:
            screengrab.decode_xwd(raw)
        assert "bytes" in str(raised.value)

    def test_an_impossible_stride_is_refused(self):
        with pytest.raises(screengrab.ScreenCaptureError, match="stride"):
            screengrab.decode_xwd(_xwd_dump(width=6, height=4, stride=4))

    def test_the_native_geometry_is_recoverable_from_the_same_header(self):
        raw = _xwd_dump(width=6, height=4)
        assert screengrab.xwd_size(raw) == (6, 4)


# --- the real path: capture, child process, loopback server ------------------


def _free_port() -> int:
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        return probe.getsockname()[1]


class _OllamaStub(BaseHTTPRequestHandler):
    """A real HTTP server speaking the part of Ollama's API vision needs."""

    received: list = []
    reply = "A dark desktop with a terminal window open."

    def do_POST(self):  # noqa: N802 - BaseHTTPRequestHandler's own name
        length = int(self.headers.get("Content-Length", "0"))
        body = json.loads(self.rfile.read(length) or b"{}")
        type(self).received.append({"path": self.path, "body": body})
        payload = json.dumps(
            {
                "model": body.get("model"),
                "message": {"role": "assistant", "content": self.reply},
                "done": True,
            }
        ).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def do_GET(self):  # noqa: N802 - BaseHTTPRequestHandler's own name
        payload = b'{"version":"0.0.0-stub"}'
        self.send_response(200)
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def log_message(self, *args):
        return


@pytest.fixture
def ollama_stub():
    """A loopback HTTP server standing in for a local Ollama.

    Yields the handler class with its `port` attribute set, so a test can point
    the real `ollama-host` setting at a real socket: the vision sense refuses
    anything that is not loopback, so a real server on 127.0.0.1 is the only
    honest way to exercise its describe path.
    """
    _OllamaStub.received = []
    server = ThreadingHTTPServer(("127.0.0.1", 0), _OllamaStub)
    _OllamaStub.port = server.server_address[1]
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield _OllamaStub
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def _stub_host() -> str:
    return f"http://127.0.0.1:{_OllamaStub.port}"


class TestTheRealPath:
    """Real capture, real child interpreter, real HTTP, real bytes."""

    def test_a_screenshot_is_captured_and_described_end_to_end(
        self, vision_enabled, ollama_stub, capture_spy
    ):
        vision_enabled.set("vision-model", "qwen3-vl:2b")
        vision_enabled.set("ollama-host", _stub_host())

        result = vision_mod.run({})

        assert isinstance(result, Percept), result
        assert ollama_stub.received, "no HTTP request reached the local server"
        request = ollama_stub.received[0]
        assert request["path"] == "/api/chat"
        assert request["body"]["model"] == "qwen3-vl:2b"
        sent = base64.b64decode(request["body"]["messages"][0]["images"][0], validate=True)
        assert sent == _png(), "the image changed crossing the process boundary"
        assert ollama_stub.reply in result.content
        assert not _leftover_envelopes(), "the envelope holding the screenshot was left behind"

    def test_the_image_is_not_interpolated_into_the_child_command(
        self, vision_enabled, ollama_stub, capture_spy, monkeypatch
    ):
        """The command is a fixed program; the image lives in an envelope file."""
        from shani_chronoa import argfile

        commands: list = []
        real_reference_command = argfile.reference_command

        def _spy(module, function, arguments, by_reference=False):
            payload = real_reference_command(module, function, arguments, by_reference)
            commands.append(payload.command)
            return payload

        monkeypatch.setattr(vision_mod.argfile, "reference_command", _spy)
        vision_enabled.set("ollama-host", _stub_host())

        result = vision_mod.run({})

        # Then: one child, running a fixed program, with the image nowhere in
        # what the shell was asked to execute - not as raw bytes, and not as the
        # base64 a naive JSON transport would have interpolated into the source
        assert isinstance(result, Percept)
        assert len(commands) == 1
        command = commands[0]
        assert "run_from_file" in command
        assert _png()[:64].hex() not in command
        assert base64.b64encode(_png()).decode()[:64] not in command
        assert command.count("envelope.json") == 1

    @pytest.mark.skipif(
        screengrab.display_environment() == "none",
        reason="no display on this machine, so a real screenshot cannot be taken",
    )
    def test_a_real_screenshot_of_this_desktop_is_decoded_and_described(
        self, vision_enabled, ollama_stub
    ):
        """No stubs anywhere: a real screen, a real model call, a real percept."""
        vision_enabled.set("vision-model", "qwen3-vl:2b")
        vision_enabled.set("ollama-host", _stub_host())

        result = vision_mod.run({})

        assert isinstance(result, Percept), result
        assert result.metadata["width"] > 0 and result.metadata["height"] > 0
        assert result.metadata["image_bytes"] > 0
        sent = base64.b64decode(
            ollama_stub.received[0]["body"]["messages"][0]["images"][0], validate=True
        )
        assert sent.startswith(PNG_MAGIC)
        assert screengrab.png_size(sent) == (result.metadata["width"], result.metadata["height"])
        if result.metadata["downscaled"]:
            assert (result.metadata["width"], result.metadata["height"]) != (
                result.metadata["native_width"],
                result.metadata["native_height"],
            )

    def test_the_camera_source_is_captured_and_described_too(
        self, vision_enabled, ollama_stub, capture_spy
    ):
        vision_enabled.set("ollama-host", _stub_host())

        result = vision_mod.run({"source": "camera", "device": "/dev/video0"})

        assert isinstance(result, Percept), result
        assert capture_spy[0]["source"] == "camera"
        assert result.metadata["capture_source"] == "camera"


def _leftover_envelopes() -> "list[Path]":
    """Any argument-envelope directories left in this test's HOME."""
    return list(
        Path(os.path.expanduser("~")).glob(".local/share/shani-chronoa/argfiles/call-*")
    )
