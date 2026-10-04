"""u2net - cutting any object out of a picture, where PP-HumanSeg only knows people.

PP-HumanSeg (`opencv/detect.people_mask`) answers exactly one question: which
pixels are a *person*. Asked about a dog, a book or a laptop it still answers,
and what comes back is a confident cut-out of whatever faint person-shaped
region it found, or an empty one - the failure this module exists to stop. So
this is a second model, not a replacement: `opencv/effects.py` keeps the people
path, and nothing in here calls it, in either direction.

The model is rembg's release of u2net (Apache-2.0), the same one Alpaca's
Background Remover runs through rembg. It is installed into the user's home by
`stt_provision.install_verified`, which is the existing downloader: consent
gating, HTTP Range resume, retry on a dropped connection, the pinned size, the
pinned digest and a private temp renamed into place. A second downloader here
would be a second set of those rules to keep true.

Everything it needs is optional and is reached only when someone asks for a
non-person cut-out. `problem()` reports each missing thing *separately* -
onnxruntime, the model, the digest, the address space - because "it did not
work" is not an answer a person can act on, and a refusal that names one of
four different causes when a fifth is true is the confident wrong answer this
repo keeps refusing to ship.

**The switch is the request, not a setting.** `subject="object"` is the only way
in, and nothing defaults to it, so the extra is off on every machine that has
not asked for it by name. That is a stronger gate than a default-off GSettings
key - it cannot fire unasked - and it is also the only one available here: the
schema and `config.py` are another engineer's files in this pass, and adding a
key to them silently would collide with the setting they are adding. When that
schema lands, one `enabled(config)` reading `object-cutout-enabled` in front of
`cut_out` is the whole change.
"""

from __future__ import annotations

import logging
import resource
from pathlib import Path
from typing import Callable, Optional

from shani_chronoa import files
from shani_chronoa.stt_provision import ModelSpec, install_verified

logger = logging.getLogger(__name__)

#: rembg's pinned release asset. Size and digest measured from a real download on
#: 2026-10-02, not copied from a README: sha256 over all 175,997,641 bytes, and
#: an md5 of 60024c5c889badc19c04ad937298a77b, which is the digest the release's
#: own storage advertises, so the bytes are the ones rembg publishes rather than
#: something rewritten in place. A pin read from the same server that served the
#: file proves only that the server agreed with itself.
MODEL = ModelSpec("u2net", "u2net.onnx", 175_997_641,
                  "8d10d2f3bb75ae3b6d527c77944fc5e7dcd94b29809d47a739a7a728a912b491",
                  "u2net (Apache-2.0)",
                  "https://github.com/danielgatis/rembg/releases/download/v0.0.0")

#: u2net's own graph: 1x3x320x320 in, seven 1x1x320x320 out. All seven are the
#: same saliency map, one per decoder stage; on cat.jpg and book.jpg the first
#: and the last agree to about 2 points of coverage (measured 2026-10-02), so
#: the first is taken - which is what rembg takes, so a cut-out here matches the
#: one the tool everyone else uses produces.
INPUT_SIZE = 320

#: ImageNet normalisation, u2net's training recipe. The image is divided by its
#: own maximum rather than by 255, again to match rembg; for an 8-bit photo
#: with any bright pixel the two are the same, and the floor keeps an all-black
#: frame from dividing by zero.
_MEAN = (0.485, 0.456, 0.406)
_STD = (0.229, 0.224, 0.225)

#: Address space u2net needs through onnxruntime, measured 2026-10-02 with
#: `RLIMIT_AS` walked down in 128 MiB steps: the session loads at 1 GiB and the
#: run peaks at 914 MiB of virtual size / 539 MiB resident. Below 1 GiB it dies
#: with `std::bad_alloc` - at 512 MiB while loading, at 768 MiB inside a Conv.
#: No SessionOptions setting changes that: the CPU arena off, sequential
#: execution, one thread and every graph-optimisation level were all measured,
#: and so was u2netp (4.7 MB), which fails at 448 MiB. A skill call runs under
#: the sandbox profile's 512 MiB `RLIMIT_AS` (`sandbox/profiles.py`,
#: PROFILE_DEFAULT), so this is stated as a condition rather than discovered as
#: an out-of-memory traceback from inside the child.
ADDRESS_SPACE_BYTES = 1 << 30

#: one session per process; loading it is ~0.4 s and holding it is ~500 MiB
_SESSION = None

RUNTIME_INSTALL = ("Arch: pacman -S python-onnxruntime-cpu, "
                   "Debian: apt install python3-onnxruntime")


def model_dir() -> Path:
    return files.data_home() / "shani-chronoa" / "u2net"


def model_path() -> Path:
    return model_dir() / MODEL.filename


def installed() -> bool:
    """The model file is there. Not the same as it being right - see `problems`."""
    return model_path().is_file()


def digest_of(path: Path) -> Optional[str]:
    """`path`'s own sha256, or None when it cannot be read."""
    import hashlib

    digest = hashlib.sha256()
    try:
        with path.open("rb") as handle:
            for block in iter(lambda: handle.read(1 << 20), b""):
                digest.update(block)
    except OSError:
        return None
    return digest.hexdigest()


