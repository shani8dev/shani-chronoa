"""渡劫 Ascension: a LoRA adapter applied *alongside* the base.

The merge path rewrites the whole container. This does not - the base is
untouched and the adapter is a separate file that is loaded, scaled, or dropped
at runtime. That is why it is the stage worth building first: a merge that goes
wrong costs you the model, and an adapter that goes wrong costs you a file you
delete.

**The check this module exists for is base compatibility, and it exists because
`llama-server` does not do it before it crashes.** Measured: pointing the
1.7B-era adapter at the 0.6B base produces

    llama_adapter_lora_init: failed to apply lora adapter:
    tensor 'blk.0.attn_k.weight' has incorrect shape
    (hint: maybe wrong base model?)

followed by a **segfault** of the whole server process, on a box where two
other llama-servers are already running. Nothing in that sequence is a message a
person can act on: the hint is the only useful line, it arrives after the
crash's predecessor, and the exit is a signal rather than a refusal.

**Header metadata cannot answer this.** The adapter declares
`general.size_label = 0.6B`, `block_count = 28`, `embedding_length = 1024` and
`feed_forward_length = 3072` - every one of which **matches the 0.6B base
exactly**, because Qwen3 checkpoints in the 1.7B and 0.6B families share those
numbers. Only `general.name` and `general.tags` disagree, and those are free-text
provenance strings that publishers get wrong: this adapter's `general.name` says
`Qwen/Qwen3-1.7B` while its own `size_label` says `0.6B`, so the file
contradicts itself before it is compared with anything.

**The per-tensor shapes are the only reliable check, and it is cheap.** Every
`lora_a` tensor is `[rank, in_features]` and the base weight it targets is
`[out, in]`; the fan-in has to agree. Measured on that same mismatched pair:
**196 of 196 `lora_a` tensors disagree**, in four seconds, before any server is
started.
"""
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import numpy as np

from . import modelfmt

#: The metadata keys that look like they decide compatibility and do not.
UNRELIABLE_KEYS = ("general.name", "general.tags", "general.finetune",
                   "general.basename")

ADAPTER_MAGIC = b"GGUF"


class AdapterMismatch(Exception):
    """The adapter does not fit this base. Never raised for anything else."""


def is_adapter(path: Path) -> bool:
    """Whether the file is a LoRA/LoRA-style adapter rather than a model.

    **`general.type == "adapter"`, not the file's size or its name.** Measured:
    `mradermacher/Qwen3-0.6B-climateguard-lora-16-GGUF` publishes files whose
    names end `-lora-16` at **1142.7 MiB in f16 and 365.5 MiB in Q4_K_S** - the
    size of a whole 0.6B model, because it is one. It has no `lora_a`/`lora_b`
    tensors at all. A name or a size test would have called that an adapter and
    handed `llama-server` a second full model.
    """
    info = _header(path)
    return info.get("general.type") == "adapter"


def _header(path: Path) -> Dict[str, str]:
    """The adapter's own strings, read without the reader's numeric decoding."""
    try:
        from gguf import GGUFReader
        fields = GGUFReader(str(path)).fields
    except Exception:                                          # noqa: BLE001
        return {}
    out: Dict[str, str] = {}
    for key, field in fields.items():
        parts = getattr(field, "parts", None)
        # **The shape of `ReaderField.parts` is not documented and guessing it
        # cost this module its whole first run.** Measured on the installed
        # gguf-py: a string field arrives as FIVE parts -
        #   [0] the type table offset, [1] the KEY bytes, [2] the value-type
        #   index, [3] the length, [4] the VALUE bytes.
        # The first version read `parts[-1]` as the value and `parts[1]` as the
        # key, so every string came back as the digits of an offset -
        # `general.type` read as `'97'`, `general.name` as `'81'` - and
        # `is_adapter()` returned False on a file that declares itself an
        # adapter. The key is `parts[1]` and the value is `parts[-1]`, and a
        # reader that guesses reports a confident wrong answer rather than
        # failing.
        if parts is None or len(parts) < 2:
            continue
        key_bytes = parts[1]
        try:
            key_bytes = bytes(key_bytes).decode("utf-8", "replace")
        except Exception:                                      # noqa: BLE001
            continue
        if key_bytes != key:
            continue
        raw = parts[-1]
        # **`tolist()` on the byte memmap yields a list of INTS**, not a string,
        # so a `isinstance(raw, str)` check after it never fires and `str(raw)`
        # prints the ASCII of the first byte. That is how `general.type` came
        # back as `'97'`: 97 is ASCII for `a`, the first letter of `adapter`.
        # Decode the bytes BEFORE touching the numeric form.
        if isinstance(raw, (bytes, bytearray)):
            out[key] = bytes(raw).decode("utf-8", "replace")
            continue
        if hasattr(raw, "dtype") and raw.dtype == np.uint8:
            out[key] = bytes(np.asarray(raw)).decode("utf-8", "replace")
            continue
        try:
            raw = raw.tolist()
        except Exception:                                      # noqa: BLE001
            pass
        if isinstance(raw, list) and raw and isinstance(raw[0], int):
            # **Only a byte field decodes as text.** A u32 field is also a list
            # of ints, and `bytes()` on it raises `ValueError: byte must be in
            # range(0, 256)` - which is the crash that stopped `is_adapter()`
            # running on a real model. Anything outside 0..255 is a number.
            if all(isinstance(v, int) and 0 <= v <= 255 for v in raw):
                out[key] = bytes(bytearray(raw)).decode("utf-8", "replace")
            else:
                out[key] = str(raw[0])
        elif isinstance(raw, list) and raw and isinstance(raw[0], (bytes, bytearray)):
            out[key] = ",".join(bytes(v).decode("utf-8", "replace") for v in raw)
        elif isinstance(raw, list) and raw:
            out[key] = str(raw[0])
        else:
            out[key] = str(raw)
    return out


