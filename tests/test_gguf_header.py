"""The GGUF header, read from the first few MB, and the cache in front of it.

## What this replaces, and the number that decided it

`ctxfit.gguf_model_info` read the header through `gguf.GGUFReader`, whose
`__init__` opens with

    self.data = np.memmap(path, mode=mode)

- a memory map of the **entire checkpoint**. Measured on the model loaded on this
machine (`Qwen3-1.7B-Q4_K_M.gguf`, 1,107,409,472 bytes):

| route | time |
|---|---:|
| `gguf.GGUFReader(path)` alone | **3.50 s** |
| plus a walk over `reader.fields` (what the old route did) | **7.7 - 9.1 s** |
| `gguf_header.read_metadata(path)`, all 32 keys | **0.127 s** |
| `gguf_header.read_tensor_shapes(path)`, all 310 tensors | **0.032 s** |

**27x and 109x**, and the second is the one `kv_width_disagreement` pays on every
context fit. Six call sites read the same header inside one startup
(`local_llm._context_arg`, `_cpu_context_arg`, `bytes_per_token`,
`model_registry.scan`, `model_registry.active_entry`, `describe`), and
`active_entry` runs on the **GTK main thread** - which is the whole reason the
window froze for seconds after start.

So there are two independent changes and both are measured here: **the read**
(no whole-file mmap) and **the cache** (one read, not six). `PREBUILD = False` on
`gui/surfaces/models.py` is the third and is in its own file's tests.

## What the parser is, and what it refuses

No `numpy`, no `gguf` import, no mmap. The value-type ids are gguf-py's own
`GGUFValueType` written as integers, because the format *is* those numbers and
`gguf` is packaged by no ecosystem manifest - so the module that reads every model
header has no dependency that a real install might not have.

Fixtures are written by **`GGUFWriter`**, never by hand, and every parser result
is compared against **`gguf.GGUFReader` on the same file**. A hand-built fixture
proves the parser agrees with whoever wrote the fixture; this compares two
independent implementations of the same published format.

## The one behaviour change, which is a bug fix

`GGUFReader`'s `ReaderField.parts[-1]` is a string field's *bytes* and an array
field's **last element**. So the old dict could not express a per-layer
`head_count_kv` at all - which means `metadata_kv_width`'s refusal of a list
(`if isinstance(kv_heads, (list, tuple)): return None`) **could never fire**.
Arrays decode to lists here, so that refusal is reachable for the first time, and
it is pinned in both directions: a scalar still answers, a per-layer list still
refuses.
"""

import os
import sys
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "usr/lib/shani-chronoa"))

from shani_chronoa import adapter, ctxfit, gguf_header  # noqa: E402

REAL_MODEL = os.path.expanduser(
    "~/.local/share/shani-chronoa/llm/Qwen3-1.7B-Q4_K_M.gguf")


def _write(path, *, arch="qwen3", pairs=(), tensors=()):
    """A real GGUF, via gguf-py's own writer."""
    import gguf

    writer = gguf.GGUFWriter(str(path), arch=arch)
    for key, value, kind in pairs:
        writer.add_key_value(key, value, kind)
    for name, dims in tensors:
        import numpy
        # dims are **reader-reported ggml axes**; `add_tensor` takes the numpy
        # shape and the reader reverses it. Measured: `zeros((2048, 1024))` is
        # read back as `[1024 2048]`, so unreversed dims hand the parser a
        # transposed file and read as a parser defect rather than a fixture
        # defect. `raw_shape` is left alone so the writer derives it.
        writer.add_tensor(name, numpy.zeros(tuple(reversed(dims)),
                                            numpy.float32))
    writer.write_header_to_file()
    writer.write_kv_data_to_file()
    writer.write_tensors_to_file()
    writer.close()
    return path


def _timed(fn, *args):
    """Wall time of one call. Both sides of the timing test go through this."""
    start = time.monotonic()
    fn(*args)
    return time.monotonic() - start


