"""The GGUF header, read from the first few megabytes instead of the whole file.

**Why this module exists.** Every question Chronoa asks a model file is a question
about its *header*: `general.architecture`, `attention.head_count`, the tensor
names and shapes. None of it needs the weights. The route that answered them until
now was `gguf.GGUFReader`, whose `__init__` starts with

    self.data = np.memmap(path, mode=mode)

- a memory map of the **entire file**. Measured on the loaded model here
  (`Qwen3-1.7B-Q4_K_M.gguf`, 1,107,409,472 bytes), `GGUFReader(path)` plus a walk
  over `reader.fields` took **7.7-9.1 s**, and six call sites did it on one
  startup: `model_registry.active_entry` (7.74 s **on the GTK main thread**,
  which is the whole reason the window froze), `local_llm._cpu_context_arg`
  (9.12 s), and three more.

The header is a few kilobytes at the front of the file and the tensor *table*
(tensor names, dimensions, types, offsets) is the last thing before the data, so
the whole answer lives in the first few MB. Reading only those is not an
optimisation of `GGUFReader` - it is a different function, because `GGUFReader`'s
stated job is to hand you tensor **data**, which is the 1.1 GB and is not wanted.

**What this deliberately does not do.** No `numpy`, no `gguf` import, no mmap.
The format is a published, stable layout (magic, version, two counts, then the
key/value block, then the tensor-info block) and the value-type ids below are
gguf-py's own `GGUFValueType` enum, written as integers so this module has no
dependency at all - which matters because `gguf` is not packaged by any
ecosystem manifest and is therefore absent on a real install.

**A partial read is a reading, not a failure.** The window is a cap, not a
promise: a file whose metadata runs past it yields the keys that fit, and a
header that runs out mid-value stops cleanly rather than raising. Callers treat a
missing key as "this model does not say", which is what a truncated window means
and is the same answer a genuinely absent key gives.
"""

from __future__ import annotations

import struct
from pathlib import Path
from typing import Any, Dict, List

#: How much of the file is read. A real model's metadata plus its tensor-info
#: table is well under a megabyte; 16 MB leaves three orders of magnitude of room
#: and is still 0.0014 of a 1.1 GB file. It is the *cap*, not a tuned number -
#: raising it costs one `read()` the kernel serves from the page cache.
HEADER_BYTES = 16 * 1024 * 1024

#: A single string longer than this is refused rather than allocated. The longest
#: string in a GGUF header is a tokenizer entry or a chat template; anything past
#: this is not metadata any reader in this tree asks for, and refusing it keeps a
#: corrupt length field from becoming a huge allocation.
_MAX_STRING = 8 * 1024 * 1024

#: gguf-py's `GGUFValueType`, in its own order. Read off the enum rather than
#: remembered, because the order is the file format.
_UINT8, _INT8, _UINT16, _INT16 = 0, 1, 2, 3
_UINT32, _INT32, _FLOAT32, _BOOL = 4, 5, 6, 7
_STRING, _ARRAY = 8, 9
_UINT64, _INT64, _FLOAT64 = 10, 11, 12

#: type id -> (struct format, how to present it)
_FIXED = {
    _UINT8: ("<B", int), _INT8: ("<b", int),
    _UINT16: ("<H", int), _INT16: ("<h", int),
    _UINT32: ("<I", int), _INT32: ("<i", int),
    _UINT64: ("<Q", int), _INT64: ("<q", int),
    _FLOAT32: ("<f", float), _FLOAT64: ("<d", float),
    _BOOL: ("<B", bool),
}

_PRELUDE = 4 + 4 + 8 + 8          # magic, version, tensor count, kv count


class Truncated(Exception):
    """The window ended before the value did. Not an error - a stopping point."""


