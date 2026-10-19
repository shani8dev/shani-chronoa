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

from shani_chronoa import gguf_header

MB = 1024 * 1024
_KV_BYTES_PER_TOKEN = 40 * 1024      # ~40 kB/token for a mid-size model
_OVERHEAD = 400 * MB                 # compute buffers, cuda context, etc.


def _int(value) -> int:
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


#: Bytes one KV-cache element costs, by llama.cpp cache type. The figures are
#: `unsloth`'s (`studio/backend/core/inference/llama_cpp.py:_kv_bytes_per_elem`),
#: cross-checked against ggml's block sizes: `q8_0` is 34 bytes for 32 values,
#: `q5_1` 0.75, `q4_0`/`iq4_nl` 0.5625. `f16` is 2.0 and is llama.cpp's default,
#: which is what `kv_bytes_per_token` assumed before this table existed.
#:
#: A type not in the table costs the f16 default rather than a guess, so an
#: unknown name is the safe direction: it over-counts the cache rather than
#: promising a context the VRAM cannot hold.
KV_CACHE_BYTES_PER_ELEM: "dict[str, float]" = {
    "f32": 4.0,
    "f16": 2.0,
    "bf16": 2.0,
    "q8_0": 34 / 32,
    "q5_1": 0.75,
    "q5_0": 0.6875,
    "q4_1": 0.625,
    "q4_0": 0.5625,
    "iq4_nl": 0.5625,
}

#: The cache types worth trying, best first. `f16` is llama.cpp's default and the
#: one the whole table assumes; below it, `q8_0` at 0.5625 of f16's bytes is the
#: near-lossless step, and `q4_0` at 0.28125 is the floor - there is nothing
#: cheaper in llama.cpp. `f32` is excluded because it costs more than the default
#: and would only ever be chosen by someone asking for it explicitly.
#:
#: **Every entry here is one the installed binary accepts, measured rather than
#: assumed (2026-10-12).** A type this build did not have would reach
#: `server_args()` as `-ctk <type>`, fail llama-server's argument parser, and take
#: the model down at start - a silent failure whose only symptom is a server that
#: never comes up. Probed against the real `/usr/bin/llama-server`, and the probe
#: has power because a bogus type is refused at argument-parse time
#: (`error while handling argument "--cache-type-k": Unsupported cache type:`),
#: before any model is loaded. All nine llama.cpp types - `f16`, `bf16`, `q8_0`,
#: `q5_1`, `q5_0`, `q4_1`, `q4_0`, `iq4_nl`, `f32` - are accepted by this build,
#: so every entry in the ladder is safe to pass.
#:
#: Not verified, and deliberately not asserted: which of these costs *quality*.
#: `q8_0` is the widely-reported near-lossless step and `q4_0` the visible one,
#: but nobody here has measured it, so nothing in this file refuses to use `q4_0`
#: on that basis. PersonaPlex's RVQ has the control this repo would want and
#: cannot build - `set_num_codebooks` is bounded *below* by the semantic
#: codebooks, so acoustic fidelity can be traded away and meaning never can. The
#: KV ladder has no such floor, because a floor is a quality claim and this one
#: would be unmeasured.
#: **The speed axis, and the hypothesis the measurement overturned.** This note
#: used to say the axis was unmeasured, on the grounds that `llama-bench` - the
#: right tool - refuses to create a context with a quantized cache type on this
#: build (`ctk=f16` rows=1 errors=0, `ctk=q8_0` rows=0 errors=1). It *is*
#: measurable anyway: `llama-server` accepts `-ctk q8_0` and has all along, and
#: `local_llm.effective_bandwidth()` already reads a decode rate off a live
#: server. **The blocked tool was never the only route.**
#:
#: The recorded expectation was that a quantized cache "pays a dequantization
#: cost, so a ladder chosen to fit more context could partly give back in
#: `tok/s`". **Measured, the opposite happens.** Qwen3-1.7B-Q4_K_M, `-t 4` (the
#: thread finding: physical cores), a ~3213-token cache so the KV cache is ~26%
#: of per-token traffic, two interleaved rounds and four samples per type:
#:
#: ====  type    tok/s   vs f16   within-type spread
#:       f16     6.793        -        15.4%
#:       q8_0    8.911   +31.2%        12.8%
#:       q5_0    6.500    -4.3%        14.4%
#:       q4_0   10.045   +47.9%         6.0%
#:
#: And **the control that says why**, which is the half that makes it a finding
#: rather than a number: the same four types at a ~220-token cache, where the KV
#: cache is a rounding error of per-token traffic - q8_0 **-0.09%**, q5_0 -8.8%,
#: q4_0 -2.0%. The advantage is a **cache-traffic effect**: it exists only where
#: the cache is big enough to matter. The baseline itself says so - 13.0 tok/s at
#: a short cache against 6.8 at a long one.
#:
#: Which is also why q5_0 breaks the pattern. A pure bytes argument predicts
#: monotonic improvement with compression, and q5_0 (0.6875 B/elem) shows no
#: advantage over f16 (2.0) in either regime - a per-type dequantization cost
#: that offsets the traffic saving. **Recorded as "not distinguishable from f16",
#: not "slower": -4.3% sits inside its own 14% spread.**
#:
#: So the shipped consequence is unusually clean. `CACHE_TYPE_LADDER` is
#: `("f16", "q8_0")`, and the one compressed rung it offers is **+31% decode rate
#: and half the KV bytes** - the dial wins on quality (measured cheap on five
#: models), on headroom, and on speed at the same time. `q5_0` and `q4_0` are not
#: on the ladder, so their figures are recorded for the next ladder extension and
#: change nothing shipped.
#:
#: **Honest limits:** one model, one context pair, CPU-only, `-t 4`, and the
#: long-context figure carries a 6-15% within-type spread - so the *sign* and the
#: order of magnitude are settled, and the exact percentage is not. The property
#: worth asserting is not "q8_0 is 31% faster" (a number this noisy must not be
#: pinned) but **"the rung the ladder offers is not slower than f16"**, which is
#: what the decision actually turns on.