@pytest.fixture
def small(tmp_path):
    """Every value shape a header can carry, plus two tensors."""
    from gguf import GGUFValueType as T
    return _write(
        tmp_path / "small.gguf",
        pairs=[
            ("general.type", "model", T.STRING),
            ("general.tags", ["a", "b", "c"], T.ARRAY),
            ("qwen3.block_count", 28, T.UINT32),
            ("qwen3.embedding_length", 2048, T.UINT32),
            ("qwen3.attention.head_count", 16, T.UINT32),
            ("qwen3.attention.head_count_kv", 8, T.UINT32),
            ("qwen3.rope.freq_base", 1000000.5, T.FLOAT32),
            ("qwen3.expert_count", [1, 2, 3], T.ARRAY),
        ],
        tensors=[("blk.0.attn_k.weight", [2048, 1024]),
                 ("blk.0.ffn.weight", [512, 256])])


class TestTheHeaderIsTheFormat:
    def test_every_value_shape_round_trips(self, small):
        from gguf import GGUFReader

        mine = gguf_header.read_metadata(small)
        reader = GGUFReader(str(small))
        # `GGUFReader` synthesises `GGUF.version` / `.tensor_count` /
        # `.kv_count` from the prelude - they are **not in the file**, and this
        # parser reads only what is in it. Measured on a one-key fixture:
        # `list(reader.fields)` is four long and three of them are `GGUF.*`.
        theirs = {k: v for k, v in reader.fields.items()
                  if not k.startswith("GGUF.")}
        assert theirs, "the fixture wrote no fields at all"
        # The keys agree: a parser that read the wrong count would answer with a
        # subset and the shapes below would still pass.
        assert set(mine) == set(theirs)
        for key in ("general.type", "qwen3.block_count",
                    "qwen3.embedding_length", "qwen3.rope.freq_base"):
            assert mine[key] == reader.fields[key].contents(), key
        # Arrays are lists here and numpy views there. **This is the behaviour
        # change**, and it is the whole reason `metadata_kv_width`'s list refusal
        # is now reachable at all.
        assert mine["general.tags"] == ["a", "b", "c"]
        assert list(mine["qwen3.expert_count"]) == [1, 2, 3]

    def test_tensor_shapes_match_the_reader_exactly(self, small):
        """**One assertion, on a file we wrote, and it is the one that caught a
        real defect.** `GGUFReader` splits its own work in two - `np_dims =
        reversed(dims)` builds the numpy view's shape, `ReaderTensor(shape =
        dims)` hands out the stored order - so "what order does `gguf` report
        shapes in" has to be asked of the installed library and not reasoned
        about. A version of `read_tensor_shapes` that reversed on the belief
        that `gguf` did agreed with it on **169 of 310** tensors and matched
        every one of them reversed, and `kv_width_from_shapes` reads `shape[1]`.

        Both sides are compared tensor for tensor, so a parser that is
        *consistently* transposed fails rather than passing on the 1-D tensors
        where a reversal is invisible.
        """
        import gguf

        theirs = {t.name: [int(d) for d in t.shape]
                  for t in gguf.GGUFReader(str(small)).tensors}
        assert theirs, "the fixture wrote no tensors at all"
        assert gguf_header.read_tensor_shapes(small) == theirs

    def test_a_transposed_tensor_would_not_look_right(self, small):
        """The control the comparison above cannot supply on its own.

        The fixture's header says `head_count_kv` 8 x `head_dim`
        `2048/16 = 128` = **1024**, and its K projection is declared
        `[2048, 1024]` so that `shape[1]` is that same 1024. **The two agree by
        construction**, so a reader that transposed would report 2048 against a
        header saying 1024 - which is what it did, on the real checkpoint, until
        the comparison above was written against the installed library.

        Stated against the number rather than against `gguf`, because a fixture
        that agreed with the parser about being transposed would pass.
        """
        shapes = gguf_header.read_tensor_shapes(small)
        assert shapes["blk.0.attn_k.weight"] == [2048, 1024]
        assert shapes["blk.0.attn_k.weight"][1] == 1024, (
            "kv_width_from_shapes reads shape[1]; if the reader transposed, the "
            "cross-check would report 2048 against a header saying 1024")
        assert ctxfit.kv_width_from_shapes(sorted(shapes.items())) == 1024

    def test_a_read_is_not_a_whole_file_read(self, small):
        """**The property the module exists for**, asserted on the code itself.

        `GGUFReader` memmaps the entire file; this reads a window. Asserted as
        "the window is capped and is a fraction of the file" rather than as a
        timing, because a timing assertion is a number that moves with the machine
        and this is the property that has to hold everywhere.
        """
        assert 0 < gguf_header.HEADER_BYTES < 64 * 1024 * 1024
        # A file far larger than the window: the answer is still complete, which
        # is what makes a cap safe rather than lossy.
        big = _write(Path(small).with_name("big.gguf"),
                     pairs=[("general.type", "model", 8)],
                     tensors=[("blk.0.attn_k.weight", [2048, 4096])])
        big.write_bytes(big.read_bytes() + os.urandom(3 * gguf_header.HEADER_BYTES))
        assert big.stat().st_size > 3 * gguf_header.HEADER_BYTES
        assert gguf_header.read_metadata(big)["general.type"] == "model"
        assert "blk.0.attn_k.weight" in gguf_header.read_tensor_shapes(big)


