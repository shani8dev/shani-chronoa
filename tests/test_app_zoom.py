"""Whole-app text zoom: Ctrl +/- steps it, Ctrl 0 returns to the system size.

Harvested from harness-studio's whole-app zoom. It turns the same knob GNOME's
text-scaling turns (`gtk-xft-dpi`), so the whole UI grows together, and it is
session-level - it never writes the desktop's own setting. These tests pin the
behaviour that could regress: the step, the clamp, and that reset goes back to
exactly the system value, not a guessed one.
"""
import sys
import types
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "usr" / "lib" / "shani-chronoa"))

import gi  # noqa: E402

gi.require_version("Gtk", "4.0")
from gi.repository import Gtk  # noqa: E402

from shani_chronoa.app.application import ChronoaApplication as App  # noqa: E402


class _FakeSettings:
    def __init__(self, dpi):
        self._props = {"gtk-xft-dpi": dpi}

    def get_property(self, key):
        return self._props[key]

    def set_property(self, key, value):
        self._props[key] = value


def _zoomer(monkeypatch, base_dpi=98304):
    fake = _FakeSettings(base_dpi)
    monkeypatch.setattr(Gtk.Settings, "get_default", staticmethod(lambda: fake))
    app = types.SimpleNamespace(
        _ZOOM_STEP=App._ZOOM_STEP, _ZOOM_MIN=App._ZOOM_MIN, _ZOOM_MAX=App._ZOOM_MAX)
    app._set_zoom = App._set_zoom.__get__(app)
    step = lambda name: App._on_zoom(app, None, None, name)  # noqa: E731
    return app, fake, step


def test_a_step_scales_the_dpi_and_reset_restores_the_system_value(monkeypatch):
    app, fake, step = _zoomer(monkeypatch, base_dpi=98304)
    step("zoom-in")
    assert abs(app._zoom_level - App._ZOOM_STEP) < 1e-6
    assert fake.get_property("gtk-xft-dpi") == int(98304 * App._ZOOM_STEP)
    step("zoom-out")
    assert abs(app._zoom_level - 1.0) < 1e-6
    # reset goes back to the captured system value, exactly
    step("zoom-in")
    step("zoom-reset")
    assert app._zoom_level == 1.0 and fake.get_property("gtk-xft-dpi") == 98304


def test_zoom_stays_inside_its_range(monkeypatch):
    app, fake, step = _zoomer(monkeypatch)
    for _ in range(40):
        step("zoom-in")
    assert app._zoom_level == App._ZOOM_MAX, app._zoom_level
    for _ in range(80):
        step("zoom-out")
    assert app._zoom_level == App._ZOOM_MIN, app._zoom_level


def test_a_desktop_with_no_dpi_set_falls_back_to_96(monkeypatch):
    # gtk-xft-dpi is -1 when the desktop sets nothing; zoom must not scale from that.
    app, fake, step = _zoomer(monkeypatch, base_dpi=-1)
    step("zoom-in")
    assert app._base_xft_dpi == 96 * 1024
    assert fake.get_property("gtk-xft-dpi") == int(96 * 1024 * App._ZOOM_STEP)
