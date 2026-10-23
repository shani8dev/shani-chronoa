"""The Models panel must not be built during the window's start-up pass.

`ChronoaWindow._seed_status_dots` builds one sidebar panel per main-loop turn,
so the window is usable while the health dots fill in. A panel whose `build` is
slow by nature opts out with a module-level `PREBUILD = False`, which that pass
reads as `getattr(module, "PREBUILD", True)`.

**The Models panel is the second one, and the reason is the freeze
`tests/test_gguf_header.py` set out to fix.** Building it calls
`model_registry.scan()`, which asks `ctxfit.gguf_model_info()` about every
`.gguf` in the model directory - and the reader that used to answer that was
`gguf.GGUFReader`, whose `__init__` is `np.memmap(path)`, a mapping of the
**whole 1.1 GB checkpoint**. The scan walks a *directory*, not one file. All of
it ran on the GTK main thread while the window was still settling.

The header parser and the `(path, mtime, size)` cache fix the **cost**. This
flag removes the work from **start-up entirely**, so a cold launch cannot be
held by it whatever any reader costs - which is the half that is still missing
without it.

## Why not just assert the flag

`PREBUILD is False` on its own is a constant, and a constant nothing reads is
decoration. So the first class **drives the real decision**: the real
`ChronoaWindow._seed_status_dots`, the real registry, and the real *module* of
every entry. Only the build **callable** is swapped for a recorder, so nothing
is constructed and `prebuilt()` still reads `PREBUILD` off the shipping module.

Every claim here carries a control that can fail:

* `test_the_harness_itself_builds_the_prebuilt_panels` - if the pass built
  nothing at all, "models was skipped" would be true of every panel and mean
  nothing.
* `test_the_skip_is_the_flag_and_not_the_harness` - `PREBUILD = True` must make
  the panel appear. Without this, a harness that never reached `models` would
  satisfy every other assertion in the file.
* `test_a_panel_that_says_nothing_is_built_eagerly` - the `True` **default**.
  A default of `False` is the same line of code with the opposite behaviour and
  would silently cost the other thirty-odd panels their dot at start-up, so it
  is driven rather than read out of the source.
"""

import sys
import types

import pytest

gi = pytest.importorskip("gi")
gi.require_version("Gtk", "4.0")
from gi.repository import GLib  # noqa: E402

from shani_chronoa.gui import surfaces, window as window_mod  # noqa: E402

#: The panels this file names. The *set* of flag-carriers is derived from the
#: modules themselves, never typed - a hand-kept list of which panels opt out is
#: a list that rots, and a panel added tomorrow would then fail the control below
#: for the wrong reason.  Measured: models, diagnostics and phone.
#: `driving` since 2026-10-23: its portal row requests a RemoteDesktop session,
#: and asked at start-up that left the app unable to answer AT-SPI.
NAMED_SLOW = ("models", "diagnostics", "driving")


def _declared_slow(panels):
    """The panels whose own module declares `PREBUILD = False`."""
    found = set()
    for name, entry in panels.items():
        module = sys.modules.get(getattr(entry[2], "__module__", ""), None)
        if getattr(module, "PREBUILD", True) is False:
            found.add(name)
    return found


def _run_the_pass(monkeypatch, panels):
    """Drive the real `_seed_status_dots` over `panels`; return what it built.

    Each entry's build callable is replaced by a recorder that keeps the
    original `__module__` - which is the module `prebuilt()` reads `PREBUILD`
    from, so the answer under test is still the shipping one and nothing is
    constructed.
    """
    built = []

    def record(name):
        def build(_app):
            built.append(name)
            return object()

        build.__module__ = panels[name][2].__module__
        return (panels[name][0], panels[name][1], build)

    wired = {name: record(name) for name in panels}
    monkeypatch.setattr(surfaces, "available_surfaces", lambda: wired)
    monkeypatch.setattr(surfaces, "SURFACE_IDS", tuple(wired))
    monkeypatch.setattr(window_mod, "_panel_status", lambda page: "ok")
    callbacks = []
    monkeypatch.setattr(GLib, "idle_add", lambda fn, *a, **k: callbacks.append(fn) or 1)
    sidebar = types.SimpleNamespace(
        _surface_rows=dict.fromkeys(wired), set_status=lambda *a: None)
    window = types.SimpleNamespace(_sidebar_page=sidebar, _surface_pages={}, _app=None)

    window_mod.ChronoaWindow._seed_status_dots(window)
    assert len(callbacks) == 1, "the pass was not scheduled on the main loop at all"
    for _ in range(len(wired) + 2):
        if callbacks[0]() == GLib.SOURCE_REMOVE:
            break
    else:
        pytest.fail("the pass never finished")
    return built


