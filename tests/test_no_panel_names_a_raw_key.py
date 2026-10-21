"""No person-facing panel names a gsettings key.

**A key is not a word any screen shows.** "Enable
'phone-control-enabled' in Settings" sends a person hunting for a switch
spelled a way no screen ever spells it - the row in front of them is
titled *"Let Chronoa use your paired phone"*, and that is the one owner
of the wording (`capabilities.gate_label()`).

Measured 2026-10-20 by building every panel: **nine of the 38 named a
raw key on screen**, through five different routes - a panel's own
sentence, a skill's refusal shown through a panel, a module's refusal,
the anatomy's note, and a table of switch names that had no rows to
name them after.

**Two panels are exempt, and the exemption is the point.** The Skills
panel and the Workbench show *the model's own registry* - the same
description the model is handed - and a description names its key on
purpose, because the model needs the exact spelling. That is a different
population from a sentence a person reads, and the two are kept apart
here rather than confused.
"""

from __future__ import annotations

import os
import re
import sys

import pytest

_REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(_REPO, "usr", "lib", "shani-chronoa"))

gi = pytest.importorskip("gi")
gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
Gtk = pytest.importorskip("gi.repository.Gtk", reason="GTK 4 is not installed")
Adw = pytest.importorskip("gi.repository.Adw", reason="libadwaita is not installed")

Adw.init()

from shani_chronoa.gui import surfaces  # noqa: E402

#: A gsettings key, as it is spelled in the schema: `something-enabled`.
#: **Not** any hyphenated word - the shape is a key's.
KEY = re.compile(r"\b[a-z][a-z0-9]*(?:-[a-z0-9]+)+-enabled\b")

#: The panels that show **the model's own registry**, where a description
#: names its key on purpose.
#:
#: **The exemption is a count, not a convenience**: a third panel added
#: here would be a panel that hides a raw key behind a name nobody
#: checked, so the count is asserted as well.
#:
#: **And it was two until 2026-10-21.** The Skills panel was exempt
#: because its rows carried the skill's own description - prose written
#: for a model with the tool schema in front of it, which names the
#: switch's key on purpose. **Its rows now say what the thing touches**
#: and keep the description in the tooltip, so it shows no description
#: at all and the exemption had become a loophole rather than a
#: distinction. The Workbench still shows the registry's own text - it
#: is the page for making a skill of your own, and that is what it is
#: for - so it keeps the exemption.
MODEL_FACING = ("workbench",)


class _Config:
    """A config where every switch is off - the state a fresh install is in."""

    def get_bool(self, key, default=False):
        return False

    def get(self, key, default=None):
        return default

    def get_string(self, key, default=""):
        return default

    def set(self, key, value):
        return None

    def sense_allowed(self, key):
        return False


class _App:
    application_id = "test.chronoa.norawkey"

    def __init__(self):
        self.config = _Config()

    def activate(self):
        pass


def _labels(page):
    """Every label on screen, in tree order.

    **Effective visibility**: `get_visible()` is per-widget, so a label
    inside a hidden widget still answers True.
    """
    if not page.get_visible():
        return []
    out, stack = [], [page]
    while stack:
        node = stack.pop()
        if isinstance(node, Gtk.Label):
            text = (node.get_label() or "").strip()
            if text:
                out.append(text)
        if hasattr(node, "get_first_child"):
            child = node.get_first_child()
            while child is not None:
                stack.append(child)
                child = child.get_next_sibling()
    return out


def _panel_texts(sid):
    registry = surfaces.all_surfaces()
    assert sid in registry, f"the panel {sid!r} is not in the registry"
    _title, _icon, build = registry[sid]
    return _labels(build(_App()))


def test_the_exemption_is_the_size_it_claims():
    """**A count, so a third panel cannot be added here quietly.**"""
    assert len(MODEL_FACING) == 1, MODEL_FACING
    for sid in MODEL_FACING:
        assert sid in surfaces.SURFACE_IDS, sid


@pytest.mark.parametrize("sid", [s for s in surfaces.SURFACE_IDS
                                 if s not in MODEL_FACING])
def test_no_panel_names_a_raw_key(sid):
    """**The switch by the name the Settings row shows**, never by the key
    the schema spells it with."""
    found = {}
    for text in _panel_texts(sid):
        for key in KEY.findall(text):
            found.setdefault(key, text)
    assert not found, (
        f"{sid} names a gsettings key on screen, and a person who follows it "
        f"is hunting for a switch spelled a way no screen shows: "
        + "; ".join(f"{k!r} in {t[:100]!r}" for k, t in sorted(found.items())))


def test_the_exempt_panels_really_do_show_the_model_registry():
    """**The exemption is not a way to hide a key**: the two panels it names
    must actually be showing descriptions, or the exemption is a loophole
    rather than a distinction."""
    from shani_chronoa.skills import discover_skills

    schemas, _handlers = discover_skills()
    described = {s["function"].get("description", "") for s in schemas}
    for sid in MODEL_FACING:
        texts = _panel_texts(sid)
        assert texts, f"{sid} shows nothing at all"
        assert any(any(part in text for part in described if part)
                   for text in texts), (
            f"{sid} does not show a skill description, so the exemption is "
            f"not the one it claims to be")