def provision(progress: Optional[Callable[[int, int], None]] = None, config=None,
              transport=None) -> Path:
    """Fetch and verify the model into the user's home; raises rather than guessing."""
    return install_verified(MODEL, model_dir(), config=config, progress=progress,
                            transport=transport, label="u2net")


def runtime():
    """The `onnxruntime` module, or ImportError naming the package to install."""
    try:
        import onnxruntime
    except ImportError as exc:
        raise ImportError(
            f"onnxruntime for Python is not installed ({RUNTIME_INSTALL}): {exc}"
        ) from exc
    return onnxruntime


def runtime_missing() -> bool:
    try:
        runtime()
    except ImportError:
        return True
    return False


def _soft_address_space() -> int:
    """This process's `RLIMIT_AS` soft limit, or RLIM_INFINITY."""
    return resource.getrlimit(resource.RLIMIT_AS)[0]


def address_space_limited() -> bool:
    """Whether this process is capped below what u2net needs.

    Read rather than assumed: a hard limit cannot be raised without privilege
    (`sandbox/limits.py:_clamp`), so a child under the profile's ceiling finds
    out here instead of as an allocation failure several frames inside a
    convolution.
    """
    soft = _soft_address_space()
    return soft != resource.RLIM_INFINITY and soft < ADDRESS_SPACE_BYTES


def problems() -> "list[str]":
    """Every reason the general cut-out cannot run here, each in its own words."""
    found = []
    if runtime_missing():
        found.append(f"onnxruntime for Python is not installed ({RUNTIME_INSTALL}), "
                     f"and it is what runs u2net")
    path = model_path()
    if not path.is_file():
        found.append(f"the u2net model is not installed ({MODEL.filename}, expected at "
                     f"{path}; Chronoa's setup, More -> Photos and videos)")
    else:
        size = path.stat().st_size
        if size != MODEL.size_bytes:
            found.append(f"the u2net model at {path} is {size} bytes and the pinned file is "
                         f"{MODEL.size_bytes}, so it is a partial download and is not being used")
        else:
            actual = digest_of(path)
            if actual is not None and actual != MODEL.sha256:
                found.append(f"the u2net model at {path} hashed to {actual[:16]}... and the "
                             f"pinned digest is {MODEL.sha256[:16]}..., so it is not being used")
    if address_space_limited():
        found.append(f"u2net needs about {ADDRESS_SPACE_BYTES >> 20} MiB of address space to run "
                     f"and this call is capped at {_soft_address_space() >> 20} MiB, so it cannot run here")
    return found


def problem() -> str:
    """Why the general cut-out cannot run here, or '' when it can."""
    found = problems()
    return "; ".join(found)


def available() -> bool:
    return not problem()


def _session():
    """The u2net session, built once and kept. Cleared by `reset()` in tests."""
    global _SESSION
    if _SESSION is None:
        ort = runtime()
        options = ort.SessionOptions()
        # One thread and no CPU arena: a skill child runs under an address-space
        # ceiling, and a thread pool per core reserves far more than one picture
        # needs. Measured at 914 MiB of virtual size with these set.
        options.intra_op_num_threads = 1
        options.inter_op_num_threads = 1
        options.execution_mode = ort.ExecutionMode.ORT_SEQUENTIAL
        options.enable_cpu_mem_arena = False
        _SESSION = ort.InferenceSession(str(model_path()), providers=["CPUExecutionProvider"],
                                        sess_options=options)
        logger.info("Loaded the u2net segmentation model from %s", model_path())
    return _SESSION


def reset() -> None:
    """Drop the cached session. For tests; production keeps one per process."""
    global _SESSION
    _SESSION = None


def alpha_mask(image):
    """A float32 HxW mask in [0, 1] the image's size: 1 on the subject.

    Runs the model at its own 320x320 and scales the mask back up, so a large
    photo costs the same as a small one.
    """
    cv2, np = libraries()
    session = _session()
    h, w = image.shape[:2]
    small = cv2.resize(image, (INPUT_SIZE, INPUT_SIZE), interpolation=cv2.INTER_AREA)
    rgb = cv2.cvtColor(small, cv2.COLOR_BGR2RGB).astype(np.float32)
    rgb /= max(float(rgb.max()), 1.0)
    rgb -= np.array(_MEAN, np.float32)
    rgb /= np.array(_STD, np.float32)
    mask = session.run(None, {session.get_inputs()[0].name: rgb.transpose(2, 0, 1)[None]})[0][0, 0]
    span = float(mask.max() - mask.min())
    mask = (mask - mask.min()) / (span if span > 1e-6 else 1.0)
    return cv2.resize(mask, (w, h), interpolation=cv2.INTER_LINEAR)


def libraries():
    """(cv2, numpy) through the OpenCV extra's own loader.

    Every caller of OpenCV in this subsystem goes through here rather than
    `import cv2`, because the wheel is unpacked into the user's home at run time
    and put on `sys.path` by that loader. An `import cv2` placed before it runs
    finds nothing, and the ImportError it raises names a missing module rather
    than the wheel's actual home.
    """
    from shani_chronoa.opencv import runtime as opencv_runtime

    return opencv_runtime.load()