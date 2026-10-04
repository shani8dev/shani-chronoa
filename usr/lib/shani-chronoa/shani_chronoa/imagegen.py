"""Imagine: pictures from a description, on this computer, with stable-diffusion.cpp.

stable-diffusion.cpp is not packaged by Arch (AUR only), but its releases ship
a self-contained Linux build - `sd-server` plus its ggml libraries, found
through `$ORIGIN`, needing only glibc, libstdc++ and libgomp from the base
system (and libvulkan for the Vulkan build). Setup installs that build in the
user's home, the Vulkan one when llama.cpp sees a GPU, exactly as `voices.py`
installs Piper: a pinned archive, verified, unpacked member by member.

The model is SD-Turbo (Stability AI Community License: free, including
commercial use, for anyone under US$1M a year in revenue), which makes a
512x512 picture in 1-4 steps - the only kind of image model that is usable on
a processor. It is served by `sd-server` on 127.0.0.1:8769 through the
`shani-chronoa-model@imagine` unit; weights load on the first request and are
memory-mapped, so an idle server costs page cache rather than RAM.

The request is the WebUI-compatible `POST /sdapi/v1/txt2img`. The server lets a
prompt carry native options inside `<sd_cpp_extra_args>...</sd_cpp_extra_args>`;
a prompt is text a model wrote, so that tag is removed before sending.
"""

from __future__ import annotations

import base64
import logging
import os
import re
import stat
import zipfile
from pathlib import Path
from typing import Callable, Optional

from shani_chronoa import files, model_service
from shani_chronoa.stt_provision import ModelSpec, _install, file_matches, install_verified

logger = logging.getLogger(__name__)

INSTANCE = "imagine"

_RELEASE = "https://github.com/leejet/stable-diffusion.cpp/releases/download/master-929-3f8527a"
#: Digests are the ones GitHub publishes for each release asset (and match a download of each).
ENGINE_CPU = ModelSpec("sdcpp-cpu", "sd-master-3f8527a-bin-Linux-Ubuntu-24.04-x86_64.zip", 26_025_088,
                       "9ad35ed309dbe59f5e66f35edafac9a69e6cc233ea6b9577071feae829159d37",
                       "stable-diffusion.cpp master-929 (CPU)", _RELEASE)
ENGINE_VULKAN = ModelSpec("sdcpp-vulkan", "sd-master-3f8527a-bin-Linux-Ubuntu-24.04-x86_64-vulkan.zip", 36_886_217,
                          "e35cc73cf5ba9637d1dc1d717760e7b8428376a4905d57e72ec8c871864f62c7",
                          "stable-diffusion.cpp master-929 (Vulkan)", _RELEASE)
MODEL = ModelSpec("sd-turbo", "sd_turbo-f16-q8_0.gguf", 2_023_745_376,
                  "d50be7655f0a554cf8041c145d88b210bd5f3c545423119dee62ae08cae51580",
                  "SD-Turbo (Stability AI Community License)",
                  "https://huggingface.co/Green-Sky/SD-Turbo-GGUF/resolve/19a31586d02d64a73b4419bc193b3ecfaf38e1f0")

#: SD-Turbo is distilled for one to four steps without guidance; more steps do not help it.
DEFAULT_STEPS, MAX_STEPS = 2, 4
SIZES = (256, 384, 512, 640, 768)
DEFAULT_SIZE = 512
_EXTRA_ARGS = re.compile(r"<\s*/?\s*sd_cpp_extra_args\s*>.*?(<\s*/\s*sd_cpp_extra_args\s*>|$)", re.S | re.I)


def engine_dir() -> Path:
    return files.data_home() / "shani-chronoa" / "sdcpp"


def model_dir() -> Path:
    return files.data_home() / "shani-chronoa" / "imagine"


def server_binary() -> str:
    path = engine_dir() / "sd-server"
    return str(path) if os.access(path, os.X_OK) else ""


def installed() -> bool:
    return bool(server_binary()) and (model_dir() / MODEL.filename).is_file()


def available() -> bool:
    return installed() and model_service.env_file(INSTANCE).is_file()


def engine_for(gpu: bool) -> ModelSpec:
    return ENGINE_VULKAN if gpu else ENGINE_CPU


