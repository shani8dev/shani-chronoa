"""Model-fit sense: which language models this machine can actually run.

`HardwareProfile.get_model()` picks a model from a two-way hardware tier, and
that is the whole of Chronoa's model selection:

    if self.profile in ("gpu", "high", "medium"):
        return "qwen3:4b"
    else:
        return "qwen3:1.7b"

It never asks what is installed, never checks that the answer fits in memory,
and reports nothing at all if the model it names is not present. On the machine
this was written against - 31.8 GB of RAM, no GPU, `profile=high` - it selects
`qwen3:4b`, roughly 2.5 GB, which is the same answer a 16 GB machine gets. The
tier exists to stop a small machine being handed a model it cannot load, and it
does that job; it just has no idea how much headroom is actually left over.

This sense closes the gap by reporting three separate facts that the tier
conflates into one guess:

1. **What this machine has** - RAM, cores, GPU - and how much RAM is *available*
   rather than merely total, since total is what the tier reads and available is
   what a model has to fit inside.
2. **What is installed** - the real model list from Ollama's own `/api/tags`.
3. **Whether the configured model is present and fits.**

**Read-only.** It never downloads, loads, unloads, pulls or modifies a model.
It reports a verdict and a recommendation; acting on that is the user's or the
assistant's separate decision.

**Ollama being unreachable is UNKNOWN, not "no models installed."** Those are
different claims and only one is safe to guess - reporting an empty model list
because the daemon was down would read as "this machine has no models," which
is exactly the confident wrong answer this whole module is correcting. The
daemon being down is a normal state for a machine that has Ollama installed but
not started, so it is reported as such.

Sizes come from Ollama's own reported bytes, so nothing here is a guess about a
model's memory footprint: what it cannot know, it says it cannot know.
"""

import glob
import json
import logging
import os
import shutil
import urllib.error
import urllib.request
from pathlib import Path
from typing import List, Optional, Union

from shani_chronoa.config import ChronoaConfig, HardwareProfile
from shani_chronoa.senses import SENSITIVITY_PUBLIC, Sense
from shani_chronoa.senses.context import Percept

logger = logging.getLogger(__name__)

KIND = "machine-state"
SENSITIVITY = SENSITIVITY_PUBLIC
_TTL_SECONDS = 300.0
_POLL_INTERVAL = 300.0

def _url(env_name: str, default: str) -> str:
    """A loopback port is not a fact about the machine, so it is overridable.

    8099 was a dev box's own arrangement, not llama.cpp's default. A daemon
    that a user moved to another port must read as UNKNOWN - the same state a
    machine with no Ollama produces - never as "no models installed", which is
    the distinction this sense exists to keep.
    """
    return os.environ.get(env_name, "").strip() or default


_TAGS_URL = _url("SHANI_OLLAMA_URL", "http://127.0.0.1:11434/api/tags")
_LLAMA_MODELS_URL = _url("SHANI_LLAMACPP_URL", "http://127.0.0.1:8099/v1/models")
_TIMEOUT = 4.0

# Leave headroom rather than filling RAM to the last page. A model that
# technically fits in total RAM will still fail to load if the context and KV
# cache have nowhere to go, and "it does not fit" is a useful answer where "it
# loaded and then the machine started swapping" is not.
_HEADROOM_FRACTION = 0.75

_SCHEMA = {
    "type": "function",
    "function": {
        # Named for the consent surface: `sense_allowed()` builds the key as
        # "<name>-sense-enabled", so a rename here leaves the sense
        # permanently ungrantable.
        "name": "modelfit",
        "description": (
            "Report which local language models this machine can actually run: "
            "detected RAM, cores and GPU, how much RAM is available rather than "
            "merely total, the models Ollama reports as installed with their "
            "real on-disk sizes, and whether the model currently configured is "
            "present and fits, plus which local inference backends are installed "
            "and whether they are actually usable on this hardware. "
            "Read-only - it never downloads, loads or changes a model. If "
            "Ollama is unreachable it says so rather than reporting an "
            "empty model list."
        ),
        "parameters": {"type": "object", "properties": {}},
    },
}


