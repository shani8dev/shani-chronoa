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
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Dict, Optional

from shani_chronoa import egress, files

logger = logging.getLogger(__name__)

# HuggingFace serves the canonical ggml builds. `resolve/main` redirects to a
# content-addressed CDN host, so the redirect walk below re-checks each hop.
_BASE_URL = "https://huggingface.co/ggerganov/whisper.cpp/resolve/main"

PARAKEET_BASE_URL = "https://huggingface.co/ggml-org/parakeet-GGUF/resolve/main"

_USER_AGENT = "shani-chronoa/0.1 (STT model provisioning)"

# A model is a few tens to a few hundred MB. 1 GiB is far above any real ggml
# file and well below "the connection died mid-stream and we filled the disk".
_MAX_BYTES = 1 << 30

_MAX_REDIRECTS = 5

# Refuse to sit on a socket forever. Generous for a 181 MB file on a slow link,
# but this is not a download manager.
_TIMEOUT = 120.0

#: Tries per file when the connection itself fails (reset, refused, timed out).
#: A dropped connection is common on a long download and says nothing about
#: the file; a wrong status, size or digest is never retried.
_ATTEMPTS = 3

#: Seconds before the second try; doubled before the third.
_RETRY_PAUSE = 2.0


@dataclass(frozen=True)
class ModelSpec:
    """One provisionable model: which file, how big, and what it must hash to."""

    key: str
    filename: str
    size_bytes: int
    sha256: str
    note: str = ""
    #: Empty means `_BASE_URL`. Set only where a model lives in a different
    #: repository, which is the whole reason Parakeet is a separate table
    #: rather than three more entries in `MODELS`.
    base_url: str = ""


# Sizes are the HF LFS `size` for each blob; digests are the same field's
# `sha256`. (The sizes were wrong until 2026-10-02 - rounded figures that made
# every download fail the size check; found by shani-testbed's chronoa-setup,
# which downloads for real. Re-read from the HF API that day.) Both were read from the HuggingFace LFS API, not from a download
# script. Only quantized builds are offered: the full-precision ggml files run
# to several gigabytes, which is not something to pull on a first utterance.
MODELS: Dict[str, ModelSpec] = {
    spec.key: spec
    for spec in (
        ModelSpec(
            "tiny-q5_1", "ggml-tiny-q5_1.bin", 32_152_673,
            "818710568da3ca15689e31a743197b520007872ff9576237bda97bd1b469c3d7",
            "fastest; for weak hardware or a proof the pipeline works",
        ),
        ModelSpec(
            "base-q5_1", "ggml-base-q5_1.bin", 59_707_625,
            "422f1ae452ade6f30a004d7e5c6a43195e4433bc370bf23fac9cc591f01a8898",
            "the default balance",
        ),
        ModelSpec(
            "small-q5_1", "ggml-small-q5_1.bin", 190_085_487,
            "ae85e4a935d7a567bd102fe55afc16bb595bdb618e11b2fc7591bc08120411bb",
            "noticeably better, 3x the download",
        ),
    )
}

DEFAULT_MODEL = "base-q5_1"

# Parakeet (NVIDIA TDT v3) is the second backend, from `stt_parakeet.py`. It is
# served from a different repository, so it cannot join `MODELS` above without
# that dict's URL becoming per-spec - and the download, consent gating and
# digest verification are shared, not reimplemented.
#
# Sizes are the HF LFS `size` and digests the same field's `sha256`, read from
# the HuggingFace LFS API for `ggml-org/parakeet-GGUF`. Only q4_0 and q8_0 are
# offered: the f16 build is 1.26 GB and f32 2.51 GB, which is not something to
# pull on a first utterance, and nothing here needs more precision than that.
PARAKEET_MODELS: Dict[str, ModelSpec] = {
    spec.key: spec
    for spec in (
        ModelSpec(
            "parakeet-q4_0", "ggml-parakeet-tdt-0.6b-v3-q4_0.bin", 355_615_679,
            "aa7fe2f5fb47d863ca23e8b1d490632d63a2599f515268b6d6bd656158dad45e",
            "smallest; the default balance", PARAKEET_BASE_URL,
        ),
        ModelSpec(
            "parakeet-q8_0", "ggml-parakeet-tdt-0.6b-v3-q8_0.bin", 668_757_119,
            "4d64e9e96c2792186d072fde0034df0ad670cf680a2f53069052ead827fd600e",
            "more accurate, ~2x the download", PARAKEET_BASE_URL,
        ),
    )
}

PARAKEET_DEFAULT_MODEL = "parakeet-q4_0"

# `ParakeetSTT`'s default model is the bare `tdt-0.6b-v3`; the quantized
# filenames above are what this backend downloads. Mapping one onto the other
# is what stops the provisioner writing a file the reader never looks for.
PARAKEET_MODEL_STEM = "tdt-0.6b-v3"

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

