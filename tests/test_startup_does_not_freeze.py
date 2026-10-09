"""The window's start-up pass builds one panel per main-loop turn.

`ChronoaWindow._seed_status_dots` built every sidebar panel inside a single
idle callback, so the main loop was held until all of them were done -
including Diagnostics, whose probes take a real screen capture. Measured
2026-10-08 in the real app: ~8 s frozen just after start, and a spoken request
made in that time was lost because the mic button's action waited behind it.
"""

import sys
import types

import pytest

gi = pytest.importorskip("gi")
gi.require_version("Gtk", "4.0")
from gi.repository import GLib  # noqa: E402

from shani_chronoa.gui import surfaces, window as window_mod  # noqa: E402


def _panel(name, built, prebuild=True):
    module = types.ModuleType(f"fake_panel_{name}")
    module.PREBUILD = prebuild

    def build(_app):
        built.append(name)
        return object()

    build.__module__ = module.__name__
    sys.modules[module.__name__] = module
    return (name.title(), "x-symbolic", build)


@pytest.fixture
def seeded(monkeypatch):
    built, callbacks = [], []
    panels = {"alpha": _panel("alpha", built), "beta": _panel("beta", built),
              "slow": _panel("slow", built, prebuild=False), "gamma": _panel("gamma", built)}
    monkeypatch.setattr(surfaces, "SURFACE_IDS", tuple(panels))
    monkeypatch.setattr(surfaces, "available_surfaces", lambda: panels)
    monkeypatch.setattr(window_mod, "_panel_status", lambda page: "ok")
    monkeypatch.setattr(GLib, "idle_add", lambda fn, *a, **k: callbacks.append(fn) or 1)
    sidebar = types.SimpleNamespace(_surface_rows=dict.fromkeys(panels), set_status=lambda *a: None)
    fake = types.SimpleNamespace(_sidebar_page=sidebar, _surface_pages={}, _app=None)
    window_mod.ChronoaWindow._seed_status_dots(fake)
    assert len(callbacks) == 1, "the pass was not scheduled on the main loop"
    yield built, callbacks[0]
    for name in panels:
        sys.modules.pop(f"fake_panel_{name}", None)


def test_each_main_loop_turn_builds_at_most_one_panel(seeded):
    built, step = seeded
    turns = 0
    while True:
        before = len(built)
        again = step()
        turns += 1
        assert len(built) - before <= 1, f"one turn built {built[before:]}"
        if again == GLib.SOURCE_REMOVE:
            break
        assert turns < 10, "the pass never finished"
    assert built == ["alpha", "beta", "gamma"]


def test_a_slow_panel_is_built_when_opened_not_at_start(seeded):
    built, step = seeded
    while step() != GLib.SOURCE_REMOVE:
        pass
    assert "slow" not in built


def test_diagnostics_is_marked_slow():
    from shani_chronoa.gui.surfaces import diagnostics
    assert diagnostics.PREBUILD is False