def _meminfo() -> dict:
    """Total and available RAM in MB, straight from the kernel."""
    out = {"total_mb": 0, "available_mb": 0}
    try:
        with open("/proc/meminfo", "r", encoding="utf-8") as handle:
            for line in handle:
                if line.startswith("MemTotal:"):
                    out["total_mb"] = int(line.split()[1]) // 1024
                elif line.startswith("MemAvailable:"):
                    out["available_mb"] = int(line.split()[1]) // 1024
                if out["total_mb"] and out["available_mb"]:
                    break
    except (OSError, ValueError, IndexError) as exc:
        logger.debug("cannot read meminfo: %s", exc)
    return out


def _from_ollama(payload: dict) -> List[dict]:
    models = []
    for entry in payload.get("models", []) or []:
        name = entry.get("name")
        if not name:
            continue
        # Ollama reports the parameter count separately from the byte size;
        # the bytes are what actually decide whether it loads, so a model with
        # no size is kept but marked rather than assumed small.
        size = entry.get("size")
        models.append({
            "name": name,
            "size_bytes": int(size) if isinstance(size, int) else None,
            "parameters": entry.get("details", {}).get("parameter_size"),
            "family": entry.get("details", {}).get("family"),
            "backend": "ollama",
        })
    return models


def _from_openai(payload: dict) -> List[dict]:
    """Read an OpenAI-compatible `/v1/models`, which is what llama.cpp serves.

    llama.cpp is listed in `_BACKENDS` as a supported engine, but this function
    only ever asked Ollama, so a machine running llama.cpp alone reported
    UNKNOWN for every model it actually had - the same "present but
    unacknowledged" gap as an ungranted consent key. Size comes from
    `size_bytes`, or from llama.cpp's own `meta.size_bytes`; a model reported
    without one is kept and marked, never assumed small.
    """
    models = []
    for entry in payload.get("data", []) or []:
        name = entry.get("id")
        if not name:
            continue
        size = entry.get("size_bytes")
        if not isinstance(size, int):
            meta = entry.get("meta") or {}
            size = meta.get("size_bytes")
        models.append({
            "name": name,
            "size_bytes": int(size) if isinstance(size, int) else None,
            "parameters": None,
            "family": None,
            "backend": "llama.cpp",
        })
    return models


# (label, url, parser): where to ask each backend what it has loaded. Ollama
# first, because it is the default and its /api/tags is what this sense has
# always used.
#
# Not named `_SOURCES`: that is the *model source* table further down
# (huggingface-cli, ollama - where weights come from, as opposed to which
# server is running). The two collided, the later definition won, and the
# loop below unpacked the 2-tuple source table as a 3-tuple endpoint - which
# only blew up once a real run reached it.
def _model_listers():
    # (label, url, parser): where to ask each backend what it has loaded.
    # Ollama first, because it is the default and its /api/tags is what this
    # sense has always used.
    #
    # A function rather than a module-level tuple because a tuple built at
    # import captures the URLs as values while the prose quotes the constants;
    # redirect one and the sense asks the old port while explaining the new one.
    #
    # Not named `_SOURCES`: that is the *model source* table further down
    # (huggingface-cli, ollama - where weights come from, as opposed to which
    # server is running). The two collided, the later definition won, and the
    # loop below unpacked the 2-tuple source table as a 3-tuple endpoint - which
    # only blew up once a real run reached it.
    return (
        ("ollama", _TAGS_URL, _from_ollama),
        ("llama.cpp", _LLAMA_MODELS_URL, _from_openai),
    )


def installed_models(url: Optional[str] = None) -> Optional[List[dict]]:
    """Models any local backend reports, or None when none could be asked.

    None is deliberately distinct from an empty list: an empty list is a real
    answer meaning "a backend is running and has no models", while None means
    "the question was not answered".

    Every source is tried before giving up, so a machine with Ollama *and*
    llama.cpp reports both rather than whichever happens to answer first.
    """
    listers = ((None, url, _from_ollama),) if url else _model_listers()
    answered = False
    found: List[dict] = []
    for label, source_url, parse in listers:
        try:
            with urllib.request.urlopen(source_url, timeout=_TIMEOUT) as response:
                payload = json.loads(response.read().decode("utf-8"))
        except (urllib.error.URLError, OSError, ValueError, json.JSONDecodeError) as exc:
            logger.debug("%s models unavailable: %s", label, exc)
            continue
        answered = True
        found.extend(parse(payload))
    if not answered:
        return None
    return found


