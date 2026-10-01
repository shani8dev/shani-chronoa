"""Fetch a whisper.cpp STT model on first use, and refuse an unverified one.

Speech input is the one capability that cannot degrade silently. `tts.py` can
fall back from piper to rhvoice to espeak-ng and always say something;
`stt.py` has no fallback at all, so a model that is missing, truncated, or
substituted produces a microphone button that either does nothing or, worse,
transcribes noise into the conversation.

So this module never installs bytes it has not verified. The pinned digests in
`MODELS` are the trust anchor, and they are deliberately *not* fetched from a
manifest at run time: a digest read from the same server that served the file
proves only that the server was consistent with itself.

whisper.cpp ships its own `models/download-ggml-model.sh`, and it verifies
nothing - it curls the file and immediately runs it. That is the gap this fills.

Three deliberate design decisions:

- **In-process, not a skill.** Landlock's writable set is {workspace, /tmp,
  /run} (`sandbox/landlock.py:497-499`), so a sandboxed skill cannot write the
  model directory at all, and skills also die at a 30s timeout (`tools.py:84`)
  which a 57 MB download overruns by orders of magnitude.
- **Fetched to a private temp, verified, then renamed.** `os.replace`
  preserves the temp file's mode, so restricting only the destination would
  leave a window where the file sits at its final path world-readable
  (`files.py:205-212`). Both ends are restricted here, as `compression.py`
  does.
- **Separate from the `modelfit` sense.** That key documents itself as
  "never downloads, loads, unloads or changes a model", and this keeps that
  promise true rather than quietly breaking it.

Public API:

    model_path("base-q5_1")   -> Path   where the model lives or would live
    is_provisioned("base-q5_1") -> bool
    provision("base-q5_1")    -> Path   fetch + verify, or raise
"""

import hashlib
import logging
import os
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Dict, Optional

from shani_chronoa import egress, files

logger = logging.getLogger(__name__)

# HuggingFace serves the canonical ggml builds. `resolve/main` redirects to a
# content-addressed CDN host, so the redirect walk below re-checks each hop.
_BASE_URL = "https://huggingface.co/ggerganov/whisper.cpp/resolve/main"

_USER_AGENT = "shani-chronoa/0.1 (STT model provisioning)"

# A model is a few tens to a few hundred MB. 1 GiB is far above any real ggml
# file and well below "the connection died mid-stream and we filled the disk".
_MAX_BYTES = 1 << 30

_MAX_REDIRECTS = 5

# Refuse to sit on a socket forever. Generous for a 181 MB file on a slow link,
# but this is not a download manager.
_TIMEOUT = 120.0


@dataclass(frozen=True)
class ModelSpec:
    """One provisionable model: which file, how big, and what it must hash to."""

    key: str
    filename: str
    size_bytes: int
    sha256: str
    note: str = ""


# Sizes are the HF LFS `size` for each blob; digests are the same field's
# `sha256`. Both were read from the HuggingFace LFS API, not from a download
# script. Only quantized builds are offered: the full-precision ggml files run
# to several gigabytes, which is not something to pull on a first utterance.
MODELS: Dict[str, ModelSpec] = {
    spec.key: spec
    for spec in (
        ModelSpec(
            "tiny-q5_1", "ggml-tiny-q5_1.bin", 32_212_224,
            "818710568da3ca15689e31a743197b520007872ff9576237bda97bd1b469c3d7",
            "fastest; for weak hardware or a proof the pipeline works",
        ),
        ModelSpec(
            "base-q5_1", "ggml-base-q5_1.bin", 59_713_024,
            "422f1ae452ade6f30a004d7e5c6a43195e4433bc370bf23fac9cc591f01a8898",
            "the default balance",
        ),
        ModelSpec(
            "small-q5_1", "ggml-small-q5_1.bin", 190_142_720,
            "ae85e4a935d7a567bd102fe55afc16bb595bdb618e11b2fc7591bc08120411bb",
            "noticeably better, 3x the download",
        ),
    )
}

DEFAULT_MODEL = "base-q5_1"

