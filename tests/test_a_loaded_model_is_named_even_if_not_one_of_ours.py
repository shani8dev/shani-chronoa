"""A shared or imported model was rendered as "no model is loaded".

`model_registry.active_entry()` had **no caller in the tree**. Its own docstring
calls it the fix to a measured defect, and the defect was still live when this
file was written - reproduced before any edit:

    current.gguf -> someone-elses-model-Q4_K_M.gguf      (not in SPECS)
    local_llm.active()  -> ''      so every reader renders ABSENT
    active_entry()      -> {'name': 'someone-elses-model-Q4_K_M.gguf',
                            'vouched': False}

`local_llm.active()` returns a `SPECS` key or `""`, so a model this build does
not ship - imported, shared, handed over by somebody - resolved to nothing. The
panel said no model was loaded while one was loaded and answering.

The cards cannot show it either: they come from `local_llm.TIERS`, so an
imported model has no card to mark. That is why this needed a row of its own
rather than a flag on a card.

**The vouched flag is reported, not turned into a verdict.** `vouched=False` means
"no hash was shipped for this file" - a fact about the file, not a reason to deny
it. The detail says so, and says it is loaded and answering, so the sentence
cannot be read as a warning to act on.

Both readers are pointed at one directory by patching `local_llm.model_dir()`,
which is what `active()` and `active_entry()` both resolve through - so the test
exercises the real path rather than a hand-built substitute.
"""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "usr/lib/shani-chronoa"))

gi = pytest.importorskip("gi")
gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gtk  # noqa: E402

Adw.init()

from shani_chronoa import local_llm, model_registry  # noqa: E402
from shani_chronoa.gui.surfaces import models as surface  # noqa: E402


def _walk(node, out=None):
    out = [] if out is None else out
    if isinstance(node, Gtk.Widget):
        out.append(node)
    child = node.get_first_child()
    while child is not None:
        _walk(child, out)
        child = child.get_next_sibling()
    return out


def _said(page):
    out = [w.get_text() for w in _walk(page)
           if isinstance(w, Gtk.Label) and w.get_text()]
    for w in _walk(page):
        if isinstance(w, Gtk.Label) and w.get_text():
            out.append(w.get_text())
    return out


class _Config:
    def get(self, key, default=""):
        return str(default)

    def get_bool(self, key, default=False):
        return bool(default)

    def get_double(self, key, default=0.0):
        return float(default)


class _App:
    def __init__(self):
        self.config = _Config()
        self.window = None
        self.tts = None
        self.stt = None
        self.hardware = None

    def activate_action(self, name, arg):
        pass


@pytest.fixture
def model_dir(tmp_path, monkeypatch):
    """A model directory holding one file this build does not ship."""
    real = tmp_path / "someone-elses-model-Q4_K_M.gguf"
    real.write_bytes(b"GGUF" + b"\0" * 2048)
    (tmp_path / "current.gguf").symlink_to(real.name)
    monkeypatch.setattr(local_llm, "model_dir", lambda: tmp_path)
    return tmp_path


def test_the_defect_reproduces_without_the_row(model_dir):
    """**The defect, verbatim, measured rather than believed.**

    If `active()` ever learns to name an imported model, this fails and asks for
    the test to be rewritten - not left green because the premise quietly
    stopped being true.
    """
    assert local_llm.active() == "", (
        "`local_llm.active()` now answers for an unlisted model, so the defect "
        "has gone for a different reason and this test needs rewriting")

    entry = model_registry.active_entry()
    assert entry, "active_entry() found nothing where the file plainly exists"
    assert "someone-elses-model" in (entry.get("name") or entry.get("filename")), entry
    assert entry["vouched"] is False, entry


def test_the_panel_names_a_loaded_model_we_do_not_ship(model_dir):
    """The row the fix adds, read off the built panel."""
    said = _said(surface.build(_App()))
    loaded = [s for s in said if s.startswith("Loaded:")]

    assert loaded, f"the panel does not name what is loaded. Rows: {said[:6]}"
    assert "someone-elses-model" in loaded[0], (
        f"the loaded row names the wrong file: {loaded[0]!r}")


def test_an_unvouched_model_is_reported_not_denied(model_dir):
    """**The distinction that matters.** No shipped hash is a fact about the
    file, not a verdict on it, and the row must not read as a warning."""
    # **Scoped to the row, not the whole page.** The first version asserted
    # `"no model" not in text` over every string on the panel, and matched
    # "Set Chronoa up models" - which is a button, says nothing about what is
    # loaded, and failed the test for being there. A panel-wide substring test
    # measures the panel; this measures the row it is about.
    said = _said(surface.build(_App()))
    row = next((s for s in said if s.startswith("Loaded:")), "")
    assert row, f"no loaded row at all: {said[:6]}"
    detail = next((s for s in said if "shipped a hash" in s or "this build ships" in s), "")

    assert "not a model this build shipped a hash for" in detail, detail[:160]
    assert "It is loaded and it is what answers." in detail, (
        f"the row leaves the reader thinking something is wrong with the file: "
        f"{detail!r}")
    assert "no model" not in row.lower(), (
        f"the loaded row itself denies the file: {row!r}")


def test_a_model_we_do_ship_reads_as_ours(tmp_path, monkeypatch):
    """**The control.** The row is not only for foreign files - a shipped tier
    that is loaded must be named too, or this reads as a warning banner."""
    shipped = tmp_path / "Qwen3-1.7B-Q4_K_M.gguf"
    shipped.write_bytes(b"GGUF" + b"\0" * 2048)
    (tmp_path / "current.gguf").symlink_to(shipped.name)
    monkeypatch.setattr(local_llm, "model_dir", lambda: tmp_path)

    text = " ".join(_said(surface.build(_App())))
    assert "one of the models this build ships" in text, text[:200]