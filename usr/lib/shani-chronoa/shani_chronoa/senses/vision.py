"""Vision sense: capture a screen (or a camera) and describe it with a local
Ollama vision model.

**This sense sees. It does not read text out of a picture** - that is
`senses/ocr.py`, and the split is deliberate in both directions: tesseract
returns glyphs and coordinates and is far better at them, while a VLM returns
"a terminal window showing a failed test run" and is far better at that. A
module that did both would be worse at each and would make "what did Chronoa
read?" unanswerable from the percept alone.

**Vision is a genuinely separate capability, in three independent places.**

- *A separate model pin.* `HardwareProfile.get_vision_model()` chooses a
  vision-capable model per hardware tier, and `ChronoaConfig.vision_model`
  overrides it through its own `vision-model` gsetting. `get_model()` (the
  `qwen3` text pin) is never consulted, and `--model=` on the command line -
  which `app.py` applies to the text LLM - cannot reach here. A text model has
  no vision tower, and a VLM is a worse tool-caller, so sharing the pin would
  be wrong in both directions.
- *Separate consent.* `vision-sense-enabled` defaults to **false** and is
  checked before the first capture happens, not filtered afterwards: by the
  time a result could be discarded, the screenshot has already been taken.
  Screen capture is the widest privacy surface Chronoa has, so it is opt-in per
  sense and never on by default.
- *Separate lifetime.* `TTL_SECONDS` is short and non-`None`, so
  `PerceptStore` keeps the description in memory only and never writes it to
  disk. Only the memory sense may be durable, and a description of what was on
  screen an hour ago is a surveillance record, not context.

**Locality is enforced here, not in `config._NETWORKED_SENSES`.** `vision` is
deliberately absent from that frozenset: whether a capture leaves the machine
depends on which model is chosen, and the choice belongs to this module.
`sense_allowed("vision")` therefore gates on consent alone, and *this* module
additionally refuses two things before capturing anything - a resolved model
name that implies a remote endpoint (a URL, a host, a Hugging Face-style
`org/model@rev` reference), and an `ollama-host` that is not loopback. A
screenshot is the most sensitive payload in the app, so "the model name looked
remote-ish" is a refusal, not a warning.

**Image bytes travel by reference, never through a command line.** A 960x540
PNG is ~100 KB and a 4K one is megabytes; inline JSON into a `python3 -c`
source string hits Linux's 128 KiB `argv` ceiling (`E2BIG`) and cannot express
binary at all. So `run()` hands the bytes to `argfile.reference_command(...,
by_reference=True)`, whose envelope carries them as a private 0600 file and
whose program text is a fixed constant with nothing interpolated - see
`argfile.py`, which documents the measured limits. The child calls
`describe_captured`, which is the only thing that talks to Ollama.

**Ollama's vision API is `POST /api/chat` with an `images` array of base64** on
the user message - the same endpoint the text LLM uses, which is why the
request shape is proven with `httpx.MockTransport` in the tests rather than
against a live server. `tests/test_vision_sense.py` additionally drives a real
capture, a real child process and a real loopback HTTP server end to end.
"""

import base64
import logging
import os
import re
import subprocess
import sys
import time
from typing import Optional

import httpx

from shani_chronoa import argfile, screengrab
from shani_chronoa.config import ChronoaConfig, HardwareProfile
from shani_chronoa.senses import SENSITIVITY_PRIVATE, Percept, Sense

logger = logging.getLogger(__name__)

# The name `config._SENSE_CONSENT_KEYS` maps to `vision-sense-enabled`. Spelled
# out so a rename is a visible diff here rather than a silently denied sense;
# `tests/test_vision_sense.py` asserts the two still agree.
CONSENT_SENSE = "vision"

# Percept kind for both sources: a screen grab and a camera frame are described
# the same way, and calling one a "screen description" would be a lie in the
# percept itself.
KIND = "image_description"

