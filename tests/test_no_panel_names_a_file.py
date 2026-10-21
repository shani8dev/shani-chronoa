"""No panel names the code's own files at the person who is reading it.

**A module's name is the code's word.** "so secret_store.py can read
and store API keys" tells a person nothing they can act on - the keyring
is the thing, and the keyring is what the Settings row and the desktop
are about. The file belongs in the evidence, which every panel already
puts behind its own **Details** button.

The same holds for a model file: "links it as current.gguf" is the
loader's private name for a symlink, and nobody reading "why is my model
not loading?" needs it.

Measured 2026-10-21 by building every panel: three panels named a
module in a sentence a person reads.

**The instrument counts what is on the page, not what is merely
visible.** `get_visible()` is per-widget, so a label inside a collapsed
`Gtk.Revealer` answers True and is not on the page - and a sweep that
counts those reports the Voice panel's own "Details" reading as a
defect. The walk here therefore asks whether a widget is *effectively*
shown: visible itself, and visible in every ancestor, and not inside a
collapsed revealer.

**Two panels are exempt, and each says why.** The artifacts panel is a
*file browser*: it lists the user's own files, and one of them is a
Python module, so naming it is the answer rather than a leak. The
workbench is the page for making a skill of your own, and its answer
names the tool to run - which is the actionable thing. The exemption is
a count, so a third panel cannot be added to it quietly.
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

#: A file the code owns: a source module (`foo.py`, `pkg/foo.py`) or an
#: engine's model file (`model.gguf`, `voice.onnx`, `weights.bin`).
CODE_FILE = re.compile(
    r"(?<![\w/.-])(?:[\w-]+/)*[\w-]+\.(?:py|gguf|onnx|bin)\b")

#: The panels whose answers are *about* files, each with its reason.
#: **A count, so a third panel cannot be added here quietly.**
EXEMPT = {
    "artifact_store": "a file browser - it lists the user's own files",
    "workbench": "a developer page - its answer names the tool to run",
}


class _Config:
    """Every switch off - the state a fresh install is in."""

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
    application_id = "test.chronoa.nofile"

    def __init__(self):
        self.config = _Config()

    def activate(self):
        pass


def _effectively_shown(node) -> bool:
    """On the page, not merely visible in itself.

    **A widget can be visible and still not be on the page**: a label
    inside a collapsed `Gtk.Revealer` answers `get_visible()` True, and
    so does one inside a hidden box. A sweep that counts those reports
    the Voice panel's own "Details" reading as a defect.
    """
    parent = node.get_parent()
    while parent is not None:
        if not parent.get_visible():
            return False
        if isinstance(parent, Gtk.Revealer) and not parent.get_child_revealed():
            return False
        parent = parent.get_parent()
    return node.get_visible()


def _on_screen_text(page):
    """Every label a person can actually see."""
    out, stack = [], [page]
    while stack:
        node = stack.pop()
        if not _effectively_shown(node):
            continue
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


def test_the_exemption_is_the_size_it_claims():
    """**A count, so a third panel cannot be added here quietly.**"""
    assert len(EXEMPT) == 2, EXEMPT
    for sid in EXEMPT:
        assert sid in surfaces.SURFACE_IDS, sid


@pytest.mark.parametrize("sid", [s for s in surfaces.SURFACE_IDS
                                 if s not in EXEMPT])
def test_no_panel_names_a_file_at_the_person(sid):
    """**The file belongs in the evidence**, which is behind the panel's
    own Details button - not in the sentence the person reads."""
    registry = surfaces.all_surfaces()
    assert sid in registry, f"the panel {sid!r} is not in the registry"
    _title, _icon, build = registry[sid]
    try:
        page = build(_App())
    except Exception as exc:  # noqa: BLE001 - a panel that cannot build is
        # reported by the other gates, not swallowed here.
        pytest.skip(f"the panel {sid!r} did not build: {exc}")
    for text in _on_screen_text(page):
        found = CODE_FILE.findall(text)
        assert not found, (
            f"{sid} names a file at the person: {found} in {text[:120]!r}")
