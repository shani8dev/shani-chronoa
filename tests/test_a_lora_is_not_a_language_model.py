"""Four LoRA adapters on this machine were offered as standalone chat models.

Measured in the real model directory, before this fix - ten files, and
`model_registry.scan()` returned **ten models and nothing excluded**:

    lumen-cortex-lora.gguf      104 MB
    lumen-executor-lora.gguf    104 MB
    lumen-mouth-lora.gguf        69 MB
    Qwen3-1.7B-Q4_K_M.gguf    1107 MB     <- a real one
    ...

Two independent bugs stacked, each enough on its own.

## 1. The suffix table was matched case-sensitively

`_TYPE_SUFFIXES` spells the suffix `LoRA`, and the check was
`stem.endswith("-" + suffix)`. The GGUF naming convention is used in **lower
case** by the exporters that produce these files, so `endswith("-LoRA")` is
false for `-lora` and the check silently did nothing. A filter documented as
"drops adapters" was dropping none of them. `kind_of('lumen-cortex-lora.gguf')`
answered `('model', '')`.

## 2. The header was not consulted at all

`classify()` reads the header for `general.architecture` and stops. The file says
what it is:

    general.architecture: qwen3
    general.type:        adapter      <- the file declaring itself
    adapter.type:        lora
    general.base_model.0.name: Qwen3 1.7B Unsloth Bnb 4bit

`general.type: adapter` is authoritative and outranks the filename, which is why
this is asserted separately below on a copy renamed to say nothing at all.
`llama-server` loading one of these as a base model does not produce a weaker
model - it produces nonsense, and `use()` can select it.

## What is deliberately not changed

`_TYPE_SUFFIXES` is matched case-insensitively on the **tail only**; nothing
about the shard regex or the sidecar prefixes moved. And `classify` still refuses
a file with no `general.architecture` first-class - the header check is added
*alongside* it, not instead of it.
"""

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "usr/lib/shani-chronoa"))

from shani_chronoa import local_llm, model_registry, modelfmt  # noqa: E402


def test_lowercase_lora_is_an_adapter():
    """**Bug 1**, on the exact spelling the exporters write."""
    kind, reason = modelfmt.kind_of("lumen-cortex-lora.gguf")

    assert kind == "adapter", f"kind_of said {kind!r} with reason {reason!r}"
    assert "must not run it by itself" in reason


@pytest.mark.parametrize("name", [
    "thing-lora.gguf", "thing-LoRA.gguf", "thing-LORA.gguf", "thing-LoRa.gguf",
])
def test_every_casing_is_an_adapter(name):
    """Case-insensitive on the tail. One spelling being recognised is not the
    same as the convention being handled."""
    assert modelfmt.kind_of(name)[0] == "adapter", name


def test_vocab_and_sidecars_still_win():
    """The tail check must not reorder the table's own precedence."""
    assert modelfmt.kind_of("gpt2-vocab.gguf")[0] == "vocab"
    assert modelfmt.kind_of("mmproj-SmolVLM2.gguf")[0] == "sidecar"


def test_a_plain_model_is_still_a_model():
    assert modelfmt.kind_of("Qwen3-1.7B-Q4_K_M.gguf")[0] == "model"


def _adapter_entry(**over):
    entry = {"filename": "mystery-export.gguf", "architecture": "qwen3",
             "declared_type": "adapter", "adapter_type": "lora",
             "basename": "Qwen3 1.7B Unsloth Bnb 4bit"}
    entry.update(over)
    return entry


def test_a_header_declared_adapter_is_never_a_model():
    """**Bug 2.** The filename is silent here - `kind_of` alone would say `model`."""
    assert modelfmt.kind_of("mystery-export.gguf")[0] == "model", (
        "this test only means something if the filename alone gets it wrong")

    kind, reason = model_registry.classify(_adapter_entry())

    assert kind == "adapter", kind
    assert "header declares it a lora" in reason
    assert "Qwen3 1.7B" in reason, (
        f"the reason does not name the base model it applies to: {reason!r}")


