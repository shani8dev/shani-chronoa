"""Changing the model on the Model page changes what the review page lists.

Found by an AI agent driving the real wizard through shani-testbed (2026-10-23):
it picked Small, the download fetched Qwen3 0.6B, and the review page had said
"Language model - qwen3-1.7b" with its 1.1 GB size all along. The brain (and
ears) page recorded its plan item once, from whichever radio was selected when
the page was built; `setup_brain(chosen())` read the radio at run time, so the
bytes followed the choice and the list did not.
"""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "usr/lib/shani-chronoa"))

gi = pytest.importorskip("gi")
gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gio, Gtk  # noqa: E402

Adw.init()

from shani_chronoa import local_llm, setup_wizard  # noqa: E402


def _walk(node, out=None):
    out = [] if out is None else out
    out.append(node)
    child = node.get_first_child()
    while child is not None:
        _walk(child, out)
        child = child.get_next_sibling()
    return out


def test_review_names_the_model_the_radio_now_selects():
    win = setup_wizard.build_window(Gtk.Application(
        application_id="test.wizard.planradio", flags=Gio.ApplicationFlags.NON_UNIQUE))
    pages = {p.get_tag(): p for p in _walk(win) if isinstance(p, Adw.NavigationPage)}
    picker, review = pages["model-picker"], pages["review"]
    # rows of the "Choose a model" group, in TIERS order
    checks = [n for n in _walk(picker) if isinstance(n, Gtk.CheckButton)]
    keys = [spec.key for spec, _label, _ram in local_llm.TIERS]
    if len(checks) < len(keys):
        pytest.skip("this machine already has a verified model, so the picker shows no list")
    active = next(i for i, c in enumerate(checks[:len(keys)]) if c.get_active())
    other = (active + 1) % len(keys)
    checks[other].set_active(True)
    review.emit("showing")          # what navigating to the review page fires
    titles = [r.get_title() for r in _walk(review) if isinstance(r, Adw.ActionRow)]
    brain = [t for t in titles if t.startswith("Language model - ")]
    assert brain == [f"Language model - {keys[other]}"], \
        f"picked {keys[other]!r}, review lists {brain}"