# `HardwareProfile.get_whisper_model()` returns bare tier names ("tiny",
# "base", "medium"); `stt.py` looks for `ggml-<tier>.bin` and then the
# quantized spellings. This maps a tier onto the small file we would actually
# download, so the two agree instead of the provisioner writing a filename
# nothing reads.
_TIER_TO_KEY = {"tiny": "tiny-q5_1", "base": "base-q5_1", "small": "small-q5_1"}


def resolve_key(name: str) -> str:
    """Map a hardware tier name onto a provisionable model key."""
    if name in MODELS:
        return name
    return _TIER_TO_KEY.get(name, name)

# Mirrors `stt.py`'s search order exactly: the user's own copy first, then a
# distro-packaged one. This module must not invent a third location, or a model
# written here would be invisible to the code that reads it.
_SYSTEM_DIR = Path("/usr/share/whisper/models")


class ProvisionError(Exception):
    """A model could not be provisioned. The message is user-facing."""


class ConsentRequired(ProvisionError):
    """Provisioning is off until the user turns it on."""


class DigestMismatch(ProvisionError):
    """The downloaded bytes did not match the pinned digest. Nothing installed."""


def model_dir() -> Path:
    """The user model directory - the same one `stt.py` reads."""
    return files.data_home() / "whisper" / "models"


def model_path(key: str = DEFAULT_MODEL) -> Path:
    """Where `key` lives, or would live.

    Mirrors `WhisperSTT._get_model_path`, including its preference for the
    user's directory over the packaged one, so writing here is what makes
    `is_available()` true without any change to `stt.py`.
    """
    spec = _spec(key)
    user = model_dir() / spec.filename
    if user.exists():
        return user
    return _SYSTEM_DIR / spec.filename


def is_provisioned(key: str = DEFAULT_MODEL) -> bool:
    """Whether a model file exists - either the user's or the distro's."""
    try:
        return model_path(key).is_file()
    except ProvisionError:
        return False


def _spec(key: str) -> ModelSpec:
    spec = MODELS.get(resolve_key(key))
    if spec is None:
        raise ProvisionError(
            f"there is no provisionable STT model called {key!r}; "
            f"choose one of {', '.join(sorted(MODELS))}"
        )
    return spec


def _consent(config=None) -> tuple:
    """Whether the user has allowed model downloads, and why not if they haven't.

    Returns `(allowed, reason)` rather than a bare bool because the reason is
    what the refusal message needs; this mirrors the `_consent(config) ->
    tuple[bool, str]` shape the skill modules already use.
    """
    if config is None:
        from shani_chronoa.config import ChronoaConfig
        config = ChronoaConfig()
    if config.get_bool("model-download-enabled", False):
        return True, ""
    return False, (
        "downloading a speech model is turned off. Enable 'Model download' in "
        "Settings, or install one yourself with `sudo pacman -S whisper-cpp` "
        "and drop the file into the model directory"
    )


