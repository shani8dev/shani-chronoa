"""The Models panel counted cards, so it did not count the folder.

Rendered on this machine, before this fix: **151 labels, and not one mentioned a
LoRA, an adapter, or any file in the model folder that the panel does not
offer.** The tally row read:

    3 installed, 27 available - 3 of 30 models are on this machine

Three is right. The folder holds **ten** GGUF files. The other seven were:

| file | what it really is |
|---|---|
| `lumen-cortex-lora.gguf` | a LoRA **adapter** — the header says `general.type: adapter` |
| `lumen-executor-lora.gguf` | the same |
| `lumen-mouth-lora.gguf` | the same |
| `tool-q4_k_m.gguf` | a usable model from elsewhere; nothing here has checked it |
| `toolcall-q5_k_m.gguf` | the same |
| `Qwen3-0.6B-Base.Q8_0.gguf` | the same |
| `Qwen3-0.6B-heretic.Q8_0.gguf` | the same |
| `SmolVLM2-500M-Video-Instruct-Q8_0.gguf` | the same, with no `mmproj-` projector beside it |

**Two halves, opposite words.** Five load and are unoffered; three would break
`llama-server` if selected. One sentence covering all eight is wrong for all of
them, so there are two rows.

`modelfmt.catalog()` existed and had **no caller in the tree** — *"Every
model-shaped file under `roots`, with format, role and loadability"* — and it is
the only thing here that can answer for a file the catalogue has never heard of.

## What is deliberately not claimed

- **The tally row is not changed.** It is about the catalogue and it is correct.
  These rows are about the folder; one does not answer for the other.
- **The panel's recorded status word is not touched.** These rows carry their own
  dots via `common.status_row`, because folding "there are some other files" into
  the panel's status would turn a healthy panel red over a folder somebody
  deliberately filled.
- **Nothing is offered for switching.** These files are *named*, not made
  selectable: a `vouched=False` file is not something this build has checked, and
  adding a button that loads one would be a promise the panel cannot keep.
"""

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "usr/lib/shani-chronoa"))

gi = pytest.importorskip("gi")
gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gtk  # noqa: E402

Adw.init()

from shani_chronoa import local_llm  # noqa: E402
from shani_chronoa.gui.surfaces import models  # noqa: E402


def _gguf(path, **kv):
    """A readable GGUF carrying the header keys given."""
    gguf = pytest.importorskip("gguf")
    numpy = pytest.importorskip("numpy")
    from gguf.constants import GGUFValueType

    w = gguf.GGUFWriter(str(path), arch="qwen3")
    for key, value in kv.items():
        w.add_key_value(key, value, GGUFValueType.STRING)
    # Not decoration: a header-only file is 24 bytes and GGUFReader reads no
    # fields from it, so `identify` cannot see a format at all.
    w.add_tensor("blk.0.attn_q.weight", numpy.zeros((8, 8), dtype=numpy.float32))
    w.write_header_to_file()
    w.write_kv_data_to_file()
    w.write_tensors_to_file()
    w.close()
    return path


class _Config:
    def get(self, key, default=""):
        return str(default)

    def get_bool(self, key, default=False):
        return bool(default)

    def get_double(self, key, default=0.0):
        return float(default)

    def sense_allowed(self, name):
        return False


class _App:
    def __init__(self):
        self.config = _Config()
        self.window = None
        self.tts = None
        self.stt = None

    def activate_action(self, name, arg):
        pass


def _walk(node, out=None):
    out = [] if out is None else out
    if isinstance(node, Gtk.Widget):
        out.append(node)
    child = node.get_first_child()
    while child is not None:
        _walk(child, out)
        child = child.get_next_sibling()
    return out


def _panel(folder, monkeypatch):
    """**What the panel actually renders** for a model directory of `folder`.

    The first version of this file re-implemented the production split in the test
    as `_rows()`, which is a tautology: it would have passed with the real helper
    deleted. Every assertion here reads labels off the rendered widget instead.
    """
    monkeypatch.setattr(local_llm, "model_dir", lambda: folder)
    return " \n".join(w.get_text() for w in _walk(models.build(_App()))
                      if isinstance(w, Gtk.Label) and w.get_text())


def _only_shipped(folder):
    """A folder holding exactly the shipped tiers - nothing to report."""
    shipped = next(iter(local_llm.SPECS.values())).filename
    _gguf(folder / shipped,
          **{"general.type": "model", "general.basename": "Qwen3"})
    return shipped


def test_a_folder_of_only_shipped_models_adds_no_row(tmp_path, monkeypatch):
    """Nothing to report is **no row**, not a row saying nothing. A row that always
    renders is one more thing to read, and this one is added under a panel that
    already opens with a tally."""
    _only_shipped(tmp_path)

    said = _panel(tmp_path, monkeypatch)

    assert "other model file" not in said, said[:400]
    assert "cannot be run as a model" not in said, said[:400]