class TestAnUnreadableFileIsAnEmptyAnswer:
    def test_a_missing_file(self, tmp_path):
        assert gguf_header.read_metadata(tmp_path / "nope.gguf") == {}
        assert gguf_header.read_tensor_shapes(tmp_path / "nope.gguf") == {}

    def test_a_file_that_is_not_a_gguf(self, tmp_path):
        junk = tmp_path / "junk.gguf"
        junk.write_bytes(b"this is not a GGUF file, it is just some bytes" * 500)
        assert gguf_header.read_metadata(junk) == {}
        assert gguf_header.read_tensor_shapes(junk) == {}

    def test_a_directory_is_not_a_crash(self, tmp_path):
        assert gguf_header.read_metadata(tmp_path) == {}

    def test_a_truncated_header_is_partial_not_an_error(self, small):
        """A window that runs out stops cleanly; what fitted is the answer."""
        head = small.read_bytes()[:40]
        clipped = Path(small).with_name("clipped.gguf")
        clipped.write_bytes(head)
        info = gguf_header.read_metadata(clipped)
        assert isinstance(info, dict)        # no exception, whatever it managed
        assert gguf_header.read_tensor_shapes(clipped) == {}


class TestTheHeaderCache:
    def test_two_reads_of_one_file_parse_it_once(self, small, monkeypatch):
        """The cache is the second half of the fix; this is its whole claim."""
        ctxfit._INFO_CACHE.clear()
        calls = []
        real = gguf_header.read_metadata

        def counting(path):
            calls.append(path)
            return real(path)

        monkeypatch.setattr(gguf_header, "read_metadata", counting)
        first = ctxfit.gguf_model_info(small)
        second = ctxfit.gguf_model_info(small)
        assert first == second
        assert len(calls) == 1, "six call sites in one startup, one parse"

    def test_a_rewritten_file_is_reread(self, small, monkeypatch):
        """**The stale-header bug, and it is the reason the key is a stat.**

        `current.gguf` points somewhere; the pointed-at file is re-quantised in
        place. A cache keyed on the path alone hands back the *previous* model's
        architecture, and a context decision made from it is confidently wrong.
        """
        ctxfit._INFO_CACHE.clear()
        assert ctxfit.gguf_model_info(small)["qwen3.block_count"] == 28

        # Same path, different content, different length.
        _write(small, pairs=[("general.type", "model", 8),
                             ("qwen3.block_count", 99, 4)])
        os.utime(small, ns=(time.time_ns() + 10**9, time.time_ns() + 10**9))
        assert ctxfit.gguf_model_info(small)["qwen3.block_count"] == 99

    def test_a_same_length_rewrite_is_still_reread(self, small):
        """Size alone would miss this one; mtime is what catches it."""
        ctxfit._INFO_CACHE.clear()
        assert ctxfit.gguf_model_info(small)["general.type"] == "model"
        _write(small, pairs=[("general.type", "model", 8),
                             ("qwen3.block_count", 7, 4)])
        later = time.time_ns() + 2 * 10**9
        os.utime(small, ns=(later, later))
        assert ctxfit.gguf_model_info(small)["qwen3.block_count"] == 7

    def test_the_cache_is_bounded(self, small, monkeypatch):
        """Unbounded, it is a header-sized dict per file for the life of the app.

        Written past the limit and read back, so this is a claim about the limit
        doing its job rather than about the constant being a number.
        """
        ctxfit._INFO_CACHE.clear()
        limit = ctxfit._INFO_CACHE_LIMIT
        for n in range(limit + 4):
            one = small.with_name(f"copy{n}.gguf")
            one.write_bytes(small.read_bytes())
            os.utime(one, ns=(time.time_ns() + n * 10**9,) * 2)
            ctxfit.gguf_model_info(one)
            assert len(ctxfit._INFO_CACHE) <= limit
        ctxfit._INFO_CACHE.clear()

    def test_the_two_reads_do_not_share_one_cache_entry(self, small):
        """**The collision, and the crash it hid.**

        `gguf_model_info` and `kv_width_disagreement` are two different reads of
        one file and the cache was keyed on `(path, mtime, size)` alone, so
        whichever ran first served the other its own answer. Read second, the
        shapes route got the metadata dict; `kv_width_disagreement` then sorted
        `(key, value)` pairs into `kv_width_from_shapes` and raised
        `TypeError: 'int' object is not iterable` on `("qwen3.block_count", 28)`.
        `local_llm._context_arg` catches that, so the cross-check raised and was
        swallowed on **every** startup - a check that passes by not running.

        **Both orders are asserted**, because a prefix only on one path is the
        half of this bug that still looks fixed.
        """
        info = ctxfit.gguf_model_info(small)
        assert ctxfit.kv_width_disagreement(small, info) is None, (
            "the metadata read answered the shapes read")

        ctxfit._INFO_CACHE.clear()
        # And the other way round, which a one-directional test never reaches.
        assert ctxfit.kv_width_disagreement(small, info) is None
        shapes = ctxfit._cached_header(gguf_header.read_tensor_shapes,
                                       small, "shapes")
        assert shapes["blk.0.attn_k.weight"] == [2048, 1024], (
            "the shapes read answered the metadata read")
        assert ctxfit.gguf_model_info(small) == info

    def test_an_unstatable_path_still_answers(self, small, monkeypatch):
        """No stat, no key - so it misses the cache rather than being cached
        against nothing, and the answer is still the file's."""
        ctxfit._INFO_CACHE.clear()
        real = gguf_header.cache_key

        def no_key(path):
            return None

        monkeypatch.setattr(gguf_header, "cache_key", no_key)
        try:
            assert ctxfit.gguf_model_info(small)["general.type"] == "model"
            assert ctxfit._INFO_CACHE == {}
        finally:
            monkeypatch.setattr(gguf_header, "cache_key", real)


