"""What a model file **is**, and what it is **for** - two separate questions.

**Measured on this box, the product runs five model formats across six roles, and
GGUF is one of them.** Read from the actual call sites, not assumed:

| role | format | how the subsystem opens it |
|---|---|---|
| LLM | GGUF | `llama-server` via the `current.gguf` symlink |
| embedding | GGUF | `llama-server`'s embedding endpoint (`nomic-embed-text`) |
| vision | GGUF **+ `mmproj-` sidecar** | `llama-server --mmproj` |
| speech-to-text | **legacy ggml `.bin`**, magic `6c 6d 67 67` | `ggml-base-q5_1.bin` |
| text-to-speech | **ONNX** | Piper `en_US-*.onnx`, Kokoro `model.int8.onnx` |
| voice identity | **raw blob, not a model** | Kokoro `voices.bin`, 5.5 MB of float32 |
| wake word | **ONNX** | Silero `model_quantized.onnx` |

`current.gguf` answers **one of seven questions**, so "which model?" has no single
answer in a product with an LLM, an embedder, a projector, an STT model, two TTS
voices and a VAD.

**Format is read from the bytes; the extension is a claim and the magic is a
fact.** `voices.bin` is why: it is not a model at all, and nothing in `.bin` says
so.

**Role comes from the binding.** A file's magic cannot say whether
`model_quantized.onnx` is a wake-word detector or a segmenter - only the
subsystem that opens it knows. So a file with no magic can still be correctly
placed, and `voices.bin` is reported as `unknown` format while still being known
to be a voice set.

**Structure is decided in exactly one place.** `kind_of` is the single source of
truth for "is this file a whole model, a sidecar, an adapter, or shard 3 of 5",
and `model_registry` calls *this* function rather than keeping its own copy. That
duplication was written and immediately contradicted itself: a `*.gguf -> llm`
glob here labelled `mmproj-SmolVLM2.gguf` an LLM, while `model_registry`
excluded it as a sidecar. **Two modules answering one question is the
two-widgets-that-disagree failure this repo keeps recording**, and the fix is to
delete the second answer, not to reconcile the two.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Dict, List, Optional, Tuple

#: Roles. A file has at most one, declared by whoever opens it.
ROLES = ("llm", "embed", "vision", "stt", "tts", "vad", "voice-set")

# --- identification ---------------------------------------------------------

#: `format -> (magic, what those bytes are)`. Every entry was read off a real file
#: in this workspace rather than remembered.
_MAGICS: Tuple[Tuple[str, bytes, str], ...] = (
    ("gguf", b"GGUF", "GGUF header (v3 adds big-endian, v2 widened counts to "
                      "uint64)"),
    # whisper.cpp's legacy ggml container. `6c 6d 67 67` is 0x67676d6c read
    # little-endian - the pre-GGUF magic, and the reason `ggml-base-q5_1.bin` is
    # NOT a GGUF despite living beside them and being 57 MB of weights.
    ("ggml-legacy", b"\x6c\x6d\x67\x67", "legacy ggml container (whisper.cpp)"),
)

#: ONNX is protobuf with **no magic**, so it is recognised by its own structure:
#: field 1 is `ir_version` (tag `0x08`), field 2 is a length-delimited
#: `producer_name`, so the bytes at offset 3 spell it out. Verified on all four
#: `.onnx` files here: `08 06 12 0d 'onnxruntime'` and `08 08 12 07 'python'`.
#: Protobuf alone would match any `.bin` full of varints, so the producer must be
#: one actually observed.
_ONNX_PRODUCERS = (b"onnxruntime", b"onnx", b"pytorch", b"python", b"torch")

_SAFETENSORS_MAX_HEADER = 100_000_000

#: Sidecars and types, verbatim from the GGUF naming convention
#: (`ggml/docs/gguf.md`, fetched 2026-10-12). Kept as data with their meanings
#: because the reason is half the answer: a person told "excluded, it is a
#: projector for a base model" learns something; one told nothing learns the file
#: is junk.
_SIDECARS = {
    "mmproj": "a multimedia projector loaded alongside a base LLM, not one itself",
    "mtp": "multi-token-prediction heads for speculative decoding, loaded "
           "alongside a base model",
}
_TYPE_SUFFIXES = {
    "LoRA": "an adapter; the spec says executors must not run it by itself",
    "vocab": "tokenizer data, not weights",
}
#: The spec's own validating regex, verbatim from `docs/gguf.md`: it checks that at
#: minimum BaseName, SizeLabel and Version are present and in order. Used only as
#: a fallback, so a box without `gguf` still parses names by the spec's grammar
#: rather than by a private guess.
_NAMING_RE = re.compile(
    r"^(?:(?P<Sidecar>mmproj|mtp)-)?"
    r"(?P<BaseName>[A-Za-z0-9\s]*(?:(?:-(?:(?:[A-Za-z\s][A-Za-z0-9\s]*)|(?:[0-9\s]*)))*))-"
    r"(?:(?P<SizeLabel>(?:\d+x)?(?:\d+\.)?\d+[A-Za-z]"
    r"(?:-[A-Za-z]+(\d+\.)?\d+[A-Za-z]+)?)(?:-(?P<FineTune>[A-Za-z0-9\s-]+))?)?-"
    r"(?:(?P<Version>v\d+(?:\.\d+)*))"
    r"(?:-(?P<Encoding>(?!LoRA|vocab)[\w_]+))?"
    r"(?:-(?P<Type>LoRA|vocab))?"
    r"(?:-(?P<Shard>\d{5}-of-\d{5}))?\.gguf$"
)


def _from_filename(filename: str) -> Dict[str, str]:
    """The spec's named components for `filename`, or `{}` if it does not conform.

    Empty is a real answer and not a failure: an imported model is often named by
    whoever made it, and refusing to describe those fields leaves the header -
    which does describe them - as the only source.
    """
    match = _NAMING_RE.match(filename)
    if not match:
        return {}
    return {k: v for k, v in match.groupdict().items() if v}


_SHARD_RE = re.compile(r"-(?P<num>\d{5})-of-(?P<total>\d{5})\.gguf$")


def _head(path: Path, count: int = 64) -> Optional[bytes]:
    """The first bytes, or `None` **if the file could not be opened**.

    `None` is kept distinct from `b""` because "you may not read this" and "this
    is empty" are different facts and collapsing them is how a permission problem
    gets reported as a format problem - which is exactly what an earlier version
    of this module did, answering "no backend loads `unreadable` for llm" about a
    root-owned mode-0600 file. That is a confident wrong answer, and it was found
    only by running the catalog against the real directory.
    """
    try:
        with Path(path).open("rb") as handle:
            return handle.read(count)
    except OSError:
        return None


def _is_onnx(head: bytes) -> bool:
    if len(head) < 6 or head[0] != 0x08 or head[1] != 0x12:
        return False
    length = head[2]
    if not 2 <= length <= 32:
        return False
    return head[3:3 + length] in _ONNX_PRODUCERS


def _is_safetensors(head: bytes) -> bool:
    """8-byte little-endian header length, then JSON. The check is exact."""
    if len(head) < 9:
        return False
    length = int.from_bytes(head[:8], "little")
    return 0 < length <= _SAFETENSORS_MAX_HEADER and head[8:9] == b"{"


def identify(path: Path) -> Dict[str, object]:
    """`(format, evidence, readable)` from the bytes alone.

    `unknown` is a real answer and carries the first bytes as evidence: a file we
    cannot identify must say so rather than be guessed at from its name.
    """
    head = _head(Path(path))
    if head is None:
        return {"format": "unreadable", "evidence": "the file could not be opened",
                "readable": False}
    if not head:
        return {"format": "empty", "evidence": "the file is zero bytes",
                "readable": True}
    for name, magic, note in _MAGICS:
        if head.startswith(magic):
            version = ""
            if name == "gguf" and len(head) >= 8:
                version = f", version {int.from_bytes(head[4:8], 'little')}"
            return {"format": name, "evidence": note + version, "readable": True}
    if _is_onnx(head):
        producer = head[3:3 + head[2]].decode("ascii", "replace")
        return {"format": "onnx", "evidence": f"ONNX (protobuf), producer {producer!r}",
                "readable": True}
    if _is_safetensors(head):
        size = int.from_bytes(head[:8], "little")
        return {"format": "safetensors",
                "evidence": f"safetensors, JSON header of {size} bytes",
                "readable": True}
    return {"format": "unknown",
            "evidence": f"no known magic; starts {head[:8].hex(' ')}",
            "readable": True}


# --- structure: the one place that decides what a file *is* ----------------

def kind_of(filename: str) -> Tuple[str, str]:
    """`(kind, reason)` from the spec's naming convention. **Single source.**

    `kind` is `model` for anything that can be loaded whole; every other value
    comes with the reason it is not, because a filter that silently drops a 5 GB
    adapter makes it look like a corrupt download.
    """
    stem = filename[:-len(".gguf")] if filename.endswith(".gguf") else filename
    head, _, _tail = stem.partition("-")
    if head in _SIDECARS:
        return "sidecar", _SIDECARS[head]
    # **Case-insensitive, and the lowercase form is the common one.** The table
    # spells it `LoRA`, but the GGUF naming convention is used in lower case by
    # the exporters that produce these files. Measured on this machine, in the
    # real model directory: four files named `*-lora.gguf`, all lowercase, and
    # `kind_of` called every one of them a `model`. `endswith("-LoRA")` is false
    # for `-lora`, so the check silently did nothing and a filter that "drops
    # adapters" was not dropping any.
    lowered = stem.lower()
    for suffix, note in _TYPE_SUFFIXES.items():
        if lowered.endswith("-" + suffix.lower()):
            return ("adapter" if suffix == "LoRA" else "vocab"), note
    hit = _SHARD_RE.search(filename)
    if hit:
        number, total = int(hit.group("num")), int(hit.group("total"))
        if number != 1:
            return "shard", (f"shard {number} of {total}; only the first shard of "
                             "a split model is loadable on its own")
        return "shard-head", f"shard 1 of {total}; the other shards must sit beside it"
    return "model", ""


# --- what each runtime can actually load ------------------------------------

class Backend:
    """One runtime, and the `(format, role)` pairs it really accepts.

    Declared, never inferred from a filename. This is the join that makes a
    present file usable, and keeping it explicit is what stops the catalog from
    offering a 57 MB STT container as a chat model - which is what a flat
    "every model file" list does.
    """

    def __init__(self, name: str, formats: Tuple[str, ...],
                 roles: Tuple[str, ...], how: str = "") -> None:
        self.name, self.formats, self.roles, self.how = name, formats, roles, how

    def serves(self, fmt: str, role: str) -> bool:
        return fmt in self.formats and role in self.roles

    def __repr__(self) -> str:
        return f"<Backend {self.name} {'+'.join(self.formats)}>"


#: Every entry names the real call site it came from.
BACKENDS: Tuple[Backend, ...] = (
    Backend("llama-server", ("gguf",), ("llm", "embed", "vision"),
            "local_llm.server_args(); --mmproj for the vision sidecar"),
    Backend("whisper.cpp", ("ggml-legacy",), ("stt",),
            "stt.py / stt_server.py load ggml-<size>-<quant>.bin"),
    Backend("piper", ("onnx",), ("tts",), "tts.py builds --piper-voice <name>.onnx"),
    Backend("kokoro", ("onnx",), ("tts",),
            "voices.py: --kokoro-model model.int8.onnx"),
    Backend("silero", ("onnx",), ("vad",), "wakeword.py: model_quantized.onnx"),
)

#: Files that are **not** neural models, because a subsystem passes them as a side
#: table. Declared rather than magic-matched: this blob's first bytes are tensor
#: data and identify nothing, and "unknown format, known purpose" is the honest
#: answer rather than a guess.
NOT_A_MODEL = {
    "voices.bin": ("voice-set", "kokoro",
                   "Kokoro's speaker embeddings: a lookup table the model indexes, "
                   "not weights that are executed"),
}

_MODEL_SUFFIXES = (".gguf", ".bin", ".onnx", ".safetensors")


def backend_for(fmt: str, role: str) -> Optional[Backend]:
    for backend in BACKENDS:
        if backend.serves(fmt, role):
            return backend
    return None


def role_for(filename: str, fmt: str) -> Tuple[str, str]:
    """`(role, note)` from the filename and the *structural* kind.

    **Never `*.gguf -> llm`.** That glob was in an earlier version and it was
    wrong three ways at once on real files in one directory: it called
    `mmproj-SmolVLM2.gguf` a language model, and `nomic-embed-text-v1.5.gguf` one
    too. Both are decided here from the sidecar prefix and the name instead.

    **And never a role for a file whose bytes were not identified at all.**
    That is the same lesson arriving from the other side, and `identify()` is what
    makes it checkable: it reports `unknown` with the first bytes as evidence, and
    `unknown` is **not** in any `BACKENDS` entry's formats - so a role claimed for
    such a file can only ever land in `catalog`'s `present` list, where the reason
    read *"declared as llm, but no backend on this machine loads unknown for llm"*.

    Measured on this machine, on real files: `catalog()` over the data directory
    returned two `arg-0.bin` as *"declared as llm"*. They are **73-byte PNGs**
    (`\x89PNG\r\n\x1a\n`) - screenshots passed to calls as attachments. A
    confident false claim about two real files on disk.

    The fix is not "trust the suffix less". A `.bin` is the one suffix that can be
    anything, and the bytes are what say otherwise. `safetensors` is *positively*
    identified and still claims `llm`, with the true reason *"no backend loads
    safetensors for llm"* - which is worth saying, because it tells a person they
    have a model file that Chronoa cannot run. That answer is kept; only the
    guess is dropped.
    """
    stem = filename[:-len(".gguf")] if filename.endswith(".gguf") else filename
    kind, reason = kind_of(filename)

    if filename in NOT_A_MODEL:
        return NOT_A_MODEL[filename][0], NOT_A_MODEL[filename][2]
    if fmt == "ggml-legacy" or filename.startswith("ggml-"):
        return "stt", "legacy ggml container, which is whisper.cpp's format"
    if filename == "model.int8.onnx":
        return "tts", "the Kokoro 82M voice model"
    if filename == "model_quantized.onnx":
        return "vad", "Silero VAD: voice activity, not speech recognition"
    if fmt == "onnx":
        return "tts", "a Piper voice: one file per speaker"
    if filename.startswith("mmproj-"):
        return "vision", "the projector half of a multimodal pair, loaded with --mmproj"
    if filename.startswith("mtp-"):
        return "vision", "multi-token-prediction heads for speculative decoding"
    if fmt == "unknown":
        # Placed **after** every case that is decided by the filename's *shape* on
        # purpose. `mmproj-`, `mtp-` and the two Kokoro/Silero names are prefixes
        # and exact names that mean the same thing whatever the bytes turned out to
        # be, and the bytes are normally a machine-written file. This case is the
        # opposite: a `.bin` whose first bytes are not a format anything here
        # loads, so the only evidence left is a name - and `NOT_A_MODEL` above
        # already covers the names whose meaning is *declared* rather than guessed.
        return "", reason or (
            f"the first bytes are not a format anything here loads, so nothing "
            f"claims what {filename} is - the name is not evidence")
    if kind == "adapter":
        return "", reason
    low = stem.lower()
    if "embed" in low:
        return "embed", "an embedding model: vectors, not chat"
    if kind == "shard":
        return "", reason
    if "vision" in low or "vl" in low.split("-")[:2]:
        return "vision", "a multimodal model"
    if kind in ("model", "shard-head"):
        return "llm", "a language model"
    return "", reason or "nothing claims this file"


def catalog(roots) -> Dict[str, object]:
    """Every model-shaped file under `roots`, with format, role and loadability.

    Three lists, because they call for different decisions:

    - `usable` - a backend serves this `(format, role)`. Offered.
    - `present` - a real file nothing can load. **Named, with the reason**; a file
      that is recognised but unusable is the case that most needs saying, and
      dropping it silently is the same defect as claiming it is absent.
    - `unreadable` - the bytes could not be opened, kept **separate from
      `present`** because a permission problem is not a format problem and the
      fix for one is not the fix for the other.
    """
    usable: List[Dict[str, object]] = []
    present: List[Dict[str, object]] = []
    unreadable: List[Dict[str, object]] = []
    unidentified: List[Dict[str, object]] = []

    for root in roots:
        root = Path(root)
        if not root.is_dir():
            continue
        for path in sorted(root.rglob("*")):
            if path.is_symlink() or not path.is_file():
                continue
            if path.suffix.lower() not in _MODEL_SUFFIXES:
                continue
            found = identify(path)
            fmt = str(found["format"])
            try:
                size = path.stat().st_size
            except OSError:
                size = 0

            if not found["readable"]:
                unreadable.append({
                    "path": path, "filename": path.name, "size": size,
                    "reason": "the file could not be opened, so its format is "
                              "unknown - this is a permissions problem, not a "
                              "model problem"})
                continue

            role, note = role_for(path.name, fmt)
            backend = backend_for(fmt, role) if role else None
            record = {"path": path, "filename": path.name, "format": fmt,
                      "evidence": found["evidence"], "role": role,
                      "backend": backend.name if backend else "", "size": size,
                      "note": note, "kind": kind_of(path.name)[0]}

            if path.name in NOT_A_MODEL:
                usable.append(dict(record, model=False))
            elif backend:
                usable.append(record)
            elif role:
                present.append(dict(record, reason=(
                    f"declared as {role}, but no backend on this machine loads "
                    f"{fmt} for {role}")))
            else:
                present.append(dict(record, reason=note or
                                    "nothing can load this file, and nothing claims it"))

    return {"usable": usable, "present": present,
            "unreadable": unreadable, "unidentified": unidentified}
