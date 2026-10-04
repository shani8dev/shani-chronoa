"""OpenCV, numpy and three small models, installed into the user's home and loaded on demand.

OpenCV is not on the images and setup cannot `pacman -S`, so - like Piper and
Kokoro - setup installs it into the user's home: the pinned
`opencv-python-headless` wheel (abi3, so it does not care which Python 3 the
image has; it bundles FFmpeg for reading video) and numpy for the image's
Python, unpacked into ~/.local/share/shani-chronoa/python and put on `sys.path`
only by `load()`.

The models are OpenCV Zoo's, each pinned by its Hugging Face LFS size and
sha256 at a revision (read 2026-10-02):

- YuNet - faces (MIT)
- NanoDet-Plus - 80 kinds of everyday object, COCO's classes (Apache-2.0)
- PP-HumanSeg - which pixels are a person, for background effects (Apache-2.0)

Both libraries are told to use one thread before they load: a skill runs under
the sandbox profile's address-space limit, and a thread pool per core reserves
far more address space than one picture needs.
"""

from __future__ import annotations

import os
import sys
import sysconfig
import zipfile
from pathlib import Path
from typing import Callable, Optional

from shani_chronoa import files
from shani_chronoa.stt_provision import ModelSpec, _install, install_verified

_PYPI = "https://files.pythonhosted.org/packages"
#: sizes and sha256 are the ones PyPI publishes for each file (2026-10-02)
OPENCV = ModelSpec("opencv", "opencv_python_headless-5.0.0.93-cp37-abi3-manylinux_2_28_x86_64.whl", 61_204_038,
                   "ed709fdf9aa0bd1f2ed8549e71d19449b03a675bb581eb292285f6861953be37", "OpenCV 5.0 (headless)",
                   f"{_PYPI}/9b/21/f6ef335f6e65724aa78b8d792b48d40a48c381715f1e62f5a5049e09d07e")
NUMPY = ModelSpec("numpy", "numpy-2.5.3-cp314-cp314-manylinux_2_27_x86_64.manylinux_2_28_x86_64.whl", 16_711_928,
                  "b0521d0f4aebb6e06189451025fa17a913287b13c03d5fe05c017333b654ea5b", "numpy 2.5.3",
                  f"{_PYPI}/45/8f/9beacf79ca7c650688ad0baa80931adb988fe6e6e5d5903c23cc3dbd70eb")

_ZOO = "https://huggingface.co/opencv/{repo}/resolve/{rev}"
FACES = ModelSpec("yunet", "face_detection_yunet_2023mar.onnx", 232_589,
                  "8f2383e4dd3cfbb4553ea8718107fc0423210dc964f9f4280604804ed2552fa4", "YuNet faces (MIT)",
                  _ZOO.format(repo="face_detection_yunet", rev="3cc26e7f1014a5ee5d74a42acee58bafc9d0a310"))
OBJECTS = ModelSpec("nanodet", "object_detection_nanodet_2022nov.onnx", 3_800_954,
                    "4b82da9944b88577175ee23a459dce2e26e6e4be573def65b1055dc2d9720186", "NanoDet-Plus objects (Apache-2.0)",
                    _ZOO.format(repo="object_detection_nanodet", rev="5bfd47077350a726ad440dd7bd1e1e35e8ebcfb2"))
PEOPLE = ModelSpec("pphumanseg", "human_segmentation_pphumanseg_2023mar.onnx", 6_163_938,
                   "552d8a984054e59b5d773d24b9b12022b22046ceb2bbc4c9aaeaceb36a9ddf24", "PP-HumanSeg (Apache-2.0)",
                   _ZOO.format(repo="human_segmentation_pphumanseg", rev="e876e63603f6c65c1a22576bfd5fc7c07ecf9eb9"))
MODELS = (FACES, OBJECTS, PEOPLE)