#: The most compression the KV cache may take. `q4_0` is deliberately absent.
#:
#: Measured 2026-10-12 with this repo's own `llama-perplexity` path and its own
#: fixed corpus, both `-ctk` and `-ctv` set, on **two** models - SmolLM2-135M
#: (98 MiB, two identical runs each) and Qwen3-8B (4.7 GB, one run each).
#: Perplexity, as a percentage of the same model's f16 figure:
#:
#:     type   SmolLM2-135M    Qwen3-8B-Q4_K_M
#:     q8_0      -0.04%          +0.32%
#:     q5_0      +0.11%          +1.35%
#:     q4_0      +6.08%          +5.81%
#:
#: **The ordering is the same on both, and `q4_0` costs ~6% on both.** That is
#: what the floor is set from: a demonstrable cliff, reproduced on a model 50x
#: the size of the first one. `q4_0` is excluded and nothing below `q5_0` is
#: reachable.
#:
#: **What changed when the second model was added:** `q5_0` is *not* free. On
#: SmolLM2 it measured +0.11% (inside the noise); on Qwen3-8B it measures
#: +1.35%, which is not. The first measurement said "q8_0 and q5_0 are free" and
#: that was a small-model artifact. The floor did not move - 1.35% is a measurable
#: cost, not the cliff, and `q5_0` is sometimes the only step that makes a model
#: fit at all - but the claim about it is now honest rather than flattering.
#:
#: Honest limits that remain: perplexity is not what a listener notices, one
#: corpus, and one run per type on the larger model. What is settled is the
#: direction, the order of magnitude, and the *ordering* - the cliff is at `q4_0`
#: and it reproduced.
#: The measurements the floor rests on, as perplexity *percentages* of the same
#: model's f16 figure. Two models, same corpus and method (2026-10-12): one run
#: per type on Qwen3-8B, two identical runs on SmolLM2. The union of what both
#: establish is the floor; either alone would be a single box's accident.
KV_CACHE_MEASUREMENTS: "dict[str, dict[str, float]]" = {
    "smollm2-135m-q4_k_m": {"f16": 0.0, "q8_0": -0.04, "q5_0": 0.11, "q4_0": 6.08},
    "qwen3-0.6b-q8_0": {"f16": 0.0, "q8_0": 0.40, "q5_0": 8.53, "q4_0": 457.58},
    "qwen3-1.7b-q4_k_m": {"f16": 0.0, "q8_0": 0.30, "q5_0": 8.54, "q4_0": 38.9},
    "smolvlm2-500m-video-instruct-q8_0": {"f16": 0.0, "q8_0": 0.13, "q5_0": 0.74, "q4_0": 2.16},
    "qwen3-8b-q4_k_m": {"f16": 0.0, "q8_0": 0.32, "q5_0": 1.35, "q4_0": 5.81},
}

