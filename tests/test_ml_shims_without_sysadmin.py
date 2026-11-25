"""The ML shims must import and degrade on a Chronoa-only install.

shani-sysadmin is a separate app. Its modules are absent on an install that
only has Chronoa, so the shims must fail open: importing them and calling the
guarded sites must not raise. The import is simulated by parking ``None`` in
``sys.modules['shani_sysadmin']``, which is exactly what Python does for a
blocked top-level import.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

REPO = Path(__file__).resolve().parents[1]
PKG = REPO / "usr/lib/shani-chronoa/shani_chronoa"
SHIMS = ("distill", "model_registry", "adapter", "cultivation", "gguf_write", "merge")

sys.path.insert(0, str(REPO / "usr/lib/shani-chronoa"))


@pytest.fixture
def without_sysadmin(monkeypatch):
    import shani_chronoa

    saved = {name: sys.modules.get(f"shani_chronoa.{name}") for name in SHIMS}
    monkeypatch.setitem(sys.modules, "shani_sysadmin", None)
    for name in list(sys.modules):
        if name.startswith("shani_sysadmin."):
            monkeypatch.delitem(sys.modules, name, raising=False)
    yield
    for name, module in saved.items():
        key = f"shani_chronoa.{name}"
        if module is None:
            sys.modules.pop(key, None)
            if hasattr(shani_chronoa, name):
                delattr(shani_chronoa, name)
        else:
            sys.modules[key] = module
            setattr(shani_chronoa, name, module)


def _load(name: str):
    """Execute a shim from source with the app blocked."""
    import shani_chronoa

    spec = importlib.util.spec_from_file_location(
        f"shani_chronoa.{name}", PKG / f"{name}.py"
    )
    module = importlib.util.module_from_spec(spec)
    sys.modules[f"shani_chronoa.{name}"] = module
    spec.loader.exec_module(module)
    loaded = sys.modules[f"shani_chronoa.{name}"]
    setattr(shani_chronoa, name, loaded)
    return loaded


def test_all_six_shims_import_without_the_app(without_sysadmin):
    for name in SHIMS:
        module = _load(name)
        assert getattr(module, "SYSADMIN_MISSING", False) is True, name
        assert module.__name__ == f"shani_chronoa.{name}"


def test_distill_degrades_to_no_router_and_no_rows(without_sysadmin):
    distill = _load("distill")
    assert distill.fallback("increase the volume") is None
    assert distill.load_router() is None
    assert distill.harvest_rows() == []
    assert distill.teacher_notes() == []
    assert distill.train_router([("say", "hello")])["saved"] is False
    assert distill._cloud_allowed(object())[0] is False


def test_brain_router_fallback_has_nothing_to_offer(without_sysadmin):
    _load("distill")
    from shani_chronoa.app.brain import BrainMixin

    brain = SimpleNamespace(window=None)
    assert BrainMixin._router_fallback(brain, "increase the volume") is False


def test_learning_surface_reads_no_router_without_raising(without_sysadmin):
    _load("distill")
    from shani_chronoa.gui.surfaces import learning

    assert "none" in learning._pairs_sentence().lower()
    assert "none" in learning._router_sentence().lower()
    assert learning._teachers_sentence() == ""


def test_model_registry_has_no_active_model(without_sysadmin):
    registry = _load("model_registry")
    assert registry.active_entry() is None
    assert registry.scan()["models"] == []


def test_other_shims_name_the_missing_app(without_sysadmin):
    merge = _load("merge")
    with pytest.raises(RuntimeError, match="shani-sysadmin app"):
        merge.blend([], [])
