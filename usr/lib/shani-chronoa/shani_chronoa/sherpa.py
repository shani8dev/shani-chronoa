"""sherpa-onnx: one release, several programs - Kokoro's voice, hearing sounds, telling speakers apart.

k2-fsa's sherpa-onnx (Apache-2.0) publishes a 27 MB Linux release of
command-line programs that carry their own onnxruntime (`lib/`, found through
each binary's `$ORIGIN/../lib` rpath). It is installed once, into the user's
home, exactly as Piper's release is (`voices.install_piper`), and each feature
that needs one of its programs downloads only its own model:

- `sherpa-onnx-offline-tts`             Kokoro (voices.py)
- `sherpa-onnx-offline-audio-tagging`   what a sound is (sounds.py)
- `sherpa-onnx-offline-speaker-diarization`  who spoke when (speakers.py)

The archive's digest is the one GitHub publishes for the release asset, and
matches a download of it (2026-10-02).
"""

from __future__ import annotations

import os
import tarfile
from pathlib import Path
from typing import Callable, Optional

from shani_chronoa import files
from shani_chronoa.stt_provision import ModelSpec, _install

VERSION = "v1.13.8"
RELEASE = ModelSpec("sherpa-onnx", f"sherpa-onnx-{VERSION}-linux-x64-shared.tar.bz2", 28_156_791,
                    "c0bdb7907d3a74bba1d55d22bf4d9fa75586cf1530614ebe88a27b9118e015c4",
                    f"sherpa-onnx {VERSION}",
                    f"https://github.com/k2-fsa/sherpa-onnx/releases/download/{VERSION}")
MODELS_URL = "https://github.com/k2-fsa/sherpa-onnx/releases/download"


def install_dir() -> Path:
    return files.data_home() / "shani-chronoa" / "sherpa-onnx"


def binary(name: str) -> str:
    """The path of one of the release's programs, or '' when the release is not installed."""
    path = install_dir() / f"sherpa-onnx-{VERSION}-linux-x64-shared" / "bin" / name
    return str(path) if os.access(path, os.X_OK) else ""


def unpack(archive: Path, destination: Path) -> None:
    with tarfile.open(archive) as tar:
        # data filter: no absolute paths, no "..", no devices or setuid bits
        tar.extractall(destination, filter="data")


def install(progress: Optional[Callable[[int, int], None]] = None, config=None, transport=None,
            label: str = "sherpa-onnx") -> str:
    """Fetch, verify and unpack the release (once); returns the install directory."""
    if binary("sherpa-onnx-offline-tts"):
        return str(install_dir())
    archive = _install(RELEASE, user_dir=install_dir(), system_dir=Path("/usr/share/sherpa-onnx"), existing=None,
                       config=config, progress=progress, transport=transport, label=label)
    try:
        unpack(archive, install_dir())
    finally:
        archive.unlink(missing_ok=True)
    if not binary("sherpa-onnx-offline-tts"):
        raise RuntimeError("the sherpa-onnx archive did not contain its programs")
    return str(install_dir())


def install_archive(spec: ModelSpec, directory: Path, expect: Path, progress=None, config=None, transport=None,
                    label: str = "sherpa-onnx") -> Path:
    """A model shipped as a .tar.bz2: fetched, verified and unpacked into `directory` unless `expect` exists."""
    if expect.exists():
        return expect
    archive = _install(spec, user_dir=directory, system_dir=Path("/usr/share/sherpa-onnx"), existing=None,
                       config=config, progress=progress, transport=transport, label=label)
    try:
        unpack(archive, directory)
    finally:
        archive.unlink(missing_ok=True)
    if not expect.exists():
        raise RuntimeError(f"{spec.filename} did not contain {expect.name}")
    return expect