def install_engine(gpu: bool, progress=None, config=None, transport=None) -> str:
    """Fetch, verify and unpack the release build; returns sd-server's path."""
    spec = engine_for(gpu)
    marker = engine_dir() / ".release"
    if server_binary() and marker.is_file() and marker.read_text().strip() == spec.filename:
        return server_binary()
    archive = _install(spec, user_dir=engine_dir(), system_dir=Path("/usr/share/shani-chronoa/sdcpp"),
                       existing=None, config=config, progress=progress, transport=transport, label="imagine")
    try:
        unpack(archive, engine_dir())
    finally:
        archive.unlink(missing_ok=True)
    marker.write_text(spec.filename + "\n", encoding="utf-8")
    if not server_binary():
        raise RuntimeError("the stable-diffusion.cpp archive did not contain sd-server")
    return server_binary()


def unpack(archive: Path, destination: Path) -> None:
    """Regular files only, each landing inside `destination`; libraries and the two programs executable."""
    destination.mkdir(parents=True, exist_ok=True)
    root = destination.resolve()
    with zipfile.ZipFile(archive) as zf:
        for info in zf.infolist():
            name = info.filename
            if info.is_dir():
                continue
            mode = (info.external_attr >> 16) & 0o170000
            if mode not in (0, stat.S_IFREG):
                raise RuntimeError(f"refusing {name!r}: not a regular file")
            target = (destination / name).resolve()
            if root not in target.parents or Path(name).is_absolute() or ".." in Path(name).parts:
                raise RuntimeError(f"refusing {name!r}: it would land outside {destination}")
            target.parent.mkdir(parents=True, exist_ok=True)
            with zf.open(info) as src, open(target, "wb") as dst:
                while block := src.read(1 << 20):
                    dst.write(block)
            executable = target.name in ("sd-server", "sd-cli") or ".so" in target.name
            os.chmod(target, 0o755 if executable else 0o644)


def server_args() -> "list[str]":
    # the Vulkan build picks the GPU by itself; the CPU build has nothing to pick
    return ["-m", str(model_dir() / MODEL.filename), "--listen-ip", model_service.HOST,
            "--listen-port", str(model_service.PORTS[INSTANCE]), "--mmap",
            "-t", str(max(1, (os.cpu_count() or 2) - 1))]


def provision(gpu: bool, progress: Optional[Callable[[int, int], None]] = None, config=None, transport=None) -> Path:
    install_engine(gpu, progress=progress, config=config, transport=transport)
    path = install_verified(MODEL, model_dir(), config=config, progress=progress, transport=transport, label="imagine")
    model_service.write_env(INSTANCE, server_binary(), server_args())
    return path


def verify() -> bool:
    return file_matches(model_dir() / MODEL.filename, MODEL)


class ImageError(RuntimeError):
    """Making a picture failed; the message is fit to show a person."""


def clean_prompt(prompt: str) -> str:
    return " ".join(_EXTRA_ARGS.sub(" ", prompt or "").split())[:1000]


def txt2img_payload(prompt: str, size: int = DEFAULT_SIZE, steps: int = DEFAULT_STEPS, seed: int = -1,
                    negative: str = "") -> dict:
    if size not in SIZES:
        raise ImageError(f"size must be one of {', '.join(map(str, SIZES))}")
    if not 1 <= steps <= MAX_STEPS:
        raise ImageError(f"steps must be between 1 and {MAX_STEPS} for this model")
    text = clean_prompt(prompt)
    if not text:
        raise ImageError("say what the picture should show")
    return {"prompt": text, "negative_prompt": clean_prompt(negative), "width": size, "height": size,
            "steps": steps, "cfg_scale": 1.0, "seed": int(seed), "batch_size": 1, "sampler_name": "euler_a"}


def generate(prompt: str, size: int = DEFAULT_SIZE, steps: int = DEFAULT_STEPS, seed: int = -1,
             negative: str = "", timeout: float = 600.0, transport=None) -> bytes:
    """PNG bytes for `prompt`. The first request after the server starts also loads the model."""
    return _post("/sdapi/v1/txt2img", txt2img_payload(prompt, size, steps, seed, negative), timeout, transport)


