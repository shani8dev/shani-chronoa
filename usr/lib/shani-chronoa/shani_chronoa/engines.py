"""The programs that run the local models - fetched when the system has none.

llama.cpp and whisper.cpp are two separate halves. The **model** half has
always been downloadable (`local_llm.SPECS`, `stt_provision.MODELS`); the
**program** half was a package, so on a machine without `llama-cpp` or
`whisper-cpp` setup refused with *"it comes with the next ShaniOS update"* and
the model download was never even offered.

This makes the program half downloadable the way Piper's release and
stable-diffusion.cpp already are: upstream's own Linux build, pinned by size
and the sha256 GitHub publishes for the release asset, unpacked into the
user's own data directory. Nothing is installed system-wide and nothing needs
root.

**The upstream Linux binaries live on the nightly tag, not the version tag.**
Measured 2026-10-18: llama.cpp `v0.7.0` carries one asset (`nightly-tag.txt`)
and whisper.cpp `v1.9.5` carries none, while `b11719` / `b5454` carry 36 and
12. A pinned tag is immutable - an asset cannot be replaced without deleting
the release - so pinning `b11719` is as reproducible as pinning a version,
and `sherpa.py` already pins a tag for the same reason.

**They need no wrapper.** Both carry `RUNPATH=$ORIGIN` (measured with
`readelf -d`), so `llama-server --version` and `whisper-cli --help` both run
from an unpacked directory under `env -i` with no `LD_LIBRARY_PATH` and no
shim. That is what makes this the same shape as `sherpa.py` rather than a
parallel invention.

**What each archive was measured to contain, because two of these decide what
works at all:**

- `libmtmd.so` is linked into `llama-server`, so the **Eyes** extra's
  `--mmproj` path works with the downloaded engine and not only with Arch's
  libmtmd-patched build.
- `parakeet-cli` ships **inside the whisper.cpp archive**, so the second STT
  backend costs nothing extra - one download serves both.
- all ten flags `local_llm.server_args()` passes are accepted by this build
  (`-m --host --port --jinja -c -ngl -t -ctk -ctv --no-webui`), checked
  against its own `--help` rather than assumed from the installed one.

**The system's copy always wins.** `resolve()` asks `PATH` first and only then
looks here, so on a machine that already has the package nothing about its
behaviour changes, and a distro build - which is patched for the distro -
keeps precedence over an unpinned-for-this-distro nightly. This is a fallback,
not an override.

**tesseract is deliberately absent.** Upstream's latest release (5.5.3) has
exactly one asset, a 26 MB Windows `.exe` installer; there is no Linux build
to mirror. What *is* downloadable is the language data, and `languages.py`
already pins and fetches `tessdata_fast` at a fixed commit. So the OCR engine
stays a system package and this module does not pretend otherwise.
"""

from __future__ import annotations

import logging
import os
import shutil
from pathlib import Path

from shani_chronoa import files
from shani_chronoa.stt_provision import ModelSpec, _install, unpack_tar

logger = logging.getLogger(__name__)

#: Pinned 2026-10-18. Sizes and digests are GitHub's own, for the release
#: assets, and both were confirmed against an actual download of each
#: (`sha256sum` matched the API's `digest` field byte for byte).
_LLAMA_URL = "https://github.com/ggml-org/llama.cpp/releases/download/b11719"
_WHISPER_URL = "https://github.com/ggml-org/whisper.cpp/releases/download/b5454"

#: The CPU build, which runs everywhere and matches what a machine with no GPU
#: needs. 18 MB.
LLAMA_CPU = ModelSpec(
    "llama-cpp", "llama-b11719-bin-ubuntu-x64.tar.gz", 17_968_546,
    "2c50edd1422cdc686a1620923a7dda9a33634509566d2821155fb3d4879510a3",
    "llama.cpp b11719 (CPU)", _LLAMA_URL)

#: The Vulkan build, for a machine with any Vulkan GPU - which is what Arch's
#: `ggml-vulkan` optdepend provides, so this is the same choice that package
#: offers. 30 MB.
LLAMA_VULKAN = ModelSpec(
    "llama-cpp-vulkan", "llama-b11719-bin-ubuntu-vulkan-x64.tar.gz", 30_611_378,
    "9a09cce245acf8823feb99fb7ba8ddd3e878e3827564888131e0185044aa4429",
    "llama.cpp b11719 (Vulkan)", _LLAMA_URL)

#: whisper.cpp's release carries `whisper-cli`, `whisper-server` *and*
#: `parakeet-cli`, so one 10 MB fetch covers all three of Chronoa's speech
#: programs and both STT backends.
WHISPER = ModelSpec(
    "whisper.cpp", "whisper-bin-ubuntu-x64.tar.gz", 10_364_195,
    "a72becf15d7917f990f6313867a52638b82b7f9ef237fb0c980dac56a135781c",
    "whisper.cpp b5454 (CPU)", _WHISPER_URL)