def test_unoffered_but_loadable_is_named(tmp_path, monkeypatch):
    """**The half that was invisible.** A model that works and that the panel does
    not offer is the one a person is silently missing."""
    _only_shipped(tmp_path)
    _gguf(tmp_path / "toolcall-q5_k_m.gguf",
          **{"general.type": "model", "general.basename": "Qwen3"})

    said = _panel(tmp_path, monkeypatch)

    assert "1 other model file in this folder" in said, said[:500]
    assert "toolcall-q5_k_m.gguf" in said, said[:500]
    # named as loadable, and as unchecked - it did not come from this build
    assert "nothing here has checked them" in said, said[:500]


def test_an_adapter_is_reported_as_unrunnable_not_as_a_model(tmp_path, monkeypatch):
    """**The other half**, and the over-blocking direction. An adapter is not a
    model: loading one with `llama-server` does not produce a weaker model, it
    produces nonsense."""
    _only_shipped(tmp_path)
    _gguf(tmp_path / "lumen-cortex-lora.gguf",
          **{"general.type": "adapter", "adapter.type": "lora"})

    said = _panel(tmp_path, monkeypatch)

    assert "1 file in this folder cannot be run as a model" in said, said[:500]
    assert "lumen-cortex-lora.gguf" in said, said[:500]
    assert "other model file" not in said, (
        "an adapter is being counted among the loadable ones: " + said[:500])


def test_both_halves_appear_at_once_and_are_not_merged(tmp_path, monkeypatch):
    """One sentence covering both would be wrong for both, so there are two rows."""
    _only_shipped(tmp_path)
    _gguf(tmp_path / "toolcall-q5_k_m.gguf",
          **{"general.type": "model", "general.basename": "Qwen3"})
    _gguf(tmp_path / "lumen-cortex-lora.gguf",
          **{"general.type": "adapter", "adapter.type": "lora"})

    said = _panel(tmp_path, monkeypatch)

    assert "1 other model file in this folder" in said, said[:600]
    assert "1 file in this folder cannot be run as a model" in said, said[:600]
    assert "lumen-cortex-lora.gguf" not in said.split("other model file")[0][-160:], (
        "the adapter is named in the loadable row")


def test_an_unreadable_file_is_not_an_empty_folder(tmp_path, monkeypatch):
    """`unreadable` is kept separate from `present` on purpose: a permission
    problem is not a format problem, and a file you cannot open is not a file that
    does not exist."""
    _only_shipped(tmp_path)
    blocked = tmp_path / "locked.gguf"
    blocked.write_bytes(b"GGUF")
    blocked.chmod(0o000)
    try:
        said = _panel(tmp_path, monkeypatch)
        assert "cannot be run as a model" in said, said[:500]
        assert "locked.gguf" in said, said[:500]
    finally:
        blocked.chmod(0o644)


def test_the_tally_row_is_untouched(tmp_path, monkeypatch):
    """It is about the catalogue and it is correct. One count does not answer for
    the other, and folding "there are other files" into it would make it wrong."""
    _only_shipped(tmp_path)
    _gguf(tmp_path / "toolcall-q5_k_m.gguf",
          **{"general.type": "model", "general.basename": "Qwen3"})

    said = _panel(tmp_path, monkeypatch)

    tally = next((t for t in said.split("\n") if "available to add" in t), "")
    assert tally, "the tally row disappeared"
    assert "toolcall-q5_k_m" not in tally, (
        f"an unoffered file was counted in the catalogue tally: {tally!r}")


def test_the_panel_status_word_is_not_turned_red(tmp_path, monkeypatch):
    """These rows carry their own dots via `common.status_row`. Folding "there are
    some other files" into the panel's recorded status would turn a healthy panel
    red over a folder somebody deliberately filled - and the sidebar dot reads
    exactly that word, via `page.status`."""

    def status_of(folder):
        monkeypatch.setattr(local_llm, "model_dir", lambda: folder)
        panel = models.build(_App())
        assert callable(getattr(panel, "status", None)), (
            "the page no longer exposes its status word, so this claim is "
            "unverifiable and should be re-checked rather than assumed")
        return panel.status()

    _only_shipped(tmp_path)
    before = status_of(tmp_path)

    _gguf(tmp_path / "lumen-cortex-lora.gguf",
          **{"general.type": "adapter", "adapter.type": "lora"})
    after = status_of(tmp_path)

    assert after == before, (
        f"an adapter in the folder changed the panel's health word {before!r} -> "
        f"{after!r}; the sidebar dot would show a healthy panel as needing "
        f"attention over files the user put there deliberately")


def test_catalog_now_has_a_caller():
    """The smallest claim that was false, and the reason this file exists."""
    root = ROOT / "usr/lib/shani-chronoa"
    callers = [p for p in root.rglob("*.py")
               if "modelfmt.catalog(" in p.read_text(encoding="utf-8", errors="replace")
               and p.name != "modelfmt.py"]
    assert callers, "modelfmt.catalog() still has no caller"