def _post(route: str, payload: dict, timeout: float, transport=None) -> bytes:
    import httpx
    url = model_service.base_url(INSTANCE) + route
    try:
        with httpx.Client(timeout=httpx.Timeout(timeout, connect=10.0), transport=transport) as client:
            response = client.post(url, json=payload)
    except httpx.ConnectError as exc:
        raise ImageError("the picture maker is not running; open Chronoa's setup to start it") from exc
    except httpx.HTTPError as exc:
        raise ImageError(f"making the picture failed: {type(exc).__name__}: {exc}") from exc
    if response.status_code != 200:
        raise ImageError(f"the picture maker answered HTTP {response.status_code}: {response.text[:300]}")
    try:
        data = base64.b64decode(response.json()["images"][0])
    except (ValueError, KeyError, IndexError, TypeError) as exc:
        raise ImageError(f"the picture maker's answer was not understood: {response.text[:200]}") from exc
    if not data.startswith(b"\x89PNG\r\n\x1a\n"):
        raise ImageError("the picture maker did not return a PNG")
    return data


def fit_for_model(path: Path, longest: int = DEFAULT_SIZE) -> "tuple[bytes, int, int]":
    """`path` as PNG bytes resized for the model: longest side `longest`, both sides multiples of 64.

    ImageMagick does it (every ShaniOS install has it); the model wants its
    input at a size it was trained near, and sd.cpp wants multiples of 64.
    """
    import shutil
    import subprocess
    if not shutil.which("magick"):
        raise ImageError("ImageMagick (magick) is needed to prepare the photo")
    probe = subprocess.run(["magick", "identify", "-format", "%w %h", f"{path}[0]"], capture_output=True,
                           text=True, timeout=30)
    try:
        w, h = (int(x) for x in probe.stdout.split()[:2])
    except ValueError as exc:
        raise ImageError(f"{path.name} is not a picture ImageMagick can read") from exc
    scale = longest / max(w, h)
    tw, th = (max(64, int(round(w * scale / 64)) * 64), max(64, int(round(h * scale / 64)) * 64))
    out = subprocess.run(["magick", f"{path}[0]", "-auto-orient", "-resize", f"{tw}x{th}!", "png:-"],
                         capture_output=True, timeout=60)
    if out.returncode != 0 or not out.stdout.startswith(b"\x89PNG"):
        raise ImageError(f"could not prepare {path.name}: {out.stderr.decode(errors='replace')[:160]}")
    return out.stdout, tw, th


def img2img_payload(image_png: bytes, width: int, height: int, prompt: str, strength: float = 0.5,
                    steps: int = DEFAULT_STEPS, seed: int = -1, negative: str = "") -> dict:
    """`/sdapi/v1/img2img`: the photo, how much to change it (0 keeps it, 1 ignores it), and what into."""
    if not 0.1 <= strength <= 0.9:
        raise ImageError("strength must be between 0.1 (a light touch) and 0.9 (nearly a new picture)")
    payload = txt2img_payload(prompt, DEFAULT_SIZE, steps, seed, negative)
    # SD-Turbo needs steps * strength to be at least one real step
    payload["steps"] = max(steps, int(1 / strength + 0.999))
    payload.update(width=width, height=height, denoising_strength=float(strength),
                   init_images=[base64.b64encode(image_png).decode("ascii")])
    return payload


def edit(image_png: bytes, width: int, height: int, prompt: str, strength: float = 0.5,
         steps: int = DEFAULT_STEPS, seed: int = -1, negative: str = "", timeout: float = 600.0,
         transport=None) -> bytes:
    """The photo changed as `prompt` describes (img2img), as PNG bytes."""
    payload = img2img_payload(image_png, width, height, prompt, strength, steps, seed, negative)
    return _post("/sdapi/v1/img2img", payload, timeout, transport)


def pictures_dir() -> Path:
    """XDG_PICTURES_DIR/Chronoa, else ~/Pictures/Chronoa."""
    import shutil
    import subprocess
    base = ""
    if shutil.which("xdg-user-dir"):
        try:
            base = subprocess.run(["xdg-user-dir", "PICTURES"], capture_output=True, text=True,
                                  timeout=5, check=False).stdout.strip()
        except (OSError, subprocess.TimeoutExpired):
            base = ""
    home = Path.home()
    folder = Path(base) if base and Path(base) != home else home / "Pictures"
    return folder / "Chronoa"
