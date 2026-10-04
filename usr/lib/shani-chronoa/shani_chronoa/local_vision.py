"""Eyes without Ollama: a llama.cpp vision model and its projector, served on 127.0.0.1:8767.

Arch's llama-cpp is built with libmtmd, so `llama-server --mmproj` serves a
vision-language model over the same OpenAI API the brain uses; an image goes
in a user message as a `data:image/png;base64,...` URL. Two files per model -
the language model and its multimodal projector - each pinned by size and
sha256 and downloaded through `stt_provision`'s verified path. The server is
started with `--sleep-idle-seconds`, so a model nobody is looking through
gives its memory back and loads again on the next request.

`senses/vision.py` uses this when a model here is set up, and falls back to a
local Ollama otherwise; both stay on this machine. Reading text out of an image
is `senses/ocr.py` (tesseract), not this.

Pins are each file's Hugging Face LFS size and sha256 at the revision named,
read from the HF API on 2026-10-02. All three model families are Apache-2.0.
"""

from __future__ import annotations

import base64
import logging
import os
from pathlib import Path
from typing import Callable, NamedTuple, Optional

from shani_chronoa import files, model_service
from shani_chronoa.stt_provision import ModelSpec, file_matches, install_verified

logger = logging.getLogger(__name__)

INSTANCE = "vision"
IDLE_SECONDS = 300
CONTEXT = 4096

_HF = "https://huggingface.co/{repo}/resolve/{rev}"


class VisionModel(NamedTuple):
    key: str
    label: str
    model: ModelSpec
    mmproj: ModelSpec
    #: the smallest RAM (GB) this is the default for on a CPU; GPU picks are in `recommended_for_machine`
    ram_gb: float

    @property
    def size_bytes(self) -> int:
        return self.model.size_bytes + self.mmproj.size_bytes


def _pair(key, repo, rev, model_file, model_size, model_sha, proj_file, proj_size, proj_sha, note):
    base = _HF.format(repo=repo, rev=rev)
    return (ModelSpec(key, model_file, model_size, model_sha, note, base),
            ModelSpec(key + "-mmproj", proj_file, proj_size, proj_sha, note + " (projector)", base))


TIERS = (
    VisionModel("smolvlm2-500m", "Small (0.5 GB) - what is on the screen, roughly; quick on any computer",
                *_pair("smolvlm2-500m", "ggml-org/SmolVLM2-500M-Video-Instruct-GGUF",
                       "ccd7aae53bcb1997355c2f094959e72b3642ce17",
                       "SmolVLM2-500M-Video-Instruct-Q8_0.gguf", 436_808_704,
                       "6f67b8036b2469fcd71728702720c6b51aebd759b78137a8120733b4d66438bc",
                       "mmproj-SmolVLM2-500M-Video-Instruct-Q8_0.gguf", 108_785_184,
                       "921dc7e259f308e5b027111fa185efcbf33db13f6e35749ddf7f5cdb60ef520b", "SmolVLM2 500M"),
                0),
    VisionModel("qwen3-vl-2b", "Medium (1.6 GB) - reads screens, windows and documents well",
                *_pair("qwen3-vl-2b", "Qwen/Qwen3-VL-2B-Instruct-GGUF", "52d6c8ffea26cc873ac5ad116f8631268d7eb503",
                       "Qwen3VL-2B-Instruct-Q4_K_M.gguf", 1_107_409_952,
                       "089d75c52f4b7ffc56ba998ffc50aae89fcafc755f9e7208aacca281dca6c2ae",
                       "mmproj-Qwen3VL-2B-Instruct-Q8_0.gguf", 445_053_216,
                       "f9a68fabba69c3b81e153367b2c7521030b0fa8bb0de400c9599c8e6725f9c82", "Qwen3-VL 2B"),
                8),
    VisionModel("qwen3-vl-4b", "Large (3.0 GB) - the most detail; slow without a graphics card",
                *_pair("qwen3-vl-4b", "Qwen/Qwen3-VL-4B-Instruct-GGUF", "1cd86afb9a95c410a6038ab3b40d8b578c892266",
                       "Qwen3VL-4B-Instruct-Q4_K_M.gguf", 2_497_281_664,
                       "66358cb18bb6b3b1b6675aa412c7a88ef01d228f481184d13668e5201c730a0a",
                       "mmproj-Qwen3VL-4B-Instruct-Q8_0.gguf", 453_974_304,
                       "30ba2c7dd3127a4561b6cba9d13d0f711c91bdb38742e2f56d73c8cb596bd06d", "Qwen3-VL 4B"),
                999),
)
MODELS = {m.key: m for m in TIERS}


def model_dir() -> Path:
    return files.data_home() / "shani-chronoa" / "vision"