class _Window:
    """A cursor over the bytes that were actually read."""

    __slots__ = ("_data", "_at", "_end")

    def __init__(self, data: bytes):
        self._data = data
        self._at = 0
        self._end = len(data)

    def take(self, count: int) -> bytes:
        stop = self._at + count
        if stop > self._end:
            raise Truncated(f"wanted {count} bytes at {self._at}, have {self._end - self._at}")
        chunk = self._data[self._at:stop]
        self._at = stop
        return chunk

    def skip(self, count: int) -> None:
        """Advance past `count` bytes without copying them.

        **This is the whole reason `read_tensor_shapes` is fast.** The first
        version skipped the key/value block by *decoding* every value and
        throwing the Python object away: profiled, that was 910k `value`/`text`/
        `scalar` calls for a file with 32 keys and 310 tensors, and the read
        itself is 1.9 ms - so the parse was spending ~99.9% of its time
        building values nobody wanted. A cursor that moves is the difference
        between "skip" and "read", and the file bytes are already in memory.
        """
        stop = self._at + count
        if stop > self._end:
            raise Truncated(f"wanted {count} bytes at {self._at}, have {self._end - self._at}")
        self._at = stop

    def scalar(self, type_id: int):
        fmt, cast = _FIXED[type_id]
        return cast(struct.unpack(fmt, self.take(struct.calcsize(fmt)))[0])

    def u32(self) -> int:
        at = self._at
        if at + 4 > self._end:
            raise Truncated(f"wanted 4 bytes at {at}, have {self._end - at}")
        self._at = at + 4
        return _U32.unpack_from(self._data, at)[0]

    def text(self) -> str:
        length = self.scalar(_UINT64)
        if length > _MAX_STRING:
            raise Truncated(f"a string claiming {length} bytes")
        return bytes(self.take(int(length))).decode("utf-8", "replace")

    def _string_array(self, count: int) -> List[str]:
        """Decode `count` length-prefixed strings with the cursor held in locals.

        **This loop is the whole cost of reading a real model's header.** On the
        1.7 B checkpoint `tokenizer.ggml.tokens` is 151,936 entries and
        `tokenizer.ggml.merges` is 151,387 - 303k strings, against 32 keys. A
        version that called `text()` per element measured 0.22 s; binding the
        buffer, the offset and the reader to locals and never re-entering the
        object takes the per-element work from three method calls to one
        `unpack_from`, which is the difference between this being a cost and
        this being free.

        Nesting is **not** re-checked here: `value` has already refused a nested
        array before it got here, and a second check with the element depth
        passed in refuses the *first* array instead - which is a bug this
        function shipped once, measured as a real 32-key header answering with
        19 keys.
        """
        data, at, end = self._data, self._at, self._end
        unpack = _U64.unpack_from
        out: List[str] = []
        append = out.append
        for _ in range(count):
            if at + 8 > end:
                self._at = at
                raise Truncated(f"wanted 8 bytes at {at}, have {end - at}")
            length = unpack(data, at)[0]
            at += 8
            if length > _MAX_STRING:
                self._at = at
                raise Truncated(f"a string claiming {length} bytes")
            stop = at + length
            if stop > end:
                self._at = at
                raise Truncated(f"wanted {length} bytes at {at}, have {end - at}")
            append(data[at:stop].decode("utf-8", "replace"))
            at = stop
        self._at = at
        return out

    def _skip_string_array(self, count: int) -> None:
        """`_string_array` without the objects: the same walk, no `str` built."""
        data, at, end = self._data, self._at, self._end
        unpack = _U64.unpack_from
        for _ in range(count):
            if at + 8 > end:
                self._at = at
                raise Truncated(f"wanted 8 bytes at {at}, have {end - at}")
            length = unpack(data, at)[0]
            at += 8
            if length > _MAX_STRING:
                self._at = at
                raise Truncated(f"a string claiming {length} bytes")
            at += length
            if at > end:
                self._at = at
                raise Truncated(f"a string ran past the window at {at}")
        self._at = at

    def value(self, type_id: int, depth: int = 0):
        if type_id == _STRING:
            return self.text()
        if type_id == _ARRAY:
            # **Nested arrays are refused, not decoded.** They exist in the format
            # and nothing in this tree reads one; decoding them here would mean a
            # second recursive shape with no consumer to keep it honest.
            if depth:
                raise Truncated("a nested array")
            element = self.u32()
            count = int(self.scalar(_UINT64))
            if element == _STRING:
                return self._string_array(count)
            return [self.value(element, depth + 1) for _ in range(count)]
        if type_id not in _FIXED:
            raise Truncated(f"unknown value type {type_id}")
        return self.scalar(type_id)

    def skip_value(self, type_id: int, depth: int = 0) -> None:
        """Advance past one value of `type_id` without building it.

        Same shape as `value` - and the same refusal of a nested array, so the
        two cannot disagree about where a value ends. Every branch is a *length*
        walked rather than an object built, which is what makes skipping a
        tensor-table read cheap: `read_tensor_shapes` crosses the entire
        key/value block through here and reads no key either (the key is a
        string, and its length prefix is all the cursor needs).
        """
        if type_id == _STRING:
            length = self.scalar(_UINT64)
            if length > _MAX_STRING:
                raise Truncated(f"a string claiming {length} bytes")
            self.skip(int(length))
            return
        if type_id == _ARRAY:
            if depth:
                raise Truncated("a nested array")
            element = self.u32()
            count = int(self.scalar(_UINT64))
            if element == _STRING:
                self._skip_string_array(count)
                return
            if element not in _FIXED:
                raise Truncated(f"unknown value type {element}")
            self.skip(count * struct.calcsize(_FIXED[element][0]))
            return
        if type_id not in _FIXED:
            raise Truncated(f"unknown value type {type_id}")
        self.skip(struct.calcsize(_FIXED[type_id][0]))


def _prelude(path):
    """`(window, tensor_count, kv_count)` for a GGUF, or `None`.

    The two counts are read here rather than by each caller so that both routes
    start from the same place - and so neither can read one of them twice, which
    is how the first version of this read the first key's length prefix as the
    key count and answered every file with an empty dict.
    """
    try:
        with open(path, "rb") as handle:
            head = handle.read(HEADER_BYTES)
    except OSError:
        return None
    if len(head) < _PRELUDE or head[:4] != b"GGUF":
        return None
    window = _Window(head)
    try:
        window.take(4)                       # magic
        window.u32()                         # version; 2 and 3 both exist
        tensors = int(window.scalar(_UINT64))
        pairs = int(window.scalar(_UINT64))
    except Truncated:
        return None
    return window, tensors, pairs


#: Pre-compiled readers for the two shapes that dominate a real header: a
#: `uint64` (every string length) and a `uint32` (every value-type tag). Built
#: once at import because `_Window` is on the hot path of *every* header read,
#: and `struct.unpack(fmt, chunk)` re-parses its format string on every call.
_U64 = struct.Struct("<Q")
_U32 = struct.Struct("<I")


def read_metadata(path) -> Dict[str, Any]:
    """Every key/value in the header, decoded to plain Python. `{}` if unreadable.

    **The values are what the format says, not what a reader happened to get out
    of a numpy view.** A string is a `str`, a scalar is an `int`/`float`/`bool`,
    and an array is a **list** - which the old route could not produce, because
    `ReaderField.parts[-1]` on an array field is its *last element*. That is not
    a cosmetic difference: `ctxfit.metadata_kv_width` refuses a per-layer
    `head_count_kv` list on the grounds that the widths differ per layer, and
    through that route the list never arrived, so the refusal could not fire.
    """
    opened = _prelude(path)
    if opened is None:
        return {}
    window, _tensors, count = opened
    out: Dict[str, Any] = {}
    for _ in range(count):
        try:
            key = window.text()
            out[key] = window.value(window.u32())
        except Truncated:
            break
    return out


def read_tensor_shapes(path) -> Dict[str, List[int]]:
    """Tensor name -> dimensions, **in `gguf`'s own `ReaderTensor.shape` order**.

    **Which is the on-disk order, un-reversed** - and that was measured rather
    than reasoned, because the obvious reading of it is backwards. `GGUFReader`
    splits its own work in two: `np_dims = tuple(reversed(dims.tolist()))` builds
    the *numpy view's* shape, while `ReaderTensor(shape = dims)` hands out the
    dims exactly as stored. So `gguf.GGUFReader` and this agree tensor for
    tensor, and **310 of 310** agree on the checkpoint measured here.

    An earlier version reversed on the belief that `gguf` did. It reported
    **169 equal and 310 reversed-equal** against the real file - consistently
    wrong, on every tensor - and because `ctxfit.kv_width_from_shapes` reads
    `shape[1]`, the K/V projection came back **2048 wide against a header that
    says 1024**. The cross-check would have reported a disagreement about a model
    that agrees with itself, naming the wrong number, on every machine. It was
    invisible only because the second defect in this pair raised before it could
    be reached.

    Shapes are the *last* thing before the weights, so this skips the whole
    key/value block to reach them - and still never touches a byte of data.
    """
    opened = _prelude(path)
    if opened is None:
        return {}
    window, tensors, pairs = opened
    out: Dict[str, List[int]] = {}
    try:
        for _ in range(pairs):              # the key/value block, skipped whole
            window.skip_value(_STRING)       # the key is a string
            window.skip_value(window.u32())  # then its type, then its value
        for _ in range(tensors):
            name = window.text()
            dims = [int(window.scalar(_UINT64)) for _ in range(window.u32())]
            window.u32()                     # ggml type
            window.scalar(_UINT64)            # offset into the data section
            out[name] = dims
    except Truncated:
        pass                                 # what fitted is the answer
    return out


def cache_key(path) -> Any:
    """What makes two reads of one file the same read: `(path, mtime, size)`.

    Size and mtime together, which is the same pair `learning._durable_records`
    and `maps._cached_get` use. **mtime alone** would miss an in-place rewrite
    that keeps its timestamp, and **size alone** would miss a rewrite that lands
    on the same length - both are how a header cache hands back the previous
    model's numbers. The stat is inside the key rather than outside it so a file
    that cannot be stat'd simply misses the cache.
    """
    try:
        info = Path(path).stat()
    except OSError:
        return None
    return (str(path), info.st_mtime_ns, info.st_size)