def _real_registry():
    """The real registry, as the window sees it."""
    return surfaces.available_surfaces()


class TestTheModelsPanelIsNotPrebuilt:

    def test_the_flag_is_set_on_the_module(self):
        from shani_chronoa.gui.surfaces import models
        assert models.PREBUILD is False, (
            "the Models panel must declare PREBUILD = False; the reading is "
            "getattr(module, 'PREBUILD', True), so an absent flag is an eager build")

    def test_the_startup_pass_does_not_build_the_models_panel(self, monkeypatch):
        real = _real_registry()
        assert "models" in real, (
            "the registry no longer names the Models panel, so this file would "
            "be asserting about nothing")
        built = _run_the_pass(monkeypatch, real)
        assert "models" not in built, (
            "the start-up pass built the Models panel, so a cold launch still "
            f"walks every .gguf on the main thread; it built {built}")

    def test_diagnostics_is_skipped_by_the_same_mechanism(self, monkeypatch):
        """The other panel carrying the flag, so the pass reads the flag per
        module rather than having models wired in by name."""
        built = _run_the_pass(monkeypatch, _real_registry())
        for name in NAMED_SLOW:
            assert name not in built, f"{name} carries PREBUILD = False but was built"

    def test_the_harness_itself_builds_the_prebuilt_panels(self, monkeypatch):
        """**The control.** A pass that built nothing would satisfy every other
        assertion in this file, because "skipped" and "never reached" look alike."""
        real = _real_registry()
        slow = _declared_slow(real)
        assert set(NAMED_SLOW) <= slow, (
            "a panel this file names no longer opts out: "
            f"{sorted(set(NAMED_SLOW) - slow)}")
        built = _run_the_pass(monkeypatch, real)
        missing = set(real) - set(built)
        assert missing <= slow, (
            f"the harness reached only {len(built)} of {len(real)} panels and "
            f"skipped {sorted(missing - slow)}, which carry no PREBUILD flag, so "
            "it is not driving the real pass")
        assert len(built) >= len(real) - len(slow) - 1, (
            f"only {len(built)} of {len(real)} panels were built; the control "
            "above needs the pass to reach the registry")

    def test_the_skip_is_the_flag_and_not_the_harness(self, monkeypatch):
        """**The control that makes the rest of the file mean anything.**

        `PREBUILD = True` must make the panel appear in the built list. A harness
        that quietly failed to reach `models` would satisfy every other test here.
        """
        from shani_chronoa.gui.surfaces import models
        monkeypatch.setattr(models, "PREBUILD", True)
        built = _run_the_pass(monkeypatch, _real_registry())
        assert "models" in built, (
            "raising the flag did not make the panel appear, so the skip is not "
            "being caused by the flag")
        assert "diagnostics" not in built, (
            "diagnostics carries its own flag and was not touched here; seeing it "
            "built means the fixture is reading some module other than the one it "
            "patched")

    def test_a_panel_that_says_nothing_is_built_eagerly(self, monkeypatch):
        """**The `True` default, driven rather than read.** A panel whose module
        declares no `PREBUILD` must still be built at start-up - otherwise the
        flag silently turns every panel opt-out, which is the opposite design."""
        module = types.ModuleType("fake_panel_says_nothing")
        sys.modules[module.__name__] = module
        assert not hasattr(module, "PREBUILD"), "the fixture declared the flag itself"

        def build(_app):
            return object()

        build.__module__ = module.__name__
        panels = {"says_nothing": ("Says Nothing", "x-symbolic", build)}
        try:
            built = _run_the_pass(monkeypatch, panels)
        finally:
            sys.modules.pop(module.__name__, None)
        assert built == ["says_nothing"], (
            "a panel with no PREBUILD must be built eagerly; the pass built "
            f"{built}")