# A description of the screen is a description of *now*. 90s covers a
# follow-up question about the same screen, which is the realistic use, and
# `PerceptStore` drops it from the transient window the moment it is older -
# so nothing about what was on screen survives the conversation it happened in.
TTL_SECONDS = 90.0

# Deliberately short. A VLM on a cold 8B checkpoint answers in tens of seconds
# on a modest GPU and well over a minute on CPU-only hardware; past this the
# answer would arrive after the user stopped caring. The child's own HTTP
# timeout is the same number, and the parent adds a small grace on top for
# interpreter start-up.
DEFAULT_TIMEOUT_SECONDS = 90.0
CHILD_GRACE_SECONDS = 15.0

# The instruction sent with the image. Short, because a long one eats the
# model's attention and because the model is being asked a question, not
# prompted to write an essay about whatever happens to be on screen.
DEFAULT_PROMPT = (
    "Describe what is on this image in two or three sentences. If there is text "
    "visible, say what it says and where it is. Do not speculate about anything "
    "you cannot see."
)

# An Ollama model reference: `name` or `namespace/name`, with an optional
# `:tag`. Deliberately a whitelist rather than a denylist - the test is
# "could this string possibly name something that is not a model on this
# machine?", and `://`, `@` and a leading `/` are exactly the shapes that turn
# one into a remote endpoint. Anything else is refused before a capture.
_LOCAL_MODEL = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._/-]{0,127}(?::[A-Za-z0-9._-]{1,64})?$")

# A camera node, and nothing else. The path reaches a GStreamer property, not a
# shell, so this is about not pointing a camera capture at an arbitrary file.
_CAMERA_DEVICE = re.compile(r"^/dev/video[0-9]{1,3}$")

# The upper bound on a caller-supplied timeout, so `timeout_seconds=100000`
# cannot turn "bounded" into "unbounded".
MAX_TIMEOUT_SECONDS = 300.0


class VisionError(Exception):
    """Vision could not do what was asked. Carries a reason fit to show a user."""


def _refusal(reason: str) -> str:
    return f"Vision unavailable: {reason}."


def _require(condition: object, message: str) -> None:
    if not condition:
        raise VisionError(message)


# --- model and endpoint -----------------------------------------------------


def is_local_model(model: str) -> bool:
    """True if `model` can only name a model served by the local Ollama."""
    return bool(_LOCAL_MODEL.match(model.strip()))


def resolve_vision_model(config: ChronoaConfig) -> str:
    """The vision model to use: the `vision-model` override, else the tier's.

    Never reads `config.model` and never sees the `--model=` CLI override. That
    separation is the whole reason this function exists rather than a call to
    `config.model` with a fallback.
    """
    override = (config.vision_model or "").strip()
    return override or HardwareProfile().get_vision_model()


def local_endpoint(config: ChronoaConfig) -> str:
    """The Ollama host to send an image to, or raise if it is not loopback.

    `config.ollama_host` already forces loopback while privacy mode is on. This
    is the other half: with privacy mode off, a configured LAN or remote host
    is *still* refused, because a screenshot is not the same thing to send
    abroad as a chat message, and the setting that most easily points a user's
    install at someone else's machine is exactly the one that must not carry
    pixels. It adds a gate without touching `config._NETWORKED_SENSES`, whose
    membership stays a statement about `web`.
    """
    host = config.ollama_host.rstrip("/")
    if not ChronoaConfig._is_local_host(host):  # noqa: SLF001 - one list of local hostnames
        raise VisionError(
            f"the configured ollama-host {host!r} is not this machine, and a screen "
            "capture is not something to send off it; point ollama-host at localhost "
            "or clear the vision-model override"
        )
    return host


# --- describing an image with Ollama ----------------------------------------


def build_chat_payload(model: str, prompt: str, image: bytes) -> dict:
    """The `/api/chat` request body: one user message carrying base64 `image`."""
    return {
        "model": model,
        "messages": [
            {
                "role": "user",
                "content": prompt,
                # Ollama takes raw base64 (no data: prefix) in an `images` array
                # on the message, not on the request and not in the content.
                "images": [base64.b64encode(image).decode("ascii")],
            }
        ],
        "stream": False,
        "options": {"temperature": 0.2, "top_p": 0.9, "num_ctx": 4096},
    }


