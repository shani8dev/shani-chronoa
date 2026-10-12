"""渡劫: an adapter is checked against its base before anything is started.

`llama-server` does not do this and **segfaults** when it fails to - measured on
the pair this repository now has, where the server dies with a hint nobody sees
in a log they are not reading. So the check is here, and it is cheap.

Three defects this file's own tests found while it was written, all in the
header reader and all of the same kind - a confident wrong answer rather than an
error:

1. `ReaderField.parts` is **five** long on the installed gguf-py, not two:
   `[type-table offset, KEY bytes, value-type index, length, VALUE bytes]`.
   Guessing two meant `general.type` read as `'97'`.
2. **`tolist()` on the byte memmap yields a list of ints**, so a
   `isinstance(raw, str)` test after it never fires and `str(raw)` prints the
   ASCII of the first byte - 97 is ASCII for `a`, the first letter of `adapter`.
3. Decoding "a list of ints" as bytes raises on a u32 field, which is also a
   list of ints. Anything outside 0..255 is a number.
"""
from pathlib import Path

import numpy as np
import pytest

from shani_chronoa import adapter


def _gguf_with_metadata(tmp_path, name, pairs, tensors=(), numeric=()):
    """A real GGUF holding the given metadata and tensors.
    `dims` are **reader-reported ggml axes** - `[in, out]` for a weight and
    `[in, rank]` for `lora_a` - because that is what the module reads.

    **Written by `GGUFWriter`, not by hand.** A hand-built one took three
    attempts and failed differently each time - a missing tensor payload
    (`cannot reshape array of size 0`), then a relative-vs-absolute offset
    mismatch (`size must be a divisor`) - and each failure was a defect in the
    fixture being mistaken for a defect in the reader. The writer produces the
    layout the reader actually consumes, which is the entire point: these tests
    are about what the reader *interprets*, not about how the format packs.
    """
    from gguf import GGUFValueType, GGUFWriter
    path = tmp_path / name
    # `GGUFWriter`'s first argument is the *architecture*, and it writes a
    # `general.architecture` key of its own - so the first pair here is the
    # architecture and its value must not be passed as a key as well. That
    # double-write is why two tests read `general.type` as absent from a file
    # that plainly contains it.
    writer = GGUFWriter(path, pairs[0][1] or "qwen3")
    for key, value in pairs[1:]:
        writer.add_key_value(key, value, GGUFValueType.STRING)
    for key, value in numeric:
        writer.add_key_value(key, int(value), GGUFValueType.UINT32)
    for tname, dims in tensors:
        count = 1
        for d in dims:
            count *= int(d)
        # **The signature is `(name, tensor_shape, tensor_dtype, tensor_nbytes)`**
        # and `tensor_shape` is the *numpy* shape, so gguf-py's `add_tensor`
        # takes it and reverses it internally. Guessing the parameter order gave
        # `unexpected keyword argument` and, before that, a shape that packed
        # transposed - both in the fixture, both misread as reader defects.
        # `dims` are **reader-reported ggml axes**, because that is what the
        # module reads and what a real GGUF holds - so the numpy array is built
        # with them reversed, which is what `add_tensor` expects.
        #
        # Three wrong turns here, all fixture defects that read as reader
        # defects: `add_tensor_info` records no data and `write_tensors_to_file`
        # then asserts; the numpy shape was passed unreversed, packing every
        # tensor transposed; and the reversal was "verified" against this
        # synthetic file rather than against a real GGUF, which disagreed with
        # it. The last one cost a change to correct working module code.
        writer.add_tensor(tname, np.zeros(count, dtype=np.float32).reshape(
            [int(d) for d in reversed(dims)]))
    writer.write_header_to_file()
    writer.write_kv_data_to_file()
    writer.write_tensors_to_file()
    writer.close()
    return path


def test_an_adapter_is_identified_by_its_own_declaration(tmp_path):
    """**Not by its name and not by its size.**

    Measured: `Qwen3-0.6B-climateguard-lora-16-GGUF` publishes files ending
    `-lora-16` at 1142.7 MiB in f16 - a whole 0.6B model - with no lora_a
    tensors at all. A name or size test hands llama-server a second model.
    """
    path = _gguf_with_metadata(tmp_path, "impostor.gguf",
                               [("general.architecture", "qwen3"),
                                ("general.type", "model"),
                                ("general.name", "Qwen3-0.6B-lora-16")])
    assert adapter.is_adapter(path) is False


def test_an_adapter_that_says_so_is_one(tmp_path):
    path = _gguf_with_metadata(tmp_path, "real.gguf",
                               [("general.architecture", "qwen3"),
                                ("general.type", "adapter"),
                                ("adapter.type", "lora")],
                               tensors=[("blk.0.attn_q.weight.lora_a", [1024, 16])])
    assert adapter.is_adapter(path) is True


def test_a_numeric_field_does_not_crash_the_reader(tmp_path):
    """The u32 case, and it was **not a test at all** in its first version.

    The field was written as the *string* `"2"`, so the reader never met a
    u32 and the byte-decode was never reached - and the mutation that removed
    the `0 <= v <= 255` guard left the file green. `GGUFWriter` refused the
    string outright once it was given `GGUFValueType.UINT32`, which is how the
    fixture was found to be describing a field it did not contain.

    The value is now a real number larger than 255, which is the only thing
    that makes `bytes()` raise.
    """
    path = _gguf_with_metadata(tmp_path, "with-numbers.gguf",
                               [("general.architecture", "qwen3"),
                                ("general.type", "adapter"),
                                ],
                               numeric=[("adapter.lora.alpha", 32),
                                        ("general.quantization_version", 4294967295)],
                               tensors=[("blk.0.attn_q.weight.lora_a", [1024, 16])])
    assert adapter.is_adapter(path) is True
    # And the number really is a number, not text that happened to survive.
    assert adapter._header(path)["general.quantization_version"] == "4294967295"