DOWNLOAD_BYTES = OPENCV.size_bytes + NUMPY.size_bytes + sum(m.size_bytes for m in MODELS)


def python_dir() -> Path:
    return files.data_home() / "shani-chronoa" / "python"


def models_dir() -> Path:
    return files.data_home() / "shani-chronoa" / "opencv"


def model_path(spec: ModelSpec) -> Path:
    return models_dir() / spec.filename


def _python_tag() -> str:
    return f"cp{sys.version_info.major}{sys.version_info.minor}"


def load():
    """(cv2, numpy), from the user-local install or the system; one thread each. Raises ImportError."""
    for name in ("OPENBLAS_NUM_THREADS", "OMP_NUM_THREADS", "MKL_NUM_THREADS"):
        os.environ.setdefault(name, "1")
    local = str(python_dir())
    if python_dir().is_dir() and local not in sys.path:
        sys.path.insert(0, local)
    import numpy  # noqa: F401 - first, so cv2 finds this one
    import cv2
    cv2.setNumThreads(1)
    return cv2, numpy


def installed() -> bool:
    libs = (python_dir() / "cv2").is_dir() and (python_dir() / "numpy").is_dir()
    return libs and all(model_path(m).is_file() for m in MODELS)


def libraries_problem() -> str:
    """Why OpenCV itself cannot be loaded here, or '' (the models are not needed for a page scan)."""
    try:
        load()
    except ImportError as exc:
        return f"OpenCV is not installed (Chronoa's setup, More -> Photos and videos): {exc}"
    return ""


def problem() -> str:
    """Why photos and videos cannot be worked on here, or ''."""
    missing = [m.filename for m in MODELS if not model_path(m).is_file()]
    try:
        load()
    except ImportError as exc:
        return f"OpenCV is not installed (Chronoa's setup, More -> Photos and videos): {exc}"
    if missing:
        return f"the OpenCV models are not installed ({missing[0]}; Chronoa's setup, More -> Photos and videos)"
    return ""


def _unpack_wheel(archive: Path, destination: Path) -> None:
    destination.mkdir(parents=True, exist_ok=True)
    root = destination.resolve()
    with zipfile.ZipFile(archive) as zf:
        for info in zf.infolist():
            if info.is_dir():
                continue
            target = (destination / info.filename).resolve()
            if root not in target.parents:
                raise RuntimeError(f"refusing {info.filename!r}: it would land outside {destination}")
            target.parent.mkdir(parents=True, exist_ok=True)
            with zf.open(info) as src, open(target, "wb") as dst:
                while block := src.read(1 << 20):
                    dst.write(block)
            mode = (info.external_attr >> 16) & 0o777
            os.chmod(target, 0o755 if (mode & 0o111 or ".so" in target.name) else 0o644)


def install(progress: Optional[Callable[[int, int], None]] = None, config=None, transport=None) -> Path:
    """Fetch, verify and unpack numpy and OpenCV, and fetch the three models."""
    if _python_tag() not in NUMPY.filename:
        raise RuntimeError(f"the pinned numpy is built for {NUMPY.filename.split('-')[2]}, and this Python is "
                           f"{_python_tag()} ({sysconfig.get_python_version()}); the pin needs updating")
    for spec, package in ((NUMPY, "numpy"), (OPENCV, "cv2")):
        if (python_dir() / package).is_dir():
            continue
        archive = _install(spec, user_dir=python_dir(), system_dir=Path("/usr/lib/python3/site-packages"),
                           existing=None, config=config, progress=progress, transport=transport, label="opencv")
        try:
            _unpack_wheel(archive, python_dir())
        finally:
            archive.unlink(missing_ok=True)
    for spec in MODELS:
        install_verified(spec, models_dir(), config=config, progress=progress, transport=transport, label="opencv")
    if not installed():
        raise RuntimeError("OpenCV did not unpack to cv2/ and numpy/, or a model is missing")
    return python_dir()