def describe_image(
    image: bytes,
    model: str,
    host: str,
    prompt: str = DEFAULT_PROMPT,
    timeout: float = DEFAULT_TIMEOUT_SECONDS,
    transport: "Optional[httpx.BaseTransport]" = None,
) -> str:
    """Send one image to a local Ollama and return the model's description.

    `transport` exists so the request shape can be proven with
    `httpx.MockTransport` - the same technique this repo's `assistant.py` and
    `llm.py` tests use - without a running Ollama. Production passes nothing.

    Raises `VisionError` with a message fit to show a user; nothing here returns
    an empty description, because "the model said nothing" and "the model said
    the screen is blank" must never look the same to a caller.
    """
    _require(bool(image), "the captured image was empty")
    payload = build_chat_payload(model, prompt, image)
    try:
        with httpx.Client(
            base_url=host, timeout=httpx.Timeout(timeout, connect=10.0), transport=transport
        ) as client:
            response = client.post("/api/chat", json=payload)
    except httpx.ConnectError as e:
        raise VisionError(f"cannot reach an Ollama server at {host} ({e})") from e
    except httpx.HTTPError as e:
        raise VisionError(f"the request to {host} failed: {e}") from e

    if response.status_code != 200:
        # Ollama reports "model not found" here, and that message is the only
        # useful thing a user can be told when a `vision-model` tag is wrong.
        raise VisionError(f"Ollama at {host} answered HTTP {response.status_code}: {response.text[:300]}")

    try:
        body = response.json()
    except ValueError as e:
        raise VisionError(f"Ollama at {host} did not answer with JSON: {response.text[:200]}") from e
    if not isinstance(body, dict):
        raise VisionError(f"Ollama at {host} answered with {type(body).__name__}, not an object")
    if body.get("error"):
        raise VisionError(f"Ollama at {host} reported: {body['error']}")

    message = body.get("message")
    content = message.get("content") if isinstance(message, dict) else None
    if not isinstance(content, str) or not content.strip():
        raise VisionError(
            f"the vision model {model!r} returned no description"
            + (f" ({body['done_reason']})" if body.get("done_reason") else "")
        )
    return content.strip()


def describe_captured(arguments: dict) -> str:
    """Child-process entry point: describe the image `arguments` names.

    Runs under `argfile`'s fixed program with the envelope as `argv[1]`, so the
    image arrives as a `FileRef` (a path into a private 0600 file) and every
    other argument as ordinary JSON. Returning a plain string is what
    `argfile.run_from_file` writes to stdout for the parent to read.

    Consent is *not* re-checked here, and that is deliberate rather than an
    oversight: the parent (`run()`) checks it before it captures anything, and
    this function is not reachable except through a parent that already did.
    A second check would read GSettings a second time and could only disagree
    with the first.
    """
    image = arguments.get("image")
    if isinstance(image, str):  # a FileRef is a str subclass carrying the payload
        with open(image, "rb") as payload:
            image = payload.read()
    _require(isinstance(image, (bytes, bytearray)), "no image was passed to describe_captured")
    timeout = arguments.get("timeout_seconds")
    return describe_image(
        bytes(image),
        str(arguments.get("model") or ""),
        str(arguments.get("host") or ""),
        str(arguments.get("prompt") or DEFAULT_PROMPT),
        float(DEFAULT_TIMEOUT_SECONDS if timeout is None else timeout),
    )