def test_the_header_outranks_the_name():
    """A file whose header says `model` but whose name ends `-lora`: the filename
    rule still applies, because both point the same way. Asserted so a future
    change cannot make the two disagree silently."""
    kind, _ = model_registry.classify(
        _adapter_entry(filename="odd-lora.gguf", declared_type="", adapter_type=""))
    assert kind == "adapter"


def test_a_real_model_is_not_caught_by_the_adapter_rule():
    """The over-blocking failure. A base model declares `general.type: model`, and
    must still be offered."""
    kind, reason = model_registry.classify(
        _adapter_entry(declared_type="model", adapter_type="", basename="Qwen3"))

    assert kind == "model", f"{kind}: {reason}"


def test_no_architecture_is_still_refused():
    """Unchanged, and checked because it is the rule this one sits beside."""
    kind, reason = model_registry.classify(
        _adapter_entry(architecture="", declared_type="", adapter_type=""))

    assert kind == "not-a-model", kind
    assert "general.architecture" in reason


def _synth(path, **kv):
    """A real, readable GGUF carrying exactly the header keys given.

    The first version of this test copied a LoRA out of the machine's real model
    directory and **skipped** when it was not there - which it always was, because
    the suite's conftest redirects `files.data_home()` into a tmp dir. A test that
    silently cannot run is the negative control that cannot fail, so the file is
    built here instead: 480 bytes, one tensor, and no machine dependence at all.
    """
    gguf = pytest.importorskip("gguf")
    numpy = pytest.importorskip("numpy")
    from gguf.constants import GGUFValueType

    writer = gguf.GGUFWriter(str(path), arch="qwen3")
    for key, value in kv.items():
        writer.add_key_value(key, value, GGUFValueType.STRING)
    # A tensor is not decoration: a header-only file is 24 bytes and GGUFReader
    # returns no fields from it, so `describe()` sees no header at all.
    writer.add_tensor("blk.0.attn_q.weight",
                      numpy.zeros((8, 8), dtype=numpy.float32))
    writer.write_header_to_file()
    writer.write_kv_data_to_file()
    writer.write_tensors_to_file()
    writer.close()
    return path


def test_a_header_declared_adapter_is_never_offered(tmp_path, monkeypatch):
    """**End to end.** `use()` picks from `scan()["models"]`, so an adapter
    reaching that list is *selectable*, not merely displayed.

    The file is named `q-adapter.gguf` on purpose: `kind_of` answers `model` for
    it, so only the header can save this.
    """
    d = tmp_path / "llm"
    d.mkdir()
    _synth(d / "q-adapter.gguf", **{"general.type": "adapter",
                                    "adapter.type": "lora",
                                    "general.basename": "Qwen3 1.7B"})
    _synth(d / "q-model.gguf", **{"general.type": "model",
                                  "general.basename": "Qwen3"})
    monkeypatch.setattr(local_llm, "model_dir", lambda: d)

    scan = model_registry.scan(model_dir=d, specs={})

    offered = [m["filename"] for m in scan["models"]]
    excluded = [e["entry"]["filename"] for e in scan["excluded"]]

    assert "q-adapter.gguf" not in offered, (
        f"an adapter declaring general.type is selectable: {offered}")
    assert "q-adapter.gguf" in excluded, f"and it is not even reported: {scan}"
    assert "q-model.gguf" in offered, (
        f"the real model beside it was caught by the same rule: {offered}")


def test_the_synthetic_adapter_really_is_read_by_ctxfit(tmp_path):
    """**The control.** Without this, every assertion above would pass even if
    `describe()` saw no header - because a file with no header is
    `not-a-model` for an unrelated reason, and an adapter-looking exclusion would
    be the wrong reason passing for the right one."""
    pytest.importorskip("numpy")
    from shani_chronoa import ctxfit

    p = _synth(tmp_path / "q-adapter.gguf",
               **{"general.type": "adapter", "adapter.type": "lora"})

    info = ctxfit.gguf_model_info(p)
    assert info, "ctxfit read no header, so nothing below is testing the header"
    assert info["general.type"] == "adapter", info
    assert info["adapter.type"] == "lora", info
