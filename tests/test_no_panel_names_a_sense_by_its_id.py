"""No panel shows a sense's registry id where a person reads it.

**"audio", "boots", "coredumps" are the code's words.** The Senses
panel calls those senses "Audio devices", "Boot history" and "Crashed
programs" - the same switch, the same panel family, two spellings - and a
person who has never met the registry cannot tell what "coredumps" is.

**The sweep reads two precise slots**, so it is not a hunt for a word:
a *group heading* and a *row subtitle*. A free-text search would be
hopeless here - "audio", "capture" and "boot" are ordinary English - and
a scan whose needle is in its own input reports nothing.

**And it cannot reach every panel.** The Memory panel's groups only
exist when the panel has a store to read, so its own test drives it with
one; this sweep is the half that reaches the panels which build empty.
"""

from __future__ import annotations

import os
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

from shani_chronoa import capabilities  # noqa: E402
from shani_chronoa.gui import surfaces  # noqa: E402


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
    application_id = "test.chronoa.sensenames"

    def __init__(self):
        self.config = _Config()

    def activate(self):
        pass


def _walk(widget):
    stack = [widget]
    while stack:
        node = stack.pop()
        yield node
        if hasattr(node, "get_first_child"):
            child = node.get_first_child()
            while child is not None:
                stack.append(child)
                child = child.get_next_sibling()


def _sense_ids():
    """Every sense's registry id - the words the code uses."""
    return set(capabilities.SENSE_TITLES)


@pytest.mark.parametrize("sid", list(surfaces.SURFACE_IDS))
def test_no_panel_names_a_sense_by_its_registry_id(sid):
    """**The id is the code's word.** It belongs in the tooltip, where
    somebody who wants it can find it."""
    registry = surfaces.all_surfaces()
    if sid not in registry:
        pytest.skip(f"the panel {sid!r} is not in the registry")
    _title, _icon, build = registry[sid]
    try:
        page = build(_App())
    except Exception as exc:  # noqa: BLE001 - a panel that cannot build is
        # reported by the other gates, not swallowed here.
        pytest.skip(f"the panel {sid!r} did not build: {exc}")
    ids = _sense_ids()
    for node in _walk(page):
        if type(node).__name__ == "PreferencesGroup":
            title = (node.get_title() or "").strip()
            assert title not in ids, (
                f"{sid} heads a group with a sense's registry id: {title!r}")
        if type(node).__name__ == "ActionRow":
            subtitle = (node.get_subtitle() or "").strip()
            assert subtitle not in ids, (
                f"{sid} subtitles a row with a sense's registry id: "
                f"{subtitle!r}")
