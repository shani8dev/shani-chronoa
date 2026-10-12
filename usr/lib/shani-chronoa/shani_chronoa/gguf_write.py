"""Write a GGUF whose metadata section is **byte-identical** to a parent's.

**Why this module exists, and it is a measured failure.** `merge.blend` rebuilt
the merged model's metadata through `gguf-py`'s typed writer, preserving each
key's *declared* type from the reader. That was not enough. On a real
`Qwen3-0.6B-Q8_0.gguf` the merged file came out with **15 of 31 keys missing**
- every architecture-specific `qwen3.*` one - and `llama-quantize` refused it:

    llama_model_quantize: failed to quantize:
        key not found in model: qwen3.context_length

Not one of those keys was exotic. They are ordinary `UINT32` and `FLOAT32`
scalars that any writer should carry, and they went missing anyway because the
round trip goes through Python values, declared-type tags and a lazily-packed
writer, and every one of those is a place a key can be reinterpreted. **The
metadata section is the one part of a merge that must not be interpreted at
all**, so it is not.

GGUF's layout puts the metadata between a fixed 24-byte preamble and the tensor
infos, and nothing in between depends on the weights:

    magic "GGUF" | version u32 | tensor_count u64 | kv_count u64
    | key/value pairs ...            <- copy these bytes verbatim
    | tensor infos ...               <- rebuilt: types and offsets change
    | pad to general.alignment
    | tensor data                    <- the blend

So this module does two things and refuses to do a third: it finds the exact
byte range of the metadata section, and it writes a container around a supplied
list of tensors. It does not inspect, convert, reorder or "improve" a single
metadata byte. The spec's own words for why that matters are in the comment on
`kv_section_bytes`.

Nothing here is llama-specific beyond the F16 tensor type; a BF16 or F32 caller
changes one constant and the layout code is unaffected.
"""

from __future__ import annotations

import struct
from pathlib import Path
from typing import Dict, List, Optional, Sequence, Tuple

import numpy as np

#: GGUF value types, from `docs/gguf.md`. Only what a size table needs.
_UINT8, _INT8 = 0, 1
_UINT16, _INT16 = 2, 3
_UINT32, _INT32, _FLOAT32, _BOOL = 4, 5, 6, 7
_STRING, _ARRAY = 8, 9
_UINT64, _INT64, _FLOAT64 = 10, 11, 12

_FIXED: Dict[int, int] = {_UINT8: 1, _INT8: 1, _UINT16: 2, _INT16: 2,
                          _UINT32: 4, _INT32: 4, _FLOAT32: 4, _BOOL: 1,
                          _UINT64: 8, _INT64: 8, _FLOAT64: 8}

#: ggml tensor types written here. F16 is deliberate: the blend happens in f32
#: and this is the single lossy step, after which `llama-quantize` runs once.
GGML_TYPE_F32 = 0
GGML_TYPE_F16 = 1
GGML_TYPE_BF16 = 30

#: The spec: "If not specified, assume it is 32."
DEFAULT_ALIGNMENT = 32


class GgufFormatError(ValueError):
    """The bytes are not a GGUF this reader understands. Named, because
    "merge failed" over a gigabyte of work is not an error message."""


def _read_typed(data: bytes, offset: int, vtype: int) -> Tuple[int, int]:
    """`(value_bytes, next_offset)` for one typed value at `offset`."""
    if vtype == _STRING:
        (length,) = struct.unpack_from("<Q", data, offset)
        end = offset + 8 + length
        return data[offset:end], end
    if vtype == _ARRAY:
        (count,) = struct.unpack_from("<Q", data, offset)
        offset += 8
        (elem_type,) = struct.unpack_from("<I", data, offset)
        offset += 4
        if elem_type == _STRING:
            for _ in range(count):
                _value, offset = _read_typed(data, offset, elem_type)
            return data[:0], offset
        size = _FIXED.get(elem_type)
        if size is None:
            raise GgufFormatError(f"array of unknown element type {elem_type}")
        end = offset + size * count
        return data[offset:end], end
    size = _FIXED.get(vtype)
    if size is None:
        raise GgufFormatError(f"unknown value type {vtype} at byte {offset}")
    return data[offset:offset + size], offset + size


def _tensor_info_size(name: str, n_dims: int) -> int:
    """Bytes one `gguf_tensor_info_t` occupies.

    From the spec: `gguf_string name`, `uint32 n_dimensions`,
    `uint64 dimensions[n]`, `ggml_type type`, `uint64 offset`.
    """
    return 8 + len(name.encode("utf-8")) + 4 + 8 * n_dims + 4 + 8