ENGINES = {spec.key: spec for spec in (LLAMA_CPU, LLAMA_VULKAN, WHISPER)}

#: Which engine provides each program Chronoa runs. One table rather than a
#: search of the unpacked directory, because a program's origin is a fact and
#: "whatever `b5454` happened to ship" is not one - the archive also contains
#: `test-whisper-*` and `test-parakeet-*`, and those must never be chosen for
#: anything.
_PROGRAMS = {
    "llama-server": ("llama-cpp", "llama-cpp-vulkan"),
    "llama-perplexity": ("llama-cpp", "llama-cpp-vulkan"),
    "llama-quantize": ("llama-cpp", "llama-cpp-vulkan"),
    "llama-imatrix": ("llama-cpp", "llama-cpp-vulkan"),
    "whisper-cli": ("whisper.cpp",),
    "whisper-server": ("whisper.cpp",),
    "parakeet-cli": ("whisper.cpp",),
}

#: The engine to install when a program is wanted and the system has none, in
#: order of preference. The CPU build comes first so a machine with no GPU is
#: never handed the 30 MB one; a caller that knows better says so.
DEFAULT_FOR_PROGRAM = {
    "llama-server": LLAMA_CPU, "llama-perplexity": LLAMA_CPU,
    "llama-quantize": LLAMA_CPU, "llama-imatrix": LLAMA_CPU,
    "whisper-cli": WHISPER, "whisper-server": WHISPER, "parakeet-cli": WHISPER,
}


class EngineMissing(Exception):
    """The program could not be found and could not be installed. The message is user-facing."""


def engine_dir(key: str) -> Path:
    return files.data_home() / "shani-chronoa" / "engines" / key


def _binary_dir(key: str) -> Path:
    """Where the archive's programs land: **the engine directory itself**.

    Both archives unpack **flat** - measured on the real ones, `tar` lists
    `llama-server`, `whisper-cli` and `parakeet-cli` as members of the root, and
    neither archive has a top folder at all. An earlier version of this function
    appended the archive's stem on the assumption that it did, so `installed()`
    looked one directory below the programs and answered `''` for an engine that
    was unpacked correctly - and `ensure()` then refused a program that was
    sitting there, having just downloaded it.

    It is a named function rather than `engine_dir` spelled out at the two call
    sites because the read path and `install_engine`'s post-unpack verification
    must name the same directory; they are the two halves of one question, and
    two hand-written paths are how they came to disagree.
    """
    return engine_dir(key)


def installed(name: str) -> str:
    """This user's own copy of `name`, or '' when there is none."""
    for key in _PROGRAMS.get(name, ()):
        path = _binary_dir(key) / name
        if os.access(path, os.X_OK):
            return str(path)
    return ""


def resolve(name: str) -> str:
    """`name` to run: the system's copy first, then one downloaded here.

    **PATH before the user's own directory** is the whole design decision, and
    it is the safe direction: a distro package is built against the distro's
    libraries and, on Arch, against `libmtmd`, and a machine that already has
    one should keep it. Downloading is a fallback for a machine that has
    nothing, so a machine that has something is not changed at all.
    """
    return shutil.which(name) or installed(name)


def has(name: str) -> bool:
    return bool(resolve(name))


def engine_for(name: str, gpu: bool = False) -> ModelSpec:
    """The release that provides `name` - the Vulkan build when `gpu` and it has one."""
    if gpu and name in ("llama-server", "llama-perplexity", "llama-quantize", "llama-imatrix"):
        return LLAMA_VULKAN
    return DEFAULT_FOR_PROGRAM[name]


def _consent(config) -> tuple:
    """Whether fetching is permitted, and why not if it isn't.

    The same gate and the same switch as every model download, because it is
    the same act: `model-download-enabled` is documented in `config.py` as *"the
    only setting in this file that causes bytes to be fetched from the internet
    on the user's behalf"*, and a 18 MB program is more of that than a 190 MB
    model is less of it.
    """
    if config is None:
        from shani_chronoa.config import ChronoaConfig
        config = ChronoaConfig()
    if config.model_download_enabled:
        return True, ""
    return False, ("downloading is turned off. Turn on 'Model download' in Settings, "
                   "or install the program with your package manager")