# The same two directories for the Parakeet backend, matching
# `stt_parakeet.py`'s `_get_model_path`. Separate because the two backends'
# models are different files with different names; one shared list would let a
# whisper lookup resolve into the parakeet directory.
_PARAKEET_SYSTEM_DIR = Path("/usr/share/parakeet/models")


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


def parakeet_model_dir() -> Path:
    """The user Parakeet model directory - the one `stt_parakeet.py` reads."""
    return files.data_home() / "parakeet" / "models"


def parakeet_model_path(key: str = PARAKEET_DEFAULT_MODEL) -> Path:
    """Where the Parakeet `key` lives, or would live.

    Mirrors `model_path` for the Parakeet backend, including its preference
    for the user's directory, for the same reason: writing where the reader
    looks is what makes `ParakeetSTT.is_available()` true.
    """
    spec = _parakeet_spec(key)
    user = parakeet_model_dir() / spec.filename
    if user.exists():
        return user
    return _PARAKEET_SYSTEM_DIR / spec.filename


def is_parakeet_provisioned(key: str = PARAKEET_DEFAULT_MODEL) -> bool:
    """Whether a Parakeet model file exists - the user's or the distro's."""
    try:
        return parakeet_model_path(key).is_file()
    except ProvisionError:
        return False


def _parakeet_spec(key: str) -> ModelSpec:
    """Resolve a Parakeet key, accepting the bare model stem `stt.py` uses.

    `ParakeetSTT` searches for `ggml-parakeet-<stem>[-qN].bin`, so a caller that
    passes `stt.build_stt`'s model through unchanged must still land on a
    provisionable key rather than an error naming two unrelated models.
    """
    spec = PARAKEET_MODELS.get(key)
    if spec is None and key in (PARAKEET_MODEL_STEM, ""):
        spec = PARAKEET_MODELS[PARAKEET_DEFAULT_MODEL]
    if spec is None:
        raise ProvisionError(
            f"there is no provisionable Parakeet model called {key!r}; "
            f"choose one of {', '.join(sorted(PARAKEET_MODELS))}"
        )
    return spec


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
    return _install(
        _spec(key),
        user_dir=model_dir(),
        system_dir=_SYSTEM_DIR,
        existing=_existing(key, model_path),
        config=config,
        progress=progress,
        transport=transport,
    )


def provision_parakeet(
    key: str = PARAKEET_DEFAULT_MODEL,
    *,
    config=None,
    progress: Optional[Callable[[int, int], None]] = None,
    transport=None,
) -> Path:
    """Fetch and verify the Parakeet `key`, then return where it now lives.

    `provision` verbatim with a different spec table, directory and host. The
    verification is deliberately *not* a second implementation: consent gating,
    the size check, the digest check, the private temp and the atomic rename all
    live in `_install`, so a Parakeet download is refused by exactly the same
    rules as a whisper one. A backend that skipped verification would be a
    security regression, and a copy of this function is how that would happen.
    """
    return _install(
        _parakeet_spec(key),
        user_dir=parakeet_model_dir(),
        system_dir=_PARAKEET_SYSTEM_DIR,
        existing=_existing(key, parakeet_model_path),
        config=config,
        progress=progress,
        transport=transport,
    )


def _existing(key, resolve) -> Optional[Path]:
    """Where `key` already is, or None. Read-only; `_install` writes the rest."""
    found = resolve(key)
    if found.is_file():
        logger.info("STT model %s is already present at %s", key, found)
        return found
    return None