def installed() -> "list[str]":
    return [m.key for m in TIERS
            if (model_dir() / m.model.filename).is_file() and (model_dir() / m.mmproj.filename).is_file()]


def active() -> str:
    """The model the server is set up to load, from its env file; '' when eyes were never set up."""
    _binary, args = model_service.read_env(INSTANCE)
    if "-m" not in args:
        return ""
    name = Path(args[args.index("-m") + 1]).name
    return next((m.key for m in TIERS if m.model.filename == name), "")


def recommended(ram: Optional[float] = None, gpu: bool = False) -> str:
    from shani_chronoa import local_llm
    ram = local_llm.ram_gb() if ram is None else ram
    if gpu and ram >= 12:
        return "qwen3-vl-4b"
    return [m.key for m in TIERS if ram >= m.ram_gb][-1]


def recommended_for_machine() -> str:
    from shani_chronoa import local_llm
    return recommended(gpu=bool(local_llm.gpu_devices()))


def verify(key: str) -> bool:
    m = MODELS[key]
    return file_matches(model_dir() / m.model.filename, m.model) and file_matches(model_dir() / m.mmproj.filename, m.mmproj)


def server_args(key: str, devices: "Optional[list[str]]" = None) -> "list[str]":
    from shani_chronoa import local_llm
    devices = local_llm.gpu_devices() if devices is None else devices
    m = MODELS[key]
    args = ["-m", str(model_dir() / m.model.filename), "--mmproj", str(model_dir() / m.mmproj.filename),
            "--host", model_service.HOST, "--port", str(model_service.PORTS[INSTANCE]),
            "-c", str(CONTEXT), "--no-webui", "--sleep-idle-seconds", str(IDLE_SECONDS)]
    args += ["-ngl", "99"] if devices else ["-ngl", "0", "-t", str(max(1, (os.cpu_count() or 2) - 1))]
    return args


def provision(key: str, progress: Optional[Callable[[int, int], None]] = None, config=None, transport=None) -> Path:
    """Download and verify both files for `key`, then point the server at them."""
    m = MODELS[key]
    for spec in (m.model, m.mmproj):
        install_verified(spec, model_dir(), config=config, progress=progress, transport=transport, label="vision")
    configure(key)
    return model_dir() / m.model.filename


def configure(key: str, devices: "Optional[list[str]]" = None) -> Path:
    from shani_chronoa import local_llm
    return model_service.write_env(INSTANCE, local_llm.server_binary() or "/usr/bin/llama-server",
                                   server_args(key, devices))


def available() -> bool:
    """Set up here: a model on disk and the server configured to load it (it may be asleep, which is fine)."""
    key = active()
    return bool(key) and key in installed()


class VisionError(RuntimeError):
    """Describing an image failed; the message is fit to show a person."""


def chat_payload(prompt: str, image: bytes, mime: str = "image/png") -> dict:
    url = f"data:{mime};base64,{base64.b64encode(image).decode('ascii')}"
    return {
        "model": active() or "vision",
        "messages": [{"role": "user", "content": [
            {"type": "image_url", "image_url": {"url": url}},
            {"type": "text", "text": prompt},
        ]}],
        "temperature": 0.2,
        "max_tokens": 400,
        "stream": False,
    }


def describe(image: bytes, prompt: str, timeout: float = 120.0, transport=None) -> str:
    """The model's description of `image`. A sleeping server wakes on this request, so it can take a while."""
    import httpx
    from shani_chronoa import egress
    if not image:
        raise VisionError("the captured image was empty")
    payload = chat_payload(prompt, image)
    url = model_service.base_url(INSTANCE) + "/v1/chat/completions"
    response = None
    try:
        with httpx.Client(timeout=httpx.Timeout(timeout, connect=10.0), transport=transport) as client:
            response = client.post(url, json=payload)
    except httpx.ConnectError as exc:
        raise VisionError(f"the vision model is not running ({exc}); open Chronoa's setup to start it") from exc
    except httpx.HTTPError as exc:
        raise VisionError(f"the request to the vision model failed: {type(exc).__name__}: {exc}") from exc
    finally:
        egress.record("senses:vision", url, method="POST", status=getattr(response, "status_code", None),
                      bytes_out=egress.payload_size(payload), privacy_mode=egress.privacy_mode_enabled())
    if response.status_code != 200:
        raise VisionError(f"the vision model answered HTTP {response.status_code}: {response.text[:300]}")
    try:
        content = response.json()["choices"][0]["message"]["content"]
    except (ValueError, KeyError, IndexError, TypeError) as exc:
        raise VisionError(f"the vision model's answer was not understood: {response.text[:200]}") from exc
    if not isinstance(content, str) or not content.strip():
        raise VisionError("the vision model returned no description")
    return content.strip()