def install_engine(spec: ModelSpec, progress=None, config=None, transport=None) -> str:
    """Fetch, verify and unpack one release; returns the directory its programs are in."""
    marker = engine_dir(spec.key) / ".release"
    if installed(_first_program(spec)) and marker.is_file() \
            and marker.read_text(encoding="utf-8").strip() == spec.filename:
        return str(_binary_dir(spec.key))
    archive = _install(spec, user_dir=engine_dir(spec.key),
                       system_dir=Path("/usr/share/shani-chronoa/engines"), existing=None,
                       config=config, progress=progress, transport=transport, label="engine")
    try:
        unpack_tar(archive, engine_dir(spec.key))
    finally:
        archive.unlink(missing_ok=True)
    for name, keys in _PROGRAMS.items():
        if spec.key in keys and not (engine_dir(spec.key) / name).exists():
            raise EngineMissing(f"the {spec.note} archive did not contain {name}")
    marker.parent.mkdir(parents=True, exist_ok=True)
    marker.write_text(spec.filename + "\n", encoding="utf-8")
    return str(_binary_dir(spec.key))


def _first_program(spec: ModelSpec) -> str:
    for name, keys in _PROGRAMS.items():
        if spec.key in keys:
            return name
    return ""


def ensure(name: str, *, progress=None, config=None, transport=None,
           gpu: bool = False) -> str:
    """`name` to run, installing the engine that provides it when the system has none.

    Returns the path, or raises `EngineMissing` with a sentence a person can act
    on. Consent is refused before anything is fetched and says which switch
    opens it; a program this tree has no release for says so rather than
    inventing a download.

    `progress` is the **same `(chunk_bytes, total_bytes)` hook** every other
    download here takes - `stt_provision._install`, `sherpa.install`,
    `local_llm`, `voices` and the wizard's own `_progress` - and it is forwarded
    to `_install` unchanged. That shape used to be a `(fraction, message)`
    call of this function's own, which meant one parameter carried two
    contracts: the fraction call reached a byte hook as
    `hook(0.0, "llama.cpp b11719 (CPU): downloading 17 MB")` and the wizard's
    `_progress` then computed `max(total, 1)` against a **string**, raising
    `TypeError` on the first progress callback of every download. One contract
    is the fix, and one spelling of it is what keeps the next download from
    finding the seam again.

    The two lifecycle announcements a fraction hook made are not lost. `_install`
    already calls `progress(size_bytes, size_bytes)` when it is done, so the hook
    reaches its own 100% and says so; and its per-chunk calls fire as soon as
    bytes arrive, which is the same first progress the pre-announcement produced.

    There is no `cancel` parameter, deliberately. Cancellation is a property of
    the caller's hook - the wizard's `_progress` raises `Cancelled` from inside
    itself - so a parameter here would only suggest a capability this function
    does not have.
    """
    found = resolve(name)
    if found:
        return found
    if name not in DEFAULT_FOR_PROGRAM:
        raise EngineMissing(
            f"{name} is not installed, and Chronoa has no download for it - it comes "
            f"with your system packages.")
    allowed, reason = _consent(config)
    if not allowed:
        raise EngineMissing(f"{name} is not installed. {reason}.")
    spec = engine_for(name, gpu=gpu)
    install_engine(spec, progress=progress, config=config, transport=transport)
    found = resolve(name)
    if not found:
        raise EngineMissing(f"{name} did not appear after installing {spec.note}.")
    return found


def pending_bytes(name: str) -> int:
    """How many bytes setup would have to fetch to get `name`, **0 when it has nothing to fetch**.

    The review page's total is the number a person agrees to, so an engine that
    is already on the system - or already downloaded here - must contribute
    nothing to it. `0` for a program this tree has no release for is deliberate
    rather than a guess at its size: `ensure()` will refuse that program with a
    sentence saying it comes from the system packages, so pricing it here would
    be quoting a download nobody is ever offered.

    A **byte count and not a formatted string** because the caller divides it by
    whatever its own rows are measured in - the wizard prints MB and GB from one
    number, and a string here would be re-parsed by three call sites.
    """
    if has(name):
        return 0
    spec = DEFAULT_FOR_PROGRAM.get(name)
    return int(spec.size_bytes) if spec else 0


def missing_reason(name: str, config=None) -> str:
    """Why `name` is not available, or '' when it is. Never a guess about a program we could fetch.

    **Deliberately not called, and recorded here so the next sweep stops
    here.** `ensure()` answers the same question at the moment of the
    attempt, and it has to: its answer carries the *consent* refusal, which
    is the reason the program is not there right now, and a panel that
    printed the sentence above instead would hide that. This is the
    panel-shaped wording - it names the size of a download setup could
    fetch - and it is kept for a panel that has to answer without asking
    for the thing.
    """
    if has(name):
        return ""
    if name in DEFAULT_FOR_PROGRAM:
        return (f"{name} is not installed. Setup can download it "
                f"({DEFAULT_FOR_PROGRAM[name].size_bytes // (1 << 20)} MB) when 'Model download' is on.")
    return f"{name} is not installed, and it comes with your system packages."