def _install(
    spec: ModelSpec,
    *,
    user_dir: Path,
    system_dir: Path,
    existing: Optional[Path],
    config=None,
    progress: Optional[Callable[[int, int], None]] = None,
    transport=None,
    label: str = "stt",
) -> Path:
    """The shared body of `provision` and `provision_parakeet` - and of `local_llm` and `voices`.

    `label` names the kind of file in logs and the egress record ("stt",
    "llm", "voice"); the verification is identical for all of them.

    Reads from wherever the reader would (`existing`, resolved by the caller
    against this backend's own `model_path`), but always WRITES to the user's
    directory: falling back to a system location would need root and would put
    a per-user download into one. Both readers prefer the user directory, so
    writing there is what makes `is_available()` true with no change to them.
    """
    if existing is not None:
        return existing
    destination = user_dir / spec.filename

    allowed, reason = _consent(config)
    if not allowed:
        raise ConsentRequired(
            f"{reason} — expected it at {user_dir} or {system_dir} as "
            f"{spec.filename}"
        )

    files.ensure_private_dir(user_dir)

    # A private temp in the *destination directory*, so the final rename stays
    # within one filesystem and is therefore atomic.
    handle, tmp_name = tempfile.mkstemp(
        dir=str(user_dir), prefix=f".{spec.filename}.", suffix=".part"
    )
    tmp = Path(tmp_name)
    digest = hashlib.sha256()
    written = 0
    fetched_url = spec_url(spec)
    # _stream is a generator, so it cannot return a status; the audit record
    # needs one, and a record that always says status=None is worse than no
    # record because it looks like a completed attempt.
    seen: Dict[str, object] = {}

    import httpx

    try:
        os.close(handle)
        attempt = 0
        while True:
            attempt += 1
            # A retry continues where the last try stopped (HTTP Range) rather
            # than from byte zero: on a slow link a 2 GB model that times out
            # at 1.8 GB should not cost 1.8 GB again. The file and the hash
            # always hold the same bytes - a chunk is hashed and written
            # together - so the digest check at the end still covers the whole
            # file, resumed or not.
            try:
                with open(tmp, "ab" if written else "wb") as sink:
                    for chunk in _stream(spec, progress, transport, fetched_url, digest, seen, start=written):
                        sink.write(chunk)
                        written += len(chunk)
                    sink.flush()
                    os.fsync(sink.fileno())
                break
            except _RangeIgnored:
                # the server sent the whole file again: start the file and the hash over
                logger.info("%s cannot be resumed from that server; starting it again", spec.filename)
                digest, written = hashlib.sha256(), 0
                attempt -= 1
                continue
            except httpx.TransportError as exc:
                if attempt == _ATTEMPTS:
                    raise ProvisionError(
                        f"could not download {spec.filename} after {_ATTEMPTS} tries: {exc}"
                    ) from exc
                logger.warning("Downloading %s failed (%s); trying again from %d MB", spec.filename, exc,
                               written >> 20)
                time.sleep(_RETRY_PAUSE * 2 ** (attempt - 1))
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
        logger.info("Provisioned %s file %s to %s", label, spec.key, destination)
        return destination
    except OSError as exc:
        raise ProvisionError(f"could not write the {label} file: {exc}") from exc
    finally:
        tmp.unlink(missing_ok=True)
        egress.record(
            f"{label}:provision",
            seen.get("url") or fetched_url,
            method="GET",
            status=seen.get("status"),
            bytes_out=written,
            privacy_mode=egress.privacy_mode_enabled(),
        )


def file_matches(path: Path, spec: ModelSpec) -> bool:
    """Whether `path` still has `spec`'s pinned size and sha256 (about a second per GB).

    For a file already in place: being there is not the same as being right - a
    copied-in, truncated or altered model must be fetched again, not loaded.
    """
    try:
        if path.stat().st_size != spec.size_bytes:
            return False
        digest = hashlib.sha256()
        with path.open("rb") as handle:
            for block in iter(lambda: handle.read(1 << 20), b""):
                digest.update(block)
    except OSError:
        return False
    return digest.hexdigest() == spec.sha256


def install_verified(spec: ModelSpec, directory: Path, *, config=None, progress=None, transport=None,
                     label: str) -> Path:
    """`spec` in `directory`: kept if it matches its pin, otherwise (re)downloaded and verified."""
    present = directory / spec.filename
    if present.is_file() and not file_matches(present, spec):
        logger.warning("%s does not match its pinned digest; fetching it again", present)
        present.unlink()
    return _install(spec, user_dir=directory, system_dir=Path("/usr/share/shani-chronoa") / label,
                    existing=present if present.is_file() else None, config=config,
                    progress=progress, transport=transport, label=label)


def spec_url(spec: ModelSpec) -> str:
    """The canonical download URL for `spec`."""
    return f"{spec.base_url or _BASE_URL}/{spec.filename}"


class _RangeIgnored(Exception):
    """A resumed request was answered with the whole file (200, not 206)."""


def _stream(spec, progress, transport, url, digest, seen, start: int = 0):
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
            headers = {"Range": f"bytes={start}-"} if start else None
            with client.stream("GET", fetched_url, headers=headers) as response:
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
                if start and status == 200:
                    raise _RangeIgnored()
                if status != (206 if start else 200):
                    raise ProvisionError(
                        f"the server answered {status} for {spec.filename}"
                    )
                if not start:
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
    # the ceiling is the larger of the global one and this file's own pinned
    # size (a 1.1 GB language model is legitimate; 1.1 GB claimed for a 60 MB
    # speech model is not)
    ceiling = max(_MAX_BYTES, spec.size_bytes)
    if declared and declared.isdigit() and int(declared) > ceiling:
        raise ProvisionError(
            f"{spec.filename} claims to be {int(declared)} bytes, which is "
            f"above the {ceiling}-byte ceiling; refusing to download it"
        )