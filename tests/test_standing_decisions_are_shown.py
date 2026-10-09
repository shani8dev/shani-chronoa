"""What Chronoa deliberately does not do is shown in the app, and matches the doc.

ARCHITECTURE-TARGET.md Part 4 lists the standing decisions (no generic shell,
no MCP client, no raw input capture, ...). They lived only in that document,
so the app could not tell "not built yet" from "will not be built". They are
data in `organism.STANDING_DECISIONS` now, rendered by the Inventory panel, and
this file fails when the document and the data drift apart.
"""

import re
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "usr/lib/shani-chronoa"))

gi = pytest.importorskip("gi")
gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gtk  # noqa: E402

Adw.init()


def _part4_terms() -> list:
    text = (REPO / "ARCHITECTURE-TARGET.md").read_text(encoding="utf-8")
    start = text.index("## Part 4")
    end = text.index("\n## ", start + 1)
    rows = [line for line in text[start:end].splitlines()
            if line.startswith("| ") and not line.startswith("| Diagram says")
            and not set(line) <= set("|- ")]
    return [re.sub(r"\*\*", "", row.split("|")[1]).strip() for row in rows]


def test_the_document_table_is_read():
    """Control: the parser finds the table, so the next test can fail."""
    assert len(_part4_terms()) >= 8, _part4_terms()


def test_every_decision_in_the_document_is_in_the_data():
    from shani_chronoa import organism
    # The document writes "<diagram box>: <term>"; the data keeps the term.
    # Matched on the term's first word, so a reworded reason does not break it
    # but a row added to (or dropped from) the table does.
    firsts = {re.findall(r"[a-z0-9]+", term.lower())[0]
              for term, *_ in organism.STANDING_DECISIONS}
    missing = [t for t in _part4_terms()
               if re.findall(r"[a-z0-9]+", t.split(":", 1)[-1].lower())[0] not in firsts]
    assert not missing, f"Part 4 rows with no entry in STANDING_DECISIONS: {missing}"
    assert len(organism.STANDING_DECISIONS) == len(_part4_terms())


def test_the_inventory_panel_lists_them():
    from shani_chronoa import organism
    from shani_chronoa.gui.surfaces import inventory

    def walk(node, out):
        out.append(node)
        child = node.get_first_child()
        while child is not None:
            walk(child, out)
            child = child.get_next_sibling()
        return out

    page = inventory.build(None)
    rows = [n for n in walk(page, []) if "standing-decision" in n.get_css_classes()]
    assert len(rows) == len(organism.STANDING_DECISIONS)
    said = " ".join(n.get_label() for n in walk(page, []) if isinstance(n, Gtk.Label))
    assert "Deliberately not built" in said and "Run any shell command" in said


def _generation_row(monkeypatch, gpu, drivers):
    from shani_chronoa import organism
    from shani_chronoa.gui.surfaces import inventory
    monkeypatch.setattr(organism, "compute_gpu", lambda: (gpu, drivers))

    def walk(node, out):
        out.append(node)
        child = node.get_first_child()
        while child is not None:
            walk(child, out)
            child = child.get_next_sibling()
        return out

    page = inventory.build(None)
    row = next(n for n in walk(page, []) if "standing-decision" in n.get_css_classes()
               and "video" in (n.get_tooltip_text() or "").lower())
    return row.get_tooltip_text()


def test_generation_is_a_gap_not_a_refusal_with_a_compute_gpu(monkeypatch):
    said = _generation_row(monkeypatch, "amdgpu", ["amdgpu"])
    assert "possible on this machine" in said and "not built yet" in said, said


def test_generation_stays_out_of_scope_on_an_integrated_gpu(monkeypatch):
    """This development machine: an Intel i915 is a GPU, and not one for this."""
    said = _generation_row(monkeypatch, None, ["i915"])
    assert "out of scope on this hardware" in said and "i915" in said, said


def test_only_compute_drivers_count():
    from shani_chronoa import organism
    assert "i915" not in organism.COMPUTE_GPU_DRIVERS
    assert {"nvidia", "amdgpu"} <= organism.COMPUTE_GPU_DRIVERS