def mismatches(adapter: Path, base: Path) -> List[str]:
    """Every reason this adapter does not fit this base, in words.

    Empty means compatible. Never raises for a *mismatch* - the mismatch is the
    answer - and raises only when a file cannot be read at all, because "I could
    not tell" and "they fit" must not be the same value.
    """
    from gguf import GGUFReader

    if Path(adapter).read_bytes()[:4] != ADAPTER_MAGIC:
        raise AdapterMismatch(
            f"{Path(adapter).name} is not a GGUF file - it may be a "
            "safetensors adapter, which needs a converter this repository does "
            "not ship (no torch, no safetensors, no convert_lora_to_gguf.py)")

    base_shapes = {t.name: tuple(int(s) for s in t.shape)
                   for t in GGUFReader(str(base)).tensors}
    adapter_tensors = GGUFReader(str(adapter)).tensors

    lows = [t for t in adapter_tensors if t.name.endswith(".lora_a")]
    if not lows:
        raise AdapterMismatch(
            f"{Path(adapter).name} declares general.type=adapter but carries no "
            "lora_a tensors. A published adapter that is really a whole model "
            "with -lora in its filename looks exactly like this.")

    reasons: List[str] = []
    unknown, wrong = [], []
    for tensor in lows:
        target = base_shapes.get(tensor.name[:-len(".lora_a")])
        if target is None:
            unknown.append(tensor.name[:-len(".lora_a")])
            continue
        # **The reader's first axis is ggml's `ne0`, and for both tensors that
        # is in-features** - measured against the real files rather than
        # reasoned: `lora_a` reads `(6144, 16)` where the base weight it targets
        # reads `(3072, 1024)`, and 6144 against 3072 is exactly the 1.7B-against-
        # 0.6B mismatch llama.cpp reports.
        #
        # **This axis was changed once and had to be changed back.** A synthetic
        # fixture built with `GGUFWriter` suggested the last axis was the
        # in-features, because that writer takes a numpy shape and reverses it -
        # so the fixture and the real files disagree about what the reader
        # reports, and the synthetic one was believed over the real pair. The
        # fixture is what is wrong: it is built by a different tool than the one
        # that produced the files this module exists to check.
        fan_in, rank = int(tensor.shape[0]), int(tensor.shape[1])
        if target[0] != fan_in:
            wrong.append((tensor.name[:-len(".lora_a")], fan_in, target[0]))
    if unknown:
        reasons.append("%d target tensors are not in the base (%s)"
                       % (len(unknown), ", ".join(sorted(unknown)[:3])))
    if wrong:
        first = wrong[0]
        reasons.append(
            "%d of %d lora_a tensors have a fan-in the base does not have - "
            "e.g. %s wants %d inputs and the base's weight is %d wide. These "
            "are different models, not different quantisations."
            % (len(wrong), len(lows), first[0], first[1], first[2]))
    return reasons


def check(adapter: Path, base: Path) -> Optional[str]:
    """`None` when it fits, otherwise the sentence explaining why not."""
    problems = mismatches(adapter, base)
    return problems[0] if problems else None


def server_args(adapter: Path, base: Path, scale: float = 1.0) -> List[str]:
    """The argv for a server that has already been checked.

    **Checked here rather than left to `llama-server`**, which crashes on a
    mismatch. `lora_scaled()` is the form used because a scale below 1.0 is how
    an adapter is turned down rather than off, and `--lora-init-without-apply`
    is deliberately not used: loading it silently and applying nothing would
    score the base and call it the adapter.
    """
    if scale <= 0.0:
        raise ValueError(
            "scale %r: drop the adapter from the argv instead of scaling it to "
            "zero, so a file that is not loading is visible in the command line"
            % scale)
    return ["--lora-scaled", "%s:%g" % (adapter, scale)]