def _child_env() -> dict:
    """The environment for the describing child, with this process's imports.

    The child is a fresh `python3`, so it inherits none of this interpreter's
    `sys.path`: not a virtualenv, not the user site-packages, and not the
    `PYTHONPATH` that got this process importing `httpx` at all. Reproduced
    here rather than reasoned about - with `HOME` pointed at an empty directory
    (which is exactly what the test harness does per test, and what a systemd
    unit looks like) the child died with `ModuleNotFoundError: No module named
    'httpx'` and the screen capture was thrown away after it had already been
    taken. Handing the child this process's own search path is the accurate
    statement of "where its dependencies are", and it costs one env var.
    """
    env = dict(os.environ)
    existing = env.get("PYTHONPATH", "")
    paths = [entry for entry in sys.path if entry and entry != os.getcwd()]
    env["PYTHONPATH"] = os.pathsep.join([*paths, existing] if existing else paths)
    return env


def _describe_via_child(
    image: bytes, model: str, host: str, prompt: str, timeout: float
) -> str:
    """Hand the image to a child interpreter by reference and read its answer.

    `shell=True` because that is the transport `argfile` produces and
    `tools.py` already runs this way: the command is a fixed program with one
    shlex-quoted path argument, and every untrusted value - the image - is
    inside the envelope file, not in the command.
    """
    payload = argfile.reference_command(
        __name__,
        "describe_captured",
        {
            "image": image,
            "model": model,
            "host": host,
            "prompt": prompt,
            "timeout_seconds": timeout,
        },
        by_reference=True,
    )
    _require(
        payload is not None,
        "the by-reference argument transport declined a bytes payload, which it "
        "must never do",
    )
    try:
        completed = subprocess.run(
            payload.command,
            shell=True,
            capture_output=True,
            text=True,
            timeout=timeout + CHILD_GRACE_SECONDS,
            env=_child_env(),
        )
    except subprocess.TimeoutExpired:
        raise VisionError(
            f"the vision model took longer than {timeout:.0f}s and was stopped"
        ) from None
    finally:
        # The envelope holds the screenshot itself, so it goes away whatever the
        # child did. cleanup() is idempotent.
        payload.cleanup()

    if completed.returncode != 0:
        stderr = (completed.stderr or "").strip().splitlines()
        detail = stderr[-1] if stderr else f"exit status {completed.returncode}"
        raise VisionError(f"the vision model could not be run: {detail}")
    description = (completed.stdout or "").strip()
    _require(bool(description), "the vision model returned an empty description")
    return description


# --- arguments --------------------------------------------------------------


def _timeout(arguments: dict) -> float:
    value = arguments.get("timeout_seconds", DEFAULT_TIMEOUT_SECONDS)
    _require(
        isinstance(value, (int, float)) and not isinstance(value, bool),
        "timeout_seconds must be a number",
    )
    _require(0 < float(value) <= MAX_TIMEOUT_SECONDS, f"timeout_seconds must be in (0, {MAX_TIMEOUT_SECONDS:g}]")
    return float(value)


def _model(arguments: dict, config: ChronoaConfig) -> str:
    """The requested model, or the resolved one, checked for locality.

    An argument-level `model` is a per-request choice and goes through exactly
    the same locality check as the pinned one, so `shani-chronoa-sense run
    vision model=https://example.com/v1` cannot turn into a request to a remote
    host with a screenshot attached.
    """
    requested = arguments.get("model")
    model = str(requested).strip() if requested else resolve_vision_model(config)
    _require(bool(model), "no vision model is configured and none could be auto-selected")
    if not is_local_model(model):
        raise VisionError(
            f"{model!r} is not a local Ollama model name; the vision sense refuses to "
            "hand a screenshot to anything that could be a remote endpoint. Use a name "
            "like 'qwen3-vl:4b' served by your own Ollama"
        )
    return model


# --- the sense --------------------------------------------------------------