#: **The third model moved this floor, which is why the third model was measured.**
#: Qwen3-0.6B-Q8_0 - smaller than the 8B, larger than the 135M - measures `q5_0` at
#: **+8.53%** and `q4_0` at **+457%**, both reproduced exactly on a confirmation
#: run. So `q5_0` is not "a known cost below the cliff"; on that model it costs
#: more than `q4_0` costs on either of the others. The two-model evidence that put
#: the floor at `q5_0` was luck.
#:
#: `q8_0` is the only step cheap on **all three** (-0.04%, +0.32%, +0.40%), so that
#: is the floor. The rule it now encodes is stronger than "the worst measured
#: step": a cache type is usable only if it is cheap on every model the ladder
#: might be applied to - which is PersonaPlex's `n_q_semantic` bound taken
#: properly, where the semantic set is what the *worst* case allows rather than
#: what the average suggests.
#: Decode rate by cache type, as a percentage against the same model and prompt
#: at f16. Positive is faster. Measured 2026-10-12, Qwen3-1.7B-Q4_K_M (the
#: default tier), `-t 4`, one server at a time, two interleaved rounds with four
#: samples per type, `n_predict 32`, `cache_prompt False` so the figure is
#: generation only and not the ~53 s of prompt evaluation.
#:
#: `long` is a ~3213-token cache, where the KV cache is ~26% of per-token traffic;
#: `short` is a ~220-token cache, where it is a rounding error. **The short
#: column is the control**: q8_0 goes from +31.2% to -0.09%, which is what
#: establishes the effect as cache traffic rather than a property of the
#: quantized kernel. See the note above `KV_CACHE_FLOOR` for the full reasoning.
KV_CACHE_SPEED: "dict[str, dict[str, float]]" = {
    "f16": {"long": 0.0, "short": 0.0},
    "q8_0": {"long": 31.2, "short": -0.09},
    "q5_0": {"long": -4.3, "short": -8.82},
    "q4_0": {"long": 47.9, "short": -1.97},
}

#: The within-type spread measured across the four samples, as a percentage of
#: the median. Recorded because it is what decides which claims are honest: a
#: figure smaller than its own spread is not a finding, which is why q5_0's
#: -4.3% is described as "not distinguishable from f16" rather than "slower".
KV_CACHE_SPEED_SPREAD: "dict[str, float]" = {
    "f16": 15.4, "q8_0": 12.8, "q5_0": 14.4, "q4_0": 6.0,
}
KV_CACHE_FLOOR: str = "q8_0"

#: Best first, and stopping at `KV_CACHE_FLOOR`. See that constant for the
#: measurement that excludes `q4_0`.
CACHE_TYPE_LADDER: "tuple[str, ...]" = ("f16", "q8_0")


def cache_bytes_per_elem(cache_type: Optional[str]) -> float:
    """Bytes one KV element costs in `cache_type`, or the f16 default."""
    return KV_CACHE_BYTES_PER_ELEM.get(
        (cache_type or "f16").strip().lower(), 2.0)