def kv_section_bytes(path: Path) -> Tuple[bytes, int, int]:
    """`(metadata_bytes, tensor_count, alignment)` for a real GGUF.

    **Computed by offset arithmetic, not by walking the key/value stream.** The
    first version walked it, and walked keys 0-17 of a real Qwen3 file to the
    byte - then desynchronised inside `tokenizer.ggml.tokens`, an array of
    151,936 strings, and ran the offset to 6.5e14. A parser is one more thing
    that can be wrong about a file whose contents are not in doubt.

    The reader already knows every tensor's name, shape and type, so the
    tensor-info section's size is exactly computable, and the KV section is
    everything before it:

        kv_end = first_tensor_data_offset - tensor_info_bytes - padding

    `padding` is the spec's alignment gap between the infos and the data, so each
    of the `alignment` candidates is tried and the right one is *confirmed* by
    parsing the first tensor's name at that offset. A guess that is checked is
    not a guess; the returned bytes are the parent's, unmodified.

    Nothing here interprets a single metadata value - which is the whole point.
    """
    from gguf import GGUFReader

    path = Path(path)
    reader = GGUFReader(str(path))
    tensors = reader.tensors
    if not tensors:
        raise GgufFormatError(f"{path.name} declares no tensors")

    data = path.read_bytes()
    if data[:4] != b"GGUF":
        raise GgufFormatError(f"{path.name} does not begin with GGUF")

    alignment = DEFAULT_ALIGNMENT
    field = reader.fields.get("general.alignment")
    if field is not None:
        try:
            raw = field.parts[-1]
            raw = raw.tolist() if hasattr(raw, "tolist") else raw
            if isinstance(raw, list) and raw:
                raw = raw[0]
            alignment = int(raw) or alignment
        except (AttributeError, IndexError, TypeError, ValueError):
            pass

    info_bytes = sum(_tensor_info_size(t.name, len(t.shape)) for t in tensors)
    first_data = min(t.data_offset for t in tensors)
    wanted = tensors[0].name.encode("utf-8")

    for pad in range(alignment):
        start = first_data - info_bytes - pad
        if start < 24:
            break
        try:
            (length,) = struct.unpack_from("<Q", data, start)
        except struct.error:
            break
        if length == len(wanted) and data[start + 8:start + 8 + length] == wanted:
            return data[:start], len(tensors), alignment

    raise GgufFormatError(
        f"could not locate the end of the metadata section in {path.name}: "
        f"tensor data begins at {first_data}, the tensor infos occupy "
        f"{info_bytes} bytes, and no padding in 0..{alignment - 1} puts the "
        f"first tensor name {wanted[:24]!r} where the arithmetic says it "
        "should be")


def _pack_string(text: bytes) -> bytes:
    return struct.pack("<Q", len(text)) + text


def write_gguf(target: Path, metadata: bytes, tensor_count: int,
               alignment: int,
               tensors: Sequence[Tuple[str, np.ndarray, int]],
               ggml_type: Optional[int] = None) -> Path:
    """Write a complete GGUF: copied metadata, then `tensors` in the given order.

    `metadata` must be the bytes from `kv_section_bytes`, **including the
    24-byte preamble**. `tensor_count` is taken from the preamble rather than
    from `len(tensors)` and is asserted against it - a mismatch means the caller
    is about to write a header that disagrees with its own body, which is the
    kind of file that loads on one machine and not another.
    """
    if len(tensors) != tensor_count:
        raise GgufFormatError(
            f"the preamble declares {tensor_count} tensors but {len(tensors)} "
            "were supplied - writing that would produce a file whose header "
            "disagrees with its contents")

    infos = bytearray()
    payload = bytearray()
    for name, array, tensor_type in tensors:
        # **Each tensor keeps its own storage type, and this is load-bearing.**
        # Measured on Qwen3-0.6B-Q8_0: **113 of 310 tensors are F32** - every
        # norm vector and bias - while the 197 weight matrices are Q8_0. Writing
        # all of them F16, which the first version did, produces a file that
        # loads and then dies computing with
        # `binary_op: unsupported types: dst: f32, src0: f32, src1: f16`,
        # because the graph multiplies an f32 accumulator by an f16 norm. So the
        # caller decides per tensor and this module does not second-guess it;
        # `ggml_type` is only the default for a two-element `tensors` list.
        if tensor_type == GGML_TYPE_BF16:
            array = array.astype(np.float32)   # stored f32; see note below
            tensor_type = GGML_TYPE_F32
        elif tensor_type == GGML_TYPE_F32:
            array = array.astype(np.float32)
        else:
            tensor_type = ggml_type if ggml_type is not None else GGML_TYPE_F16
            array = array.astype(np.float16)
        raw = np.ascontiguousarray(array).tobytes()
        offset = (len(payload) + alignment - 1) // alignment * alignment
        payload.extend(b"\x00" * (offset - len(payload)))
        payload.extend(raw)
        # **GGUF stores dimensions in ggml order, which is the reverse of
        # numpy's, while the tensor bytes stay in numpy order.** Measured against
        # a one-tensor file written by `gguf-py` itself: it reports
        # `tensor.shape == [16, 8]` for an `(8, 16)` array and its `.data`
        # compares equal to the array as written. Writing the dims unreversed
        # produces a reader that reports `[8, 16]` and hands back a `(16, 8)`
        # array - a file llama.cpp would load with every projection transposed.
        dims = list(array.shape)[::-1]
        infos += _pack_string(name.encode("utf-8"))
        infos += struct.pack("<I", len(dims))
        for dim in dims:
            infos += struct.pack("<Q", dim)
        infos += struct.pack("<I", tensor_type)
        infos += struct.pack("<Q", offset)

    padding = (alignment - (len(metadata) + len(infos)) % alignment) % alignment
    target = Path(target)
    with target.open("wb") as handle:
        handle.write(metadata)
        handle.write(bytes(infos))
        handle.write(b"\x00" * padding)
        handle.write(bytes(payload))
    return target