def run(arguments: dict) -> "str | Percept":
    """Capture one image and describe it. Returns a Percept, or a refusal string.

    The order below is the design: consent, then model and endpoint locality,
    then arguments, and only then the capture. Each of those checks is cheaper
    and less intrusive than the one after it, and a capture cannot be undone -
    it has already happened by the time anyone could decide it should not have.

    A refusal is a plain string on purpose. `Sense.to_percept` wraps it with
    this sense's declared kind/TTL/sensitivity, so a denied request is still
    audited in the same shape as a granted one without being filed as an
    observation of anything.
    """
    config = ChronoaConfig()
    if not config.sense_allowed(CONSENT_SENSE):
        return _refusal(config.sense_allowed_reason(CONSENT_SENSE))

    try:
        model = _model(arguments, config)
        host = local_endpoint(config)
        timeout = _timeout(arguments)
        source = str(arguments.get("source") or screengrab.SOURCE_SCREEN).strip()
        _require(source in screengrab.SOURCES, f"{source!r} is not a capture source; expected one of {', '.join(screengrab.SOURCES)}")
        device = arguments.get("device")
        if device is not None:
            device = str(device).strip()
            _require(bool(_CAMERA_DEVICE.match(device)), f"{device!r} is not a camera node like /dev/video0")
        prompt = str(arguments.get("prompt") or DEFAULT_PROMPT).strip()
        _require(bool(prompt), "the prompt was empty")
    except VisionError as e:
        return _refusal(str(e))

    try:
        capture = screengrab.capture(source=source, device=device, timeout=timeout)
    except screengrab.ScreenCaptureError as e:
        return _refusal(str(e))

    try:
        description = _describe_via_child(capture.data, model, host, prompt, timeout)
    except VisionError as e:
        return _refusal(str(e))

    return Percept(
        sense=CONSENT_SENSE,
        kind=KIND,
        content=f"What {capture.describe()} shows, described by {model} on {host}:\n{description}",
        created_at=time.time(),
        ttl_seconds=TTL_SECONDS,
        source=capture.describe(),
        sensitivity=SENSITIVITY_PRIVATE,
        metadata={
            "model": model,
            "endpoint": host,
            "capture_source": capture.source,
            "capture_backend": capture.backend,
            "width": capture.width,
            "height": capture.height,
            "native_width": capture.native_width,
            "native_height": capture.native_height,
            "downscaled": capture.downscaled(),
            "image_bytes": len(capture.data),
            "prompt": prompt,
            "prompt_is_default": prompt == DEFAULT_PROMPT,
            "described_at": time.time(),
        },
    )


_SCHEMA = {
    "type": "function",
    "function": {
        "name": CONSENT_SENSE,
        "description": (
            "Capture the screen (or a camera frame) and describe it with a local "
            "Ollama vision model. Needs the vision sense enabled. It describes the "
            "image rather than reading text out of it - the ocr sense does that."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "source": {
                    "type": "string",
                    "description": "'screen' (default) or 'camera'.",
                },
                "device": {
                    "type": "string",
                    "description": "Camera node such as /dev/video0; only for source=camera.",
                },
                "prompt": {
                    "type": "string",
                    "description": "What to ask about the image. Defaults to a short description request.",
                },
                "model": {
                    "type": "string",
                    "description": (
                        "A LOCAL Ollama vision model, e.g. 'qwen3-vl:4b'. Must not be "
                        "a URL or a remote reference. Independent of the chat model."
                    ),
                },
                "timeout_seconds": {
                    "type": "number",
                    "description": f"Give up after this many seconds. Default {DEFAULT_TIMEOUT_SECONDS:g}, max {MAX_TIMEOUT_SECONDS:g}.",
                },
            },
            "required": [],
        },
    },
}

# poll_interval=None: reactive only. This is the one sense in the set that
# *could* reasonably be polled on a clock, and it deliberately is not: an
# unattended screen capture every N seconds is precisely the surveillance
# shape the consent key exists to prevent, and the key's own gschema
# description promises the user that "Chronoa looks when you ask it to, it does
# not watch continuously". Ambient scheduling is a real decision for a user to
# make deliberately, not a default this module gets to pick.
_VISION_SENSE = Sense(
    name=CONSENT_SENSE,
    kind=KIND,
    ttl_seconds=TTL_SECONDS,
    sensitivity=SENSITIVITY_PRIVATE,
    schema=_SCHEMA,
    run=run,
    poll_interval=None,
)

SENSES = [_VISION_SENSE]