# Known local inference backends, and what each one needs before it can
# actually serve anything. A binary being on PATH is not the same as an engine
# being usable: vLLM and SGLang both require a CUDA GPU, so on a machine with
# only integrated graphics they are present-and-useless, and saying so is more
# useful than listing them as available.
_BACKENDS = (
    ("ollama", "ollama", False),
    ("llama.cpp", "llama-server", False),
    ("lmstudio", "lms", False),
    ("vllm", "vllm", True),
    ("sglang", "sglang", True),
)

_CUDA_GLOBS = ("/dev/nvidia*", "/dev/nvidiactl", "/proc/driver/nvidia/version")


def _has_cuda() -> bool:
    """Whether a CUDA device is actually usable, rather than merely claimed."""
    for pattern in _CUDA_GLOBS:
        if glob.glob(pattern):
            return True
    return False


def backends() -> List[dict]:
    """Which local inference engines are installed, and whether they can run.

    Three states, deliberately distinct: absent, present-but-unusable (no CUDA
    for the GPU-only engines), and present-and-reachable. Collapsing the middle
    one into the last is how a machine ends up "supporting vLLM" because a
    binary exists.
    """
    cuda = _has_cuda()
    out = []
    for name, binary, needs_cuda in _BACKENDS:
        path = shutil.which(binary)
        if path is None:
            out.append({"backend": name, "state": "absent"})
            continue
        if needs_cuda and not cuda:
            out.append({
                "backend": name, "state": "present-unusable",
                "detail": f"{binary} is installed but no CUDA device is present",
            })
            continue
        out.append({"backend": name, "state": "present", "path": path})
    return out


# Model *sources*, which are a different thing from inference backends and are
# reported separately for that reason. HuggingFace is where GGUF weights
# actually live, and Ollama pulls from HuggingFace GGUF repos underneath - so
# a model fetched with `ollama pull` already has HuggingFace provenance and
# the `hf` CLI is not needed for Chronoa's own inference path. It matters only
# when GGUF files are managed directly for llama.cpp, which is why it is
# reported here and not offered as an install route.
_SOURCES = (
    ("huggingface-cli", "HF hub CLI (for GGUF managed outside Ollama)"),
    ("ollama", None),  # a directory, not a command - see model_sources()
)


def model_sources() -> List[dict]:
    """Where models can be fetched from, and which are usable here."""
    out = []
    for binary, note in _SOURCES:
        if note is None:
            # Ollama's own store is a directory, not a command.
            store = Path(os.path.expanduser("~/.ollama/models"))
            out.append({
                "source": "ollama",
                "state": "present" if store.is_dir() else "absent",
                "detail": f"store: {store}" if store.is_dir() else
                          "no ~/.ollama/models yet",
            })
            continue
        path = shutil.which(binary)
        out.append({
            "source": binary,
            "state": "present" if path else "absent",
            "detail": path or note,
        })
    return out


def _fmt_size(size_bytes: Optional[int]) -> str:
    if size_bytes is None:
        return "size unknown"
    gb = size_bytes / (1024 ** 3)
    if gb >= 1:
        return f"{gb:.1f} GB"
    return f"{size_bytes / (1024 ** 2):.0f} MB"


def _fits(size_bytes: Optional[int], budget_mb: int) -> Optional[bool]:
    """Whether a model fits the headroom-adjusted budget, or None if unknown."""
    if size_bytes is None:
        return None
    return (size_bytes / (1024 ** 2)) <= budget_mb


