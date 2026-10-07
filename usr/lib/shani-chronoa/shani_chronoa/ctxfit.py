"""Context sizing that respects what the model can actually carry.

Ported from Maze-AI's `maze_ai/llm/hardware.py`: the KV cache grows linearly
with the context and lives beside the weights, so an over-large ``-c`` is what
pushes a model off the GPU and makes it metres times slower - an Ollama server
quietly spills; llama-server OOM-kills. Instead of a hardcoded window, the
command line for this machine is chosen from (a) the weights on disk, (b) the
model's own KV-cache cost per token read from its GGUF header, and (c) the
total GPU memory the kernel exposes.

Conservative by construction: when any input is missing or unknown the caller
keeps the old ``-c 8192`` rather than guessing.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Optional

MB = 1024 * 1024
_KV_BYTES_PER_TOKEN = 40 * 1024      # ~40 kB/token for a mid-size model
_OVERHEAD = 400 * MB                 # compute buffers, cuda context, etc.


def _int(value) -> int:
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


def kv_bytes_per_token(model_info: dict) -> int:
    """KV-cache bytes one token of context costs, from GGUF/`/api/show` metadata.

    The flat 40 kB guess is off by 3x either way: a full-attention 8B needs
    ~100 kB a token, while hybrid models (linear attention, sliding window)
    keep a full cache on only a handful of layers. Reading the architecture is
    the difference between a context that fits and one that spills the model
    onto the CPU. Assumes llama.cpp's default f16 cache, which errs on the safe
    side if the server quantises it. Returns 0 when the metadata is missing,
    so callers fall back to the generic estimate.
    """
    info = model_info or {}
    arch = str(info.get("general.architecture") or "")
    if not arch:
        return 0

    def key(name: str):
        return info.get(f"{arch}.{name}")

    layers = _int(key("block_count"))
    heads = _int(key("attention.head_count"))
    kv_heads_raw = key("attention.head_count_kv")
    embedding = _int(key("embedding_length"))
    if not layers:
        return 0
    head_dim = (embedding // heads) if heads and embedding else 0
    k_len = _int(key("attention.key_length")) or head_dim
    v_len = _int(key("attention.value_length")) or head_dim
    if not (k_len and v_len):
        return 0

    # Layers that keep their own full-length cache.
    own = layers - _int(key("attention.shared_kv_layers"))
    full_layers = list(range(max(0, own)))
    interval = _int(key("full_attention_interval"))
    if interval > 1:
        # Hybrid recurrent models: one attention layer every `interval`; the
        # rest carry a fixed-size state that does not grow with the context.
        full_layers = [i for i in full_layers if (i + 1) % interval == 0]
    pattern = key("attention.sliding_window_pattern")
    if isinstance(pattern, list) and pattern and key("attention.sliding_window"):
        # True marks a sliding-window layer, whose cache is capped at the
        # window and so does not grow with the context either.
        full_layers = [i for i in full_layers if i < len(pattern) and not pattern[i]]

    if isinstance(kv_heads_raw, list):
        kv_per_layer = [_int(v) for v in kv_heads_raw]
    else:
        kv_per_layer = [_int(kv_heads_raw) or heads] * layers
    total = 0
    for i in full_layers:
        kv = kv_per_layer[i] if i < len(kv_per_layer) else (kv_per_layer[-1] if kv_per_layer else 0)
        total += kv * (k_len + v_len) * 2      # f16: two bytes per value
    return total


def estimate_need(weights_bytes: int, context: int, per_token: Optional[int] = None) -> int:
    """Roughly how much memory a model needs at a given context size."""
    per_token = per_token or _KV_BYTES_PER_TOKEN
    return int(weights_bytes) + max(0, int(context)) * per_token + _OVERHEAD


@dataclass
class FitReport:
    """Whether a model is expected to run on the GPU, and why."""

    verdict: str            # "gpu" | "tight" | "spill" | "cpu" | "unknown"
    need_bytes: int
    free_bytes: int
    total_bytes: int

    @property
    def fits(self) -> bool:
        return self.verdict in ("gpu", "tight")


def estimate_fit(
    weights_bytes: int,
    context: int,
    *,
    free_bytes: Optional[int] = None,
    total_bytes: Optional[int] = None,
    per_token: Optional[int] = None,
) -> FitReport:
    """Predict where a model will run, before paying to load it.

    Judged against total VRAM rather than what is free right now: llama-server
    owns the whole window once started, so "free" is misleading the moment a
    model is resident.
    """
    total = total_vram_bytes() if total_bytes is None else total_bytes
    free = free_vram_bytes() if free_bytes is None else free_bytes
    need = estimate_need(weights_bytes, context, per_token)
    if not total:
        return FitReport("cpu", need, free, total)
    if not weights_bytes:
        return FitReport("unknown", need, free, total)
    if need <= total * 0.90:
        return FitReport("gpu", need, free, total)
    if need <= total:
        return FitReport("tight", need, free, total)
    if weights_bytes <= total * 0.95:
        return FitReport("spill", need, free, total)
    return FitReport("cpu", need, free, total)


def context_that_fits(
    weights_bytes: int,
    ladder: tuple = (32768, 16384, 8192, 4096, 2048),
    *,
    total_bytes: Optional[int] = None,
    per_token: Optional[int] = None,
) -> int:
    """The largest context from ``ladder`` expected to stay on the GPU."""
    for context in ladder:
        if estimate_fit(weights_bytes, context, total_bytes=total_bytes,
                        per_token=per_token).fits:
            return context
    return ladder[-1]


def total_vram_bytes() -> Optional[int]:
    """Total GPU memory, best available source. None when not knowable."""
    import shutil as _shutil
    import subprocess as _sub

    smi = _shutil.which("nvidia-smi")
    if smi:
        try:
            out = _sub.run(
                [smi, "--query-gpu=memory.total", "--format=csv,noheader,nounits"],
                capture_output=True, text=True, timeout=10, check=False,
            ).stdout.strip()
            parts = [int(line.strip()) for line in out.splitlines() if line.strip().isdigit()]
            if parts:
                return parts[0] * MB
        except (OSError, ValueError, _sub.TimeoutExpired):
            pass
    for sysfs in Path("/sys/class/drm").glob("card*/device/mem_info_vram_total"):
        try:
            return int(sysfs.read_text().strip())
        except (OSError, ValueError):
            continue
    return None


def free_vram_bytes() -> Optional[int]:
    """Free GPU memory right now, with the same caveats as `total_vram_bytes`."""
    import shutil as _shutil
    import subprocess as _sub

    smi = _shutil.which("nvidia-smi")
    if smi:
        try:
            out = _sub.run(
                [smi, "--query-gpu=memory.free", "--format=csv,noheader,nounits"],
                capture_output=True, text=True, timeout=10, check=False,
            ).stdout.strip()
            parts = [int(line.strip()) for line in out.splitlines() if line.strip().isdigit()]
            if parts:
                return parts[0] * MB
        except (OSError, ValueError, _sub.TimeoutExpired):
            pass
    for sysfs in Path("/sys/class/drm").glob("card*/device/mem_info_vram_free"):
        try:
            return int(sysfs.read_text().strip())
        except (OSError, ValueError):
            continue
    return None


def gguf_model_info(path) -> Optional[dict]:
    """The live model's GGUF header as an `/api/show`-shaped dict, or None.

    The `gguf` reader is local to this call - no server, no network - which is
    why this runs on every `server_args` refresh rather than once.
    """
    try:
        from gguf import GGUFReader

        reader = GGUFReader(str(path))
        out = {}
        for name, field in reader.fields.items():
            try:
                value = field.parts[-1]
                if hasattr(value, "tolist"):
                    value = value.tolist()
                # GGUF strings arrive as uint8 byte arrays (`general.architecture`
                # comes back as memmap([113, 119, 101, 110, 51])); scalar numbers
                # as one-element lists (memmap([28]) -> [28]). Flatten both to
                # the shape the existence check in kv_bytes_per_token expects.
                if isinstance(value, list) and value:
                    if all(isinstance(b, int) and 32 <= b < 127 for b in value):
                        value = bytes(value).decode("ascii", "replace")
                    elif len(value) == 1 and isinstance(value[0], (int, float)):
                        value = value[0]
                out[name] = value
            except (AttributeError, IndexError, TypeError):
                continue
        return out or None
    except Exception:  # noqa: BLE001 - a corrupt or foreign file is not fatal here
        return None
