"""Engine provider selection in shani_chronoa._link.

niri ships the engines as shani-shell's ``phone`` package; other editions ship
them as shani-link's ``shani_link`` package. Chronoa must load whichever is
installed, and callers must keep receiving a real module object (they either
alias it into sys.modules or keep it as ``_engine``).

Every case builds stand-in packages in tmp_path and points the env overrides
at them. ``_cached_provider`` and any imported provider modules are reset
between cases, because pytest imports shani_chronoa modules at collection and
sys.modules is process-global.
"""

import importlib
import sys

import pytest

import shani_chronoa._link as link


def _is_provider_module(name):
    return (
        name in ("phone", "shani_link")
        or name.startswith("phone.")
        or name.startswith("shani_link.")
    )


@pytest.fixture(autouse=True)
def _isolate_provider(monkeypatch):
    """Undo provider state a previous case left in globals, sys.modules, sys.path."""
    saved_modules = {
        n: sys.modules[n] for n in list(sys.modules) if _is_provider_module(n)
    }
    saved_path = list(sys.path)
    for name in saved_modules:
        monkeypatch.delitem(sys.modules, name)
    monkeypatch.setattr(link, "_cached_provider", None)
    for var in ("SHANI_PHONE_PATH", "SHANI_LINK_PATH", "SHANI_LINK_PROVIDER"):
        monkeypatch.delenv(var, raising=False)
    yield
    for name in list(sys.modules):
        if _is_provider_module(name) and name not in saved_modules:
            del sys.modules[name]
    sys.path[:] = saved_path


def _package(parent, name, modules=("moyoung",)):
    """Directory containing a minimal importable `name` package."""
    root = parent / (name + "-root")
    root.mkdir()
    pkg = root / name
    pkg.mkdir()
    (pkg / "__init__.py").write_text("")
    for mod in modules:
        (pkg / (mod + ".py")).write_text("PROVIDER = %r\n" % name)
    return root


def _empty(parent, name):
    root = parent / (name + "-empty")
    root.mkdir()
    return root


def test_shell_phone_only(tmp_path, monkeypatch):
    phone_root = _package(tmp_path, "phone")
    monkeypatch.setenv("SHANI_PHONE_PATH", str(phone_root))
    monkeypatch.setenv("SHANI_LINK_PATH", str(_empty(tmp_path, "link")))

    mod = link.module("moyoung")

    assert mod.__name__ == "phone.moyoung"
    assert mod.PROVIDER == "phone"
    assert link._cached_provider == ("phone", str(phone_root))


def test_shani_link_only(tmp_path, monkeypatch):
    link_root = _package(tmp_path, "shani_link")
    monkeypatch.setenv("SHANI_PHONE_PATH", str(_empty(tmp_path, "phone")))
    monkeypatch.setenv("SHANI_LINK_PATH", str(link_root))

    mod = link.module("moyoung")

    assert mod.__name__ == "shani_link.moyoung"
    assert mod.PROVIDER == "shani_link"
    assert link._cached_provider == ("shani_link", str(link_root))


def test_neither_installed_raises_import_error(tmp_path, monkeypatch):
    monkeypatch.setenv("SHANI_PHONE_PATH", str(_empty(tmp_path, "phone")))
    monkeypatch.setenv("SHANI_LINK_PATH", str(_empty(tmp_path, "link")))

    assert link.ensure_path() is False
    with pytest.raises(ImportError):
        link.module("moyoung")


def test_both_installed_defaults_to_phone_and_override_selects(tmp_path, monkeypatch):
    phone_root = _package(tmp_path, "phone")
    link_root = _package(tmp_path, "shani_link")
    monkeypatch.setenv("SHANI_PHONE_PATH", str(phone_root))
    monkeypatch.setenv("SHANI_LINK_PATH", str(link_root))

    assert link.module("moyoung").__name__ == "phone.moyoung"

    monkeypatch.setenv("SHANI_LINK_PROVIDER", "shani_link")
    link._reset()
    assert link.module("moyoung").__name__ == "shani_link.moyoung"
    assert link._cached_provider == ("shani_link", str(link_root))

    monkeypatch.setenv("SHANI_LINK_PROVIDER", "phone")
    link._reset()
    assert link.module("moyoung").__name__ == "phone.moyoung"


def test_module_returns_the_same_importlib_module_object(tmp_path, monkeypatch):
    phone_root = _package(tmp_path, "phone")
    monkeypatch.setenv("SHANI_PHONE_PATH", str(phone_root))
    monkeypatch.setenv("SHANI_LINK_PATH", str(_empty(tmp_path, "link")))

    mod = link.module("moyoung")

    assert mod is importlib.import_module("phone.moyoung")
    mod.PATCHED = True
    assert importlib.import_module("phone.moyoung").PATCHED is True


def test_failed_provider_does_not_poison_later_call(tmp_path, monkeypatch):
    # phone is installed but has no `moyoung` engine; shani_link has one.
    _package(tmp_path, "phone", modules=())
    link_root = _package(tmp_path, "shani_link")
    monkeypatch.setenv("SHANI_PHONE_PATH", str(tmp_path / "phone-root"))
    monkeypatch.setenv("SHANI_LINK_PATH", str(link_root))

    assert link.module("moyoung").__name__ == "shani_link.moyoung"
    assert link.module("moyoung").__name__ == "shani_link.moyoung"
    assert link._cached_provider == ("shani_link", str(link_root))


def test_provider_probe_is_cached_until_the_environment_changes(tmp_path, monkeypatch):
    phone_root = _package(tmp_path, "phone")
    monkeypatch.setenv("SHANI_PHONE_PATH", str(phone_root))
    real_isdir = link.os.path.isdir
    probes = []

    def counting_isdir(path):
        probes.append(path)
        return real_isdir(path)

    monkeypatch.setattr(link.os.path, "isdir", counting_isdir)

    first = link._providers()
    assert probes
    probes.clear()
    assert link._providers() == first
    assert probes == [], "the same environment must not re-probe the filesystem"

    monkeypatch.setenv("SHANI_LINK_PROVIDER", "shani_link")
    assert all(package == "shani_link" for package, _path in link._providers())
    assert probes, "an environment change must re-probe"

    link._reset()
    assert link._provider_cache == {}
