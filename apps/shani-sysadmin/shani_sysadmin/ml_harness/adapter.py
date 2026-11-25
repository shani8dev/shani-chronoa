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
from typing import Dict, List, Optional

from shani_chronoa import gguf_header


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
    """Every header key, each value rendered as text.

    **Was `ReaderField.parts[1]` and `parts[-1]`, and is now one parse.**
    `GGUFReader.fields` carries a string field as five parts - the type-table
    offset, the KEY bytes, the value-type index, the length, the VALUE bytes -
    and a reader that guesses which part is the value reads `general.type` as
    `'97'`, which is ASCII for the first letter of `adapter`. Every branch in the
    version this replaced existed to undo one such guess: `tolist()` on a uint8
    memmap yields *ints*, so an `isinstance(raw, str)` check after it never
    fires; and a u32 field is also a list of ints, so `bytes()` on it raises
    `ValueError: byte must be in range(0, 256)`, which is the crash that stopped
    `is_adapter()` running on a real model at all.

    `gguf_header.read_metadata` parses the file rather than decoding numpy views,
    so a string arrives as a `str` because the format says it is one and there is
    no `parts` to misread - **and the same call costs 0.127 s instead of 3.5 s**,
    which is the difference between `is_adapter()` being usable on a scan of a
    model directory and not.

    **Values are still `str(...)`, and that is now a stated contract rather than
    a decoding accident** - `general.quantization_version` is a u32 and reads
    `"4294967295"`, which is what `tests/test_adapter.py` pins. The one shape
    difference from the old route is an **array** value: it used to arrive as its
    last element (`"8"` for `[8, 4]`) and is now the whole list (`"[8, 4]")`,
    because `parts[-1]` was never the array. Nothing in the tree reads one, and
    a dict that cannot express a list is the reason this module needed guessing.

    An unreadable file is `{}`, which is what every caller here treats as "this
    file does not declare itself": `is_adapter()` answers False rather than
    raising, because a foreign file is not an adapter and is not a fault.
    """
    return {key: str(value)
            for key, value in gguf_header.read_metadata(path).items()}


def mismatches(adapter: Path, base: Path) -> List[str]:
    """Every reason this adapter does not fit this base, in words.

    Empty means compatible. Never raises for a *mismatch* - the mismatch is the
    answer - and raises only when a file cannot be read at all, because "I could
    not tell" and "they fit" must not be the same value.
    """
    # **Guarded, because `gguf` is not installed on every machine.** It is not
    # declared as a dependency and is packaged nowhere in this ecosystem, so an
    # unguarded import here loads this module fine and raises at call time on any
    # real install. `ctxfit.py` carries the same reader behind a `try:` and
    # degrades; this one cannot degrade, because an adapter that cannot be read
    # is not a mismatch *or* a fit - so it says which it is.
    try:
        from gguf import GGUFReader
    except ImportError:
        raise AdapterMismatch(
            "the `gguf` package is not installed, so an adapter cannot be read "
            "here; it is not declared as a dependency and ships in no package "
            "this ecosystem builds. **I could not tell whether they fit**, which "
            "is not the same answer as 'they fit'.")

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
        fan_in = int(tensor.shape[0])
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