def kv_bytes_per_token(model_info: dict, cache_type: str = "f16") -> int:
    """KV-cache bytes one token of context costs, from GGUF/`/api/show` metadata.

    The flat 40 kB guess is off by 3x either way: a full-attention 8B needs
    ~100 kB a token, while hybrid models (linear attention, sliding window)
    keep a full cache on only a handful of layers. Reading the architecture is
    the difference between a context that fits and one that spills the model
    onto the CPU. Returns 0 when the metadata is missing, so callers fall back
    to the generic estimate.

    `cache_type` defaults to `f16`, llama.cpp's own default, which is what this
    function assumed before the type was a parameter. It matters because the
    cache is one of only two things a context decision can trade away: shrinking
    the context, or compressing the cache that the context is made of.
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
        total += kv * (k_len + v_len)
    # Scaled rather than rewritten, so the cache type is a *lever* and not a
    # second copy of the arithmetic above. `per_elem` is 2.0 for f16, which is
    # llama.cpp's default and is what this function assumed before the type
    # became a parameter.
    return int(total * cache_bytes_per_elem(cache_type))


def kv_width_from_shapes(shapes) -> int:
    """The KV cache's per-layer width, from the projection tensors' own shapes.

    **A second, independent derivation of the number `kv_bytes_per_token`
    computes from metadata.** GGUF stores a linear weight as `[in, out]`, so the
    K and V projections' *second* dimension is the KV width directly: for
    `blk.0.attn_k.weight` on Qwen3-1.7B it is `shape[1] == 1024`, which is
    `head_count_kv (8) x head_dim (128)`.

    Why a second route at all: `kv_bytes_per_token` reads five conventions out of
    the header - `head_count`, `head_count_kv` (which may be a scalar *or* a
    per-layer list), `key_length`/`value_length` as overrides, and
    `full_attention_interval` / `sliding_window_pattern` as layer filters. A test
    written from the same reading as the module agrees with the module whether or
    not the reading is right. The tensor route cannot inherit that: it asks the
    file what the matrices actually are.

    This is edge0's `test_streaming_math.py` discipline - *"the exact path must
    match a plain fp16 reference implementation"* - applied to the one place
    Chronoa does arithmetic on a third-party file format.

    `shapes` is any iterable of `(name, [in, out])` or objects with `.name` /
    `.shape`. Returns 0 when there is nothing to read, so a caller cannot tell
    "no K tensor" from "zero width".
    """
    def _pair(entry):
        if hasattr(entry, "name"):
            name = str(entry.name)
            shape = list(entry.shape)
        else:
            name = str(entry[0])
            shape = list(entry[1])
        return name, shape

    widths = set()
    for entry in shapes or ():
        name, shape = _pair(entry)
        if not name.endswith(("attn_k.weight", "attn_v.weight")):
            continue
        if len(shape) < 2 or not shape[1]:
            continue
        widths.add(int(shape[1]))
    if len(widths) != 1:
        # More than one width across the K/V projections means a fused or
        # grouped attention this reader does not model. Saying so beats guessing
        # which one the cache uses.
        return 0
    return widths.pop()


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


def cache_type_for_context(
    weights_bytes: int,
    context: int,
    *,
    total_bytes: Optional[int] = None,
    per_token_at_f16: Optional[int] = None,
    ladder: "tuple[str, ...]" = CACHE_TYPE_LADDER,
) -> str:
    """The **lightest** cache type that still fits `context`, or the cheapest tried.

    **The second axis a context decision never had.** `context_that_fits()` walks
    a ladder of *contexts* and treats the KV cost as fixed, so the only way to fit
    more context was to shrink it. unsloth treats the same quantity as a budget
    variable - `min(_planned_main_cache_types(...), key=_kv_bytes_per_elem)`,
    *"the KV-byte reserve takes the heavier axis... which is conservative for
    storage but hides a paired quantized axis"* - and picks the smallest cache
    type that fits.

    With `q8_0` at 0.5625 of f16's bytes and `q4_0` at 0.28125, a context that
    does not fit at all in f16 fits in `q8_0` with room to spare, and the context
    is what a person asked for. Measured on Qwen3-8B's shape: 16 KiB/token at f16,
    8.5 KiB at `q8_0`, 4.5 KiB at `q4_0`.

    `ladder` is ordered **best first** - `f16`, then the quantized types - so the
    first type that fits is also the least lossy one that fits, and the walk
    returns it rather than always reaching for the cheapest.

    Returns the cheapest type tried when nothing fits. That is deliberate: the
    caller is already in the "nothing fits" case, and returning `f16` there would
    claim a quality level the numbers do not support.

    `KV_CACHE_FLOOR` clamps the ladder before the walk, so the cheapest tried is
    the floor whenever the ladder reaches it - which `CACHE_TYPE_LADDER` always
    does. A caller passing a *shorter* ladder gets what that ladder holds: the
    floor is a limit on how far down, not a promise that more types exist.
    PersonaPlex's `set_num_codebooks` bound - `n >= self.n_q_semantic` - applied
    to the one compression dial this app has.
    """
    if not per_token_at_f16:
        return ladder[0]
    # Never below the floor, whatever ladder a caller supplies.
    ladder = tuple(ladder[:ladder.index(KV_CACHE_FLOOR) + 1]) \
        if KV_CACHE_FLOOR in ladder else ladder
    f16 = cache_bytes_per_elem(ladder[0])
    for cache_type in ladder:
        scaled = int(per_token_at_f16 * cache_bytes_per_elem(cache_type) / f16)
        if estimate_fit(weights_bytes, context, total_bytes=total_bytes,
                        per_token=scaled).fits:
            return cache_type
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


#: `(cache_key) -> metadata dict`, bounded. Six call sites read the same header
#: inside one startup, and the cost was not the six reads but the 3.5 s each one
#: cost - so the same answer held for every one of them was still paid six times.
#: Bounded because this is process-lifetime state keyed on a path: a machine
#: being pointed at different models (the Models panel's re-quantise writes new
#: files) would otherwise grow a header-sized dict per file for the life of the
#: app. 8 is well past "every model on this machine" and well under "a leak".
_INFO_CACHE_LIMIT = 8
_INFO_CACHE: "dict[tuple, dict]" = {}


def _cached_header(read, path, tag):
    """`read(path)`, memoised on `(tag, gguf_header.cache_key(path))`.

    **The key is the stat, inside the cache, not around it.** `cache_key` is
    `(path, mtime_ns, size)`: mtime alone misses an in-place rewrite that keeps
    its timestamp, and size alone misses a rewrite that lands on the same length
    - and both are how a header cache hands back the *previous* model's numbers
    while `current.gguf` points somewhere else. A path that cannot be stat'd
    yields no key, so it simply misses rather than being cached against nothing.

    **`tag` is required, and it is the prefix this function's docstring used to
    claim it had.** Metadata and tensor shapes are two different reads of one
    file, keyed by the same `(path, mtime, size)`, and with no prefix the first
    to fill an entry served the second one its own answer:
    `read_tensor_shapes` handed back the metadata dict, so `kv_width_disagreement`
    sorted a list of `(key, value)` pairs into `kv_width_from_shapes` and raised
    `TypeError: 'int' object is not iterable` on `("qwen3.block_count", 28)`.
    `local_llm._context_arg` wraps that call in `except Exception`, so the
    cross-check **raised and was swallowed on every startup** - a check that
    passes by not running, which is the failure this wiring was built to detect.

    Required rather than inferred from `read`, because `read` is a callable: a
    caller who forgets to pass one gets a silent collision instead of a
    `TypeError`, and a silent collision is what happened here.
    """
    key = gguf_header.cache_key(path)
    if key is None:
        return read(path)
    key = (tag,) + key
    hit = _INFO_CACHE.get(key)
    if hit is not None:
        return hit
    value = read(path)
    if len(_INFO_CACHE) >= _INFO_CACHE_LIMIT:
        # Drop the oldest *inserted*, not an arbitrary one: dicts keep order, so
        # this is the cheapest honest eviction and it is the one whose answer is
        # "the file least recently asked about".
        _INFO_CACHE.pop(next(iter(_INFO_CACHE)))
    _INFO_CACHE[key] = value
    return value


def gguf_model_info(path) -> Optional[dict]:
    """The live model's GGUF header as an `/api/show`-shaped dict, or None.

    **Read from the first few MB of the file, and cached, where it used to walk
    the whole thing through `gguf.GGUFReader`.** That reader's `__init__` opens
    with `np.memmap(path)`, so it maps the entire checkpoint - 1,107,409,472
    bytes for the model measured here - and `GGUFReader(path)` on its own costs
    **3.5 s**, with a field walk on top taking 7.7-9.1 s. Six call sites did it
    during one startup (`local_llm._context_arg`, `_cpu_context_arg`,
    `bytes_per_token`, `model_registry.scan`, `model_registry.active_entry`,
    `describe`), and `model_registry.active_entry` runs on the GTK main thread,
    which is the whole reason the window froze.

    Two changes, measured separately:

    - **the read**: `gguf_header.read_metadata` parses the header directly and
      reads only the first `HEADER_BYTES`, taking **0.127 s** for all 32 keys -
      a **27x** saving against `GGUFReader` alone. It has no `numpy` and no
      `gguf` import, which also matters because `gguf` is packaged by no
      ecosystem manifest and is therefore absent on a real install.
    - **the cache**: the six reads become one, so a startup pays 0.127 s rather
      than 6 x 3.5 s.

    **The values are better, not merely cheaper.** `GGUFReader`'s
    `ReaderField.parts[-1]` is a string field's *bytes* and an array field's
    *last element*, so the old dict could not express a per-layer
    `head_count_kv` at all - which means `metadata_kv_width`'s refusal of a list
    could never fire. Arrays decode to **lists** now, and that refusal is
    reachable for the first time. `metadata_kv_width` is unchanged by this and
    is covered in both directions by `tests/test_ctxfit.py`.
    """
    return _cached_header(gguf_header.read_metadata, path, "metadata") or None


def metadata_kv_width(model_info: dict) -> Optional[int]:
    """`head_count_kv x head_dim`, the width `kv_bytes_per_token` works from.

    Kept beside `kv_width_from_shapes` so the two halves of the comparison are
    read together. `head_count_kv` may be a scalar or a per-layer **list**; a
    list means the widths differ per layer, which is the case
    `kv_width_from_shapes` refuses (it returns 0) - so this returns None rather
    than picking one layer's value and pretending it is the model's.
    """
    info = model_info or {}
    arch = str(info.get("general.architecture") or "")
    if not arch:
        return None
    kv_heads = info.get(f"{arch}.attention.head_count_kv")
    if isinstance(kv_heads, (list, tuple)):
        return None
    heads = _int(info.get(f"{arch}.attention.head_count"))
    embedding = _int(info.get(f"{arch}.embedding_length"))
    kv = _int(kv_heads)
    if not (heads and embedding and kv):
        return None
    return kv * (embedding // heads)


def kv_width_disagreement_from_shapes(shapes, model_info: dict) -> Optional[str]:
    """The comparison itself, over shapes a caller already holds.

    Split from `kv_width_disagreement` for the same reason `_read_tail` takes a
    path and `speech_gate.wait` takes a clock: the interesting part is a function
    of data a test can supply exactly, and a run against a real GGUF is a second,
    narrower check that the file-reading half works.
    """
    truth = metadata_kv_width(model_info)
    if truth is None:
        return None
    read = kv_width_from_shapes(shapes or ())
    if not read:
        return None
    if read == truth:
        return None
    return (f"the GGUF header says the KV width is {truth} elements but the "
            f"K/V projection tensors are {read} wide; one of the two readings is "
            f"wrong, and the header is the one being used")


def kv_width_disagreement(path, model_info: dict) -> Optional[str]:
    """A sentence naming the two widths, when the file contradicts the header.

    **The comparison `kv_width_from_shapes` was written for and never got.** The
    function is documented, tested, and had no caller in `usr/` at all - a second
    derivation of one quantity that nothing ever compared against the first,
    which is a cross-check nobody performs.

    It is wired in `local_llm.context_fit()` rather than inside
    `kv_bytes_per_token` because that function takes a dict, not a path: only a
    caller holding the file can read the tensors.

    Returns None when the comparison cannot be made - no usable metadata, no
    readable tensors, a per-layer `head_count_kv`, or fused/grouped attention the
    shape reader refuses. None means *no comparison*, which is different from
    *no disagreement*: a caller must not read a None as agreement.

    A disagreement is reported and **not acted on**. The metadata stays the
    authority for the context decision because it is the only route that knows
    about shared caches and hybrid layer patterns, and silently switching to the
    shapes on the strength of one file would trade a visible wrong number for an
    invisible right one. What changes is that the wrong number is now *sayable*.
    """
    truth = metadata_kv_width(model_info)
    if truth is None or not path:
        return None
    shapes = _cached_header(gguf_header.read_tensor_shapes, path, "shapes")
    if not shapes:
        return None
    return kv_width_disagreement_from_shapes(
        sorted(shapes.items()), model_info)


def expert_bytes_per_token(model_info: dict, file_bytes: int) -> Optional[int]:
    """What one generated token reads for a mixture-of-experts, from the header.

    **Why a second route exists at all.** `local_llm.REQUIRES` carries
    `active_bytes` as a *hardcoded* number for the one MoE tier, marked
    `measured: False`, and `requirements_met()` refuses it as a default on exactly
    that basis - *"a number nobody measured is the confident wrong answer this repo
    keeps recording"*. So a Qwen3-30B-A3B cannot be the default on any machine, and
    there is no way for it to become one, because **nothing reads the file to find
    out.** `bytes_per_token()` returns the declared constant, or the file size for
    a dense model, and nothing in between.

    The header says it. A MoE checkpoint carries its own routing topology, and
    these are gguf-py's own field names (`gguf/constants.py`), not ones remembered
    from a model card:

        {arch}.expert_count                 total experts per layer
        {arch}.expert_used_count            experts routed per token
        {arch}.expert_feed_forward_length   each expert's FFN width
        {arch}.expert_shared_feed_forward_length   the always-on expert, if any
        {arch}.feed_forward_length          the dense-layer FFN width
        {arch}.leading_dense_block_count    layers before the MoE ones start

    Per generated token the weights touched are, per layer, the attention
    projections plus **only the routed experts** - so the arithmetic is
    `used x 3 x ffn x embedding` where a dense model would be the whole file. That
    is the whole reason a 30B can decode like a 3B, and it is exactly the number
    `bytes_per_token()` needs and currently hardcodes.

    `bytes_per_weight` is taken from the file, not assumed: the caller passes
    `file_bytes` and the parameter count this derives, so the ratio is the file's
    own quantization rather than a table's. It is a mean over the *dense* parameter
    count of the same shape, which is the one part of the model the header does
    describe exactly.

    Returns None when the header is not a MoE, or is missing any field the
    arithmetic needs - which means *not a MoE* rather than *a MoE of unknown size*,
    and the caller falls back to what it had.
    """
    info = model_info or {}
    arch = str(info.get("general.architecture") or "")
    if not arch:
        return None

    def key(name: str):
        return info.get(f"{arch}.{name}")

    layers = _int(key("block_count"))
    embedding = _int(key("embedding_length"))
    used = _int(key("expert_used_count"))
    expert_ffn = _int(key("expert_feed_forward_length"))
    if not (layers and embedding and used and expert_ffn):
        return None

    dense_ffn = _int(key("feed_forward_length"))
    shared_ffn = _int(key("expert_shared_feed_forward_length"))
    leading_dense = min(_int(key("leading_dense_block_count")), layers)

    # Attention projections per layer: Q, K, V and O, each embedding x embedding.
    # K and V are smaller than Q and O under grouped attention, but the header's
    # head counts decide that and an over-estimate here is the safe direction - it
    # makes the tier look slower, never faster.
    attention = 4 * embedding * embedding

    moe_layers = layers - leading_dense
    if moe_layers <= 0:
        return None                      # all-dense: not the case this is for

    # SwiGLU has three matrices per expert (gate, up, down).
    routed = used * 3 * expert_ffn * embedding
    shared = 3 * shared_ffn * embedding if shared_ffn else 0
    dense_layer = attention + (3 * dense_ffn * embedding if dense_ffn else 0)

    # **The file stores every expert, and the per-weight cost has to be paid
    # against all of them.** The first version of this divided the file by the
    # *active* parameter count, which undercounts by the expert ratio - twelve-fold
    # for Qwen3-30B-A3B - so the derived figure came out 8x too large and the
    # cross-check below caught it. Two counts, then: everything the file holds
    # (which sets the bytes-per-weight), and what one token actually touches.
    total_experts = _int(key("expert_count"))
    # Without the total expert count there is no stored count, so there is no
    # bytes-per-weight to pay the active share against - and guessing one would
    # put a made-up denominator under a number the tier's default depends on.
    # Returning None means *cannot derive*, which is the caller's fallback case.
    if not total_experts or total_experts < used:
        return None                      # nonsense routing: not a reading
    stored = total_experts * 3 * expert_ffn * embedding + shared
    total_params = leading_dense * dense_layer + moe_layers * (attention + stored)
    active_params = leading_dense * dense_layer + moe_layers * (attention + routed + shared)
    if total_params <= 0 or file_bytes <= 0:
        return None
    # Bytes per weight, from the file against the parameters it actually holds.
    # This is the file's own mean quantization, which is the honest figure for a
    # Q4_K_M and does not need the type name decoded.
    per_weight = file_bytes / float(total_params)
    return int(active_params * per_weight)