def provision(
    key: str = DEFAULT_MODEL,
    *,
    config=None,
    progress: Optional[Callable[[int, int], None]] = None,
    transport=None,
) -> Path:
    """Fetch and verify `key`, then return the path it now lives at.

    Returns immediately if the model is already present. Raises
    `ConsentRequired`, `ProvisionError` or `DigestMismatch` otherwise - and on
    any failure path, leaves no file behind at the destination.

    `transport` exists for tests; production callers leave it None.
    """
    key = resolve_key(key)
    spec = _spec(key)

    # Read from wherever `stt.py` would, but always WRITE to the user's own
    # directory: `model_path` falls back to /usr/share when the user has no
    # copy, and installing there would need root and would put a per-user
    # download into a system location. `stt.py` prefers the user directory, so
    # writing there is what makes `is_available()` true with no change to it.
    destination = model_dir() / spec.filename
    if model_path(key).is_file():
        logger.info("STT model %s is already present at %s", spec.key, model_path(key))
        return model_path(key)

    allowed, reason = _consent(config)
    if not allowed:
        raise ConsentRequired(
            f"{reason} — expected it at {model_dir()} or {_SYSTEM_DIR} as "
            f"{spec.filename}"
        )

    files.ensure_private_dir(model_dir())

    # A private temp in the *destination directory*, so the final rename stays
    # within one filesystem and is therefore atomic.
    handle, tmp_name = tempfile.mkstemp(
        dir=str(model_dir()), prefix=f".{spec.filename}.", suffix=".part"
    )
    tmp = Path(tmp_name)
    digest = hashlib.sha256()
    written = 0
    fetched_url = spec_url(spec)
    # _stream is a generator, so it cannot return a status; the audit record
    # needs one, and a record that always says status=None is worse than no
    # record because it looks like a completed attempt.
    seen: Dict[str, object] = {}

    try:
        with os.fdopen(handle, "wb") as sink:
            for chunk in _stream(spec, progress, transport, fetched_url, digest, seen):
                sink.write(chunk)
                written += len(chunk)
            sink.flush()
            os.fsync(sink.fileno())
        files.restrict_file(tmp)

        if written != spec.size_bytes:
            raise DigestMismatch(
                f"{spec.filename} downloaded {written} bytes but the pinned size "
                f"is {spec.size_bytes}; nothing was installed"
            )
        actual = digest.hexdigest()
        if actual != spec.sha256:
            raise DigestMismatch(
                f"{spec.filename} hashed to {actual[:16]}... but the pinned "
                f"digest is {spec.sha256[:16]}...; nothing was installed. This "
                "is what a tampered or corrupted download looks like, so it is "
                "not being used."
            )

        os.replace(tmp, destination)
        files.restrict_file(destination)
        logger.info("Provisioned STT model %s to %s", spec.key, destination)
        return destination
    except OSError as exc:
        raise ProvisionError(f"could not write the STT model: {exc}") from exc
    finally:
        tmp.unlink(missing_ok=True)
        egress.record(
            "stt:provision",
            seen.get("url") or fetched_url,
            method="GET",
            status=seen.get("status"),
            bytes_out=written,
            privacy_mode=egress.privacy_mode_enabled(),
        )


def spec_url(spec: ModelSpec) -> str:
    """The canonical download URL for `spec`."""
    return f"{_BASE_URL}/{spec.filename}"


def _stream(spec, progress, transport, url, digest, seen):
    """Yield verified-stream chunks, re-checking every redirect hop.

    Redirects are followed by hand rather than by httpx so each hop passes
    through `check_destination` before it is requested. Handing a redirect to
    the client would let a remote server send us somewhere the policy has not
    seen.
    """
    import httpx

    fetched_url = url
    hops = 0
    with httpx.Client(
        timeout=_TIMEOUT, follow_redirects=False, transport=transport,
        headers={"User-Agent": _USER_AGENT},
    ) as client:
        while True:
            _guard(fetched_url)
            with client.stream("GET", fetched_url) as response:
                status = response.status_code
                seen["status"] = status
                seen["url"] = str(response.url)
                if response.has_redirect_location:
                    hops += 1
                    if hops > _MAX_REDIRECTS:
                        raise ProvisionError(
                            f"the download for {spec.filename} redirected more "
                            f"than {_MAX_REDIRECTS} times"
                        )
                    location = response.headers["location"]
                    fetched_url = str(response.url.join(location))
                    continue
                if status != 200:
                    raise ProvisionError(
                        f"the server answered {status} for {spec.filename}"
                    )
                _guard_size(response, spec)
                for chunk in response.iter_bytes():
                    digest.update(chunk)
                    if progress is not None:
                        progress(len(chunk), spec.size_bytes)
                    yield chunk
            return


def _guard(url: str) -> None:
    """Refuse a destination the egress policy rejects. Fails closed."""
    try:
        egress.check_destination(url)
    except egress.DestinationRefused as exc:
        raise ProvisionError(f"refusing to download from there: {exc}") from exc


def _guard_size(response, spec: ModelSpec) -> None:
    """Refuse an implausible length before spending the bandwidth."""
    declared = response.headers.get("content-length")
    if declared and declared.isdigit() and int(declared) > _MAX_BYTES:
        raise ProvisionError(
            f"{spec.filename} claims to be {int(declared)} bytes, which is "
            f"above the {_MAX_BYTES}-byte ceiling; refusing to download it"
        )