def test_a_file_that_is_not_gguf_says_so_rather_than_being_unknown(tmp_path):
    _gguf_with_metadata(tmp_path, "base.gguf",
                        [("general.architecture", "qwen3"),
                         ("general.type", "model")])
    path = tmp_path / "adapter.safetensors"
    path.write_bytes(b"\x00\x01\x02" + b"x" * 64)
    with pytest.raises(adapter.AdapterMismatch) as caught:
        adapter.mismatches(path, tmp_path / "base.gguf")
    said = str(caught.value)
    assert "not a GGUF file" in said
    # And it must name the missing converter, because that is the actionable half.
    assert "safetensors" in said or "convert" in said


def test_an_adapter_with_no_lora_tensors_is_refused(tmp_path):
    """A published adapter that is really a whole model looks exactly like this,
    and llama.cpp would accept the file and do nothing useful with it."""
    _gguf_with_metadata(tmp_path, "base.gguf",
                        [("general.architecture", "qwen3"),
                         ("general.type", "model")],
                        tensors=[("blk.0.attn_q.weight", [1024, 1024])])
    path = _gguf_with_metadata(tmp_path, "empty-adapter.gguf",
                               [("general.type", "adapter")],
                               tensors=[("blk.0.attn_q.weight", [1024, 1024])])
    with pytest.raises(adapter.AdapterMismatch) as caught:
        adapter.mismatches(path, tmp_path / "base.gguf")
    assert "lora_a" in str(caught.value)


def test_a_mismatch_names_the_tensor_the_widths_and_says_why(tmp_path):
    """The sentence a person acts on. It has to carry a tensor name, both
    widths, and the fact that these are different models - not 'incompatible'."""
    base = _gguf_with_metadata(tmp_path, "base.gguf",
                               [("general.architecture", "qwen3"),
                                ("general.type", "model")],
                               tensors=[("blk.0.ffn_down.weight", [1024, 3072])])
    wrong = _gguf_with_metadata(tmp_path, "wrong.gguf",
                                [("general.architecture", "qwen3"),
                                 ("general.type", "adapter")],
                                tensors=[("blk.0.ffn_down.weight.lora_a", [2048, 16])])
    reasons = adapter.mismatches(wrong, base)
    assert reasons, "a wrong fan-in must produce a reason"
    said = reasons[0]
    assert "blk.0.ffn_down.weight" in said, said
    assert "2048" in said and "1024" in said, said
    assert "different models" in said, said


def test_a_matching_adapter_reports_nothing_and_check_returns_none(tmp_path):
    """The control for the case above: a scan finding nothing satisfies a test
    that only asserts reasons are produced, so the compatible case is asserted
    explicitly."""
    base = _gguf_with_metadata(tmp_path, "base.gguf",
                               [("general.architecture", "qwen3"),
                                ("general.type", "model")],
                               tensors=[("blk.0.ffn_down.weight", [1024, 3072])])
    fits = _gguf_with_metadata(tmp_path, "fits.gguf",
                               [("general.architecture", "qwen3"),
                                ("general.type", "adapter")],
                               tensors=[("blk.0.ffn_down.weight.lora_a", [1024, 16])])
    assert adapter.mismatches(fits, base) == []
    assert adapter.check(fits, base) is None


def test_a_target_the_base_does_not_have_is_reported_separately(tmp_path):
    """A tensor the base has never heard of is a different failure from a wrong
    width, and the remedy differs."""
    base = _gguf_with_metadata(tmp_path, "base.gguf",
                               [("general.architecture", "qwen3"),
                                ("general.type", "model")])
    stray = _gguf_with_metadata(tmp_path, "stray.gguf",
                                [("general.architecture", "qwen3"),
                                 ("general.type", "adapter")],
                                tensors=[("blk.99.attn_q.weight.lora_a", [1024, 16])])
    said = " ".join(adapter.mismatches(stray, base))
    assert "not in the base" in said, said
    assert "blk.99" in said, said


def test_scaling_to_zero_is_refused_so_the_file_stays_visible(tmp_path):
    """`--lora-scaled path:0` loads the adapter and applies nothing, which scores
    the base and reports it as the adapter."""
    with pytest.raises(ValueError) as caught:
        adapter.server_args(Path("a.gguf"), Path("b.gguf"), 0.0)
    assert "argv" in str(caught.value)


def test_the_argv_is_the_scaled_form(tmp_path):
    argv = adapter.server_args(Path("/x/a.gguf"), Path("/y/base.gguf"), 0.25)
    assert argv == ["--lora-scaled", "/x/a.gguf:0.25"]


def test_the_module_does_not_load_lora_init_without_apply(tmp_path):
    """That flag loads an adapter and applies nothing, which is the same result
    as the base scoring itself and calling it the adapter."""
    import inspect
    said = inspect.getsource(adapter.server_args)
    assert "lora-init-without-apply" not in said.split('"""')[-1], (
        "the flag must be named only to explain why it is not used")