class TestTheArraysAreReal:
    """The one behaviour change, and it has a purpose."""

    def test_a_scalar_head_count_still_answers_a_width(self):
        info = {"general.architecture": "qwen3",
                "qwen3.attention.head_count": 16,
                "qwen3.attention.head_count_kv": 8,
                "qwen3.embedding_length": 2048}
        assert ctxfit.metadata_kv_width(info) == 8 * (2048 // 16)

    def test_a_per_layer_list_now_refuses(self):
        """**Unreachable through the old route**, which is why it had no witness.

        `ReaderField.parts[-1]` on an array is its last element, so a per-layer
        `head_count_kv` arrived as a bare `8` and the guard below never fired.
        """
        info = {"general.architecture": "qwen3",
                "qwen3.attention.head_count": 16,
                "qwen3.attention.head_count_kv": [8, 4],
                "qwen3.embedding_length": 2048}
        assert ctxfit.metadata_kv_width(info) is None

    def test_the_shapes_route_agrees_with_the_header_on_a_real_model(self, small):
        """The cross-check `kv_width_disagreement` performs, on a file we built."""
        info = ctxfit.gguf_model_info(small)
        # The fixture declares a 1024-wide K projection and a header that says
        # head_count_kv 8 x head_dim 128 = 1024, so the two agree and there is
        # nothing to say. **A None here is "no comparison", not "no disagreement"**,
        # which is why the control below disagrees on purpose.
        assert ctxfit.kv_width_disagreement(small, info) is None

        # **The control**: claim a width the tensors do not have, and the sentence
        # must appear and must name both numbers. Without it the assertion above
        # passes on a reader that never compared anything.
        #
        # Both figures are the module's own arithmetic, not the literals typed
        # here: `metadata_kv_width` returns `head_count_kv * (embedding // heads)`
        # and `kv_width_from_shapes` returns the projection's `shape[1]`. The
        # first version asserted the *literal* `"999"` here, which could never be
        # in the sentence - the header's width is `999 x 128 = 127872`.
        liar = dict(info, **{"qwen3.attention.head_count_kv": 999})
        said = ctxfit.kv_width_disagreement(small, liar)
        assert said, "a header claiming 127872 against tensors of 1024 is a disagreement"
        assert "127872" in said, said
        assert "1024" in said, said

    def test_no_comparison_is_not_agreement(self, tmp_path):
        """`None` means *could not compare*. A caller reading it as agreement has a
        check that passes by not running."""
        assert ctxfit.kv_width_disagreement(tmp_path / "nope.gguf", {}) is None
        assert ctxfit.kv_width_disagreement(None, {
            "general.architecture": "qwen3"}) is None


class TestTheAdapterHeader:
    def test_a_declared_adapter_is_an_adapter(self, tmp_path):
        """The route that used to guess `parts[1]` and read `general.type` as
        `'97'` - which is ASCII for the first letter of `adapter`."""
        from gguf import GGUFValueType as T
        path = _write(tmp_path / "lora.gguf",
                      pairs=[("general.type", "adapter", T.STRING),
                             ("adapter.type", "lora", T.STRING)])
        assert adapter.is_adapter(path) is True
        assert adapter._header(path)["general.type"] == "adapter"

    def test_a_model_is_not_an_adapter(self, small):
        assert adapter.is_adapter(small) is False

    def test_an_unreadable_file_is_not_an_adapter(self, tmp_path):
        """Not an exception: a foreign file is not an adapter and is not a fault."""
        junk = tmp_path / "junk.gguf"
        junk.write_bytes(b"not a gguf" * 100)
        assert adapter.is_adapter(junk) is False
        assert adapter._header(junk) == {}

    def test_a_numeric_key_still_renders_as_text(self, tmp_path):
        """`general.quantization_version` is a u32 and reads `"4294967295"` - the
        contract `tests/test_adapter.py` pins, so it is asserted here too."""
        from gguf import GGUFValueType as T
        path = _write(tmp_path / "n.gguf",
                      pairs=[("general.quantization_version",
                              4294967295, T.UINT32)])
        assert adapter._header(path)["general.quantization_version"] == "4294967295"


@pytest.mark.skipif(not os.path.exists(REAL_MODEL),
                    reason="no local model file; CHRONOA_REAL_GGUF")
class TestAgainstTheRealModel:
    """The file the whole change was measured on, when it is on this machine."""

    def test_the_header_is_complete(self):
        info = gguf_header.read_metadata(REAL_MODEL)
        assert len(info) == 32, "a window this size holds every key"
        assert info["general.architecture"] == "qwen3"

    def test_every_tensor_is_here(self):
        assert len(gguf_header.read_tensor_shapes(REAL_MODEL)) == 310

    def test_it_is_far_faster_than_the_reader_it_replaces(self):
        """**The claim the module makes, timed on the real file.**

        A bare ratio with no control would pass on a slow machine and fail on a
        fast one; both sides are timed in the same process, so the ratio is what
        is asserted and the absolute numbers are printed for the record.
        """
        import gguf

        gguf_header.read_metadata(REAL_MODEL)          # warm the page cache
        best = min(_timed(gguf_header.read_metadata, REAL_MODEL) for _ in range(3))
        theirs = _timed(gguf.GGUFReader, REAL_MODEL)
        print(f"\n  header {best:.3f}s vs GGUFReader {theirs:.3f}s "
              f"({theirs / best:.0f}x)")
        assert best * 5 < theirs, "five times faster, on the real checkpoint"