def _run(arguments: dict) -> Union[str, Percept]:
    config = ChronoaConfig()
    if not config.sense_allowed("modelfit"):
        return f"Not checking model fit: {config.sense_allowed_reason('modelfit')}."

    hw = HardwareProfile()
    mem = _meminfo()
    budget_mb = int(mem["available_mb"] * _HEADROOM_FRACTION)
    configured = hw.get_model()

    lines = [
        f"hardware: {mem['total_mb']} MB RAM total, {mem['available_mb']} MB available, "
        f"{hw.cpu_cores} cores, GPU {'yes (' + hw.gpu_type + ')' if hw.gpu_available else 'no'}"
    ]
    lines.append(
        f"fit budget: {budget_mb} MB ({int(_HEADROOM_FRACTION * 100)}% of available, "
        "leaving room for context and KV cache)"
    )
    lines.append(
        f"configured by hardware tier: {configured} (profile={hw.profile})"
    )

    engines = backends()
    lines.append("local inference backends:")
    for engine in engines:
        if engine["state"] == "present":
            lines.append(f"  {engine['backend']}: installed ({engine['path']})")
        elif engine["state"] == "present-unusable":
            lines.append(f"  {engine['backend']}: {engine['detail']}")
        else:
            lines.append(f"  {engine['backend']}: not installed")

    lines.append("model sources (where weights come from):")
    for source in model_sources():
        if source["state"] == "present":
            lines.append(f"  {source['source']}: {source['detail']}")
        else:
            lines.append(
                f"  {source['source']}: not installed "
                f"({source['detail']})"
            )
    lines.append(
        "  note: Ollama pulls GGUF from HuggingFace itself, so a model "
        "installed with `ollama pull` already has HuggingFace provenance; the "
        "hf CLI is only needed for GGUF managed outside Ollama."
    )

    models = installed_models()
    if models is None:
        lines.append(
            "installed models: UNKNOWN - Ollama did not answer on "
            f"{_TAGS_URL}. That is not the same as having no models: the "
            "daemon may simply not be running, so whether the configured "
            "model is present could not be checked either."
        )
        return _SENSE.to_percept(
            "\n".join(lines),
            source="ollama-tags",
            metadata={"reachable": False, "configured": configured, "models": None},
        )

    if not models:
        lines.append(
            f"installed models: none - Ollama is running at {_TAGS_URL} and "
            f"reports an empty list, so the configured {configured} is NOT "
            "present on this machine."
        )
        return _SENSE.to_percept(
            "\n".join(lines),
            source="ollama-tags",
            metadata={"reachable": True, "configured": configured, "models": 0},
        )

    lines.append(f"installed models: {len(models)}")
    fitting = []
    for model in sorted(models, key=lambda m: m["size_bytes"] or 0):
        verdict = _fits(model["size_bytes"], budget_mb)
        if verdict is None:
            suffix = "size unknown, fit UNKNOWN"
        elif verdict:
            suffix = "fits"
            fitting.append(model)
        else:
            suffix = "DOES NOT FIT"
        present = " <- configured" if model["name"].split(":")[0] == configured.split(":")[0] else ""
        lines.append(
            f"  {model['name']} ({_fmt_size(model['size_bytes'])}, "
            f"{model['family'] or 'unknown family'}) - {suffix}{present}"
        )

    chosen_present = any(
        m["name"].split(":")[0] == configured.split(":")[0] for m in models
    )
    if not chosen_present:
        lines.append(
            f"WARNING: the tier chose {configured}, which is not among the "
            f"installed models ({', '.join(sorted(m['name'] for m in models))})."
        )
    elif fitting:
        largest = max(fitting, key=lambda m: m["size_bytes"] or 0)
        if largest["name"] != configured:
            lines.append(
                f"note: {largest['name']} ({_fmt_size(largest['size_bytes'])}) "
                f"also fits and is larger than the tier's choice {configured}; "
                "the tier is a fixed table and does not consider headroom."
            )

    return _SENSE.to_percept(
        "\n".join(lines),
        source="ollama-tags",
        metadata={
            "reachable": True,
            "configured": configured,
            "models": len(models),
            "configured_present": chosen_present,
        },
    )


_SENSE = Sense(
    name="modelfit",
    kind=KIND,
    ttl_seconds=_TTL_SECONDS,
    sensitivity=SENSITIVITY,
    schema=_SCHEMA,
    run=_run,
    poll_interval=_POLL_INTERVAL,
)

SENSES = [_SENSE]
