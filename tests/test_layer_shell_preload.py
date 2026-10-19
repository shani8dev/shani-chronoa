"""The `LD_PRELOAD` re-exec that makes `gtk4-layer-shell` work at all.

Two surfaces in this application want a layer-shell surface, and until this
existed **only one of them had the link order fixed**:

| surface | created by | went through the re-exec |
|---|---|---|
| the cue layer | `usr/bin/shani-chronoa-overlay`, its own process | yes |
| **the mascot** | `ChronoaApplication`, in the app's own process | **no** |

`application.py` constructs `mascot.Mascot(self)` rather than launching it, so the
mascot's surface is created inside `shani-chronoa` - and `shani-chronoa` did not
re-exec, while the overlay did. The mascot was therefore the one surface that
silently rendered as an ordinary window: the library logs *"Failed to initialize
layer surface, GTK4 Layer Shell may have been linked after libwayland"* and
returns a **working** window, so nothing raises and nothing says so.

**Both launchers are checked as source, never imported** - they have no
`.py` suffix, they call `os.execv`, and importing either one would run it. A
launcher is only checkable by reading it, so the check is written to be able to
fail on its own inputs (`_launcher_preloads`) rather than trusting a `grep`.
"""

import ast
import os
from pathlib import Path

import pytest

from shani_chronoa import layer_shell_preload as lp

from _source import package_source

ROOT = Path(__file__).resolve().parents[1]
LAUNCHERS = ("shani-chronoa", "shani-chronoa-overlay")


def _launcher(name: str) -> str:
    return (ROOT / "usr" / "bin" / name).read_text(encoding="utf-8")


def _launcher_preloads(source: str) -> bool:
    """Whether a launcher's text imports the helper *and* calls it.

    Two halves, because either alone is satisfiable by a text that does nothing:
    an import nobody calls, or a call nobody can reach. The control below feeds
    it both halves separately.
    """
    imports = ("from shani_chronoa.layer_shell_preload import "
               "preload_layer_shell")
    return imports in source and "preload_layer_shell()" in source


class _ReExec:
    """Record what `preload_layer_shell` asked the loader to do."""

    def __init__(self, monkeypatch):
        self.calls = []
        monkeypatch.setattr(lp.os, "execv", self._execv)

    def _execv(self, path, argv):
        self.calls.append((path, argv, dict(lp.os.environ)))

    @property
    def count(self) -> int:
        return len(self.calls)

    @property
    def preload(self) -> str:
        return self.calls[-1][2]["LD_PRELOAD"]


@pytest.fixture
def installed(monkeypatch, tmp_path):
    """A machine that has the library, at a path this test controls."""
    lib = tmp_path / lp.LIBRARY
    lib.write_text("", encoding="utf-8")
    monkeypatch.setattr(lp, "SEARCH_PATH", (str(tmp_path),))
    return str(lib)


# --- the four guards, each one a separate observable -------------------------

def test_the_library_is_preloaded_and_the_process_re_execed(monkeypatch, installed):
    """The fix itself: on a machine with the library, the process restarts with it."""
    rec = _ReExec(monkeypatch)
    monkeypatch.delenv(lp.MARKER, raising=False)
    monkeypatch.delenv("LD_PRELOAD", raising=False)

    lp.preload_layer_shell()

    assert rec.count == 1, "the process must be re-execed exactly once"
    path, argv, env = rec.calls[0]
    assert path == lp.sys.executable, "the new image is this interpreter"
    assert argv == [lp.sys.executable] + lp.sys.argv, (
        "the arguments are carried across the re-exec unchanged - argv[0] becomes "
        "the interpreter's own path, which is the documented signature of execv")
    assert os.path.realpath(rec.preload.split()[0]) == os.path.realpath(installed), (
        "the library leads LD_PRELOAD. The entry is accepted as either the "
        "installed name or its resolved path, because the loader takes both - "
        "what it must not be is some other library, or not first.")
    assert env[lp.MARKER] == "1", (
        "the marker is what the *second* process reads to avoid restarting "
        "again, and it travels through execv's environment - so a re-exec "
        "without it would loop")


def test_a_machine_without_the_library_is_never_restarted(monkeypatch, tmp_path):
    """The ordinary machine pays nothing: no library, no second process start."""
    rec = _ReExec(monkeypatch)
    monkeypatch.setattr(lp, "SEARCH_PATH", (str(tmp_path / "nothing"),))
    monkeypatch.delenv(lp.MARKER, raising=False)
    monkeypatch.delenv("LD_PRELOAD", raising=False)

    lp.preload_layer_shell()

    assert rec.count == 0, (
        "a machine with no layer shell must behave exactly as it did before - "
        "restarting it would cost every user 7 ms for nothing")


def test_the_second_process_does_not_restart_again(monkeypatch, installed):
    """The loop guard, on its own: a marker set means the work is already done."""
    rec = _ReExec(monkeypatch)
    monkeypatch.setenv(lp.MARKER, "1")
    monkeypatch.delenv("LD_PRELOAD", raising=False)

    lp.preload_layer_shell()

    assert rec.count == 0, "the re-exec would loop forever without this"


def test_an_inherited_preload_is_not_duplicated(monkeypatch, installed):
    """`LD_PRELOAD` already naming the library means someone else did this."""
    rec = _ReExec(monkeypatch)
    monkeypatch.delenv(lp.MARKER, raising=False)
    monkeypatch.setenv("LD_PRELOAD", f"{installed} /opt/other.so")

    lp.preload_layer_shell()

    assert rec.count == 0, (
        "prepending a second copy of a library already loaded is at best waste "
        "and at worst a duplicate-initialisation crash")


def test_an_existing_preload_is_kept_after_ours(monkeypatch, installed):
    """What the preload replaces is not destroyed - and no empty slot is left."""
    rec = _ReExec(monkeypatch)
    monkeypatch.delenv(lp.MARKER, raising=False)
    monkeypatch.setenv("LD_PRELOAD", "/opt/other.so /opt/third.so")

    lp.preload_layer_shell()

    parts = rec.preload.split()
    assert parts[0] == os.path.realpath(installed), "ours must be first"
    assert parts[1:] == ["/opt/other.so", "/opt/third.so"], (
        "the loader searches in order, so ours has to lead and the rest must "
        "survive in their original order")
    assert "  " not in rec.preload and not rec.preload.endswith(" "), (
        "an empty LD_PRELOAD would otherwise leave a doubled separator")


# --- finding the library -----------------------------------------------------

def test_it_finds_the_library_where_it_is_installed(monkeypatch, tmp_path):
    """`library_path` reads the filesystem rather than assuming a layout."""
    lib = tmp_path / lp.LIBRARY
    lib.write_text("", encoding="utf-8")
    monkeypatch.setattr(lp, "SEARCH_PATH", ("/nowhere-at-all", str(tmp_path)))

    assert lp.library_path() == str(lib)


def test_it_reports_none_rather_than_guessing(monkeypatch, tmp_path):
    """`None` is the answer for a machine with no GTK 4 layer shell."""
    monkeypatch.setattr(lp, "SEARCH_PATH", ("/nowhere-at-all", str(tmp_path)))

    assert lp.library_path() is None


def test_the_gtk_3_build_is_not_mistaken_for_the_gtk_4_one(monkeypatch, tmp_path):
    """`libgtk-layer-shell.so.0` is a different file and cannot load in GTK 4.

    Measured on this box: the GTK 3 typelib raises *"Requiring namespace 'Gtk'
    version '3.0', but '4.0' is already loaded"*, and `ctypes`-calling that
    library into a GTK 4 process registers a second GObject type system - which
    is worse than having none. So the search must never match it.
    """
    (tmp_path / "libgtk-layer-shell.so.0").write_text("", encoding="utf-8")
    monkeypatch.setattr(lp, "SEARCH_PATH", (str(tmp_path),))

    assert lp.library_path() is None, (
        "preloading the GTK 3 build would load a second GObject type system "
        "into the app's own process")


def test_every_measured_install_location_is_searched():
    """The three prefixes, not one guess.

    Measured: Debian/Ubuntu install it under `x86_64-linux-gnu` (this box,
    `/usr/lib/x86_64-linux-gnu/libgtk4-layer-shell.so.0`), Arch and Fedora under
    `/usr/lib`. Dropping a prefix is invisible on whichever layout you happen to
    be standing on - which is how a machine that has the library ends up paying
    nothing for it and looking like a machine that does not have it.
    """
    assert lp.SEARCH_PATH == ("/usr/lib/x86_64-linux-gnu", "/usr/lib64", "/usr/lib")


def test_it_finds_the_real_library_rather_than_inventing_a_path():
    """The real prefixes, asked of the real filesystem.

    Every other test patches `SEARCH_PATH` to a temp directory, which proves the
    search *logic* and says nothing about whether the real prefixes are right -
    a typo in one of them is invisible to all of them. So the expectation here is
    computed independently of the function and the two are compared.

    Both outcomes are honest answers: on a machine without the library the right
    answer is `None`, and this asserts that rather than skipping.
    """
    expected = next(
        (os.path.join(d, lp.LIBRARY) for d in lp.SEARCH_PATH
         if os.path.exists(os.path.join(d, lp.LIBRARY))), None)
    found = lp.library_path()

    assert found == expected, (
        f"library_path() said {found!r}; the filesystem says {expected!r}")
    if found is not None:
        assert os.path.exists(found), "a path it invented is not on disk"


# --- the shape a launcher depends on -----------------------------------------

def test_the_module_imports_nothing_but_os_and_sys():
    """A launcher must be able to call this before it imports the package.

    `automation_overlay` imports `gi` and `Gtk` at module scope, which is why it
    is the wrong home: `shani-chronoa` would have to start GTK to get a link
    order fixed.
    """
    tree = ast.parse(package_source("layer_shell_preload"))
    imported = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported.add(node.module.split(".")[0])

    assert imported <= {"os", "sys", "__future__"}, (
        f"this module gained {sorted(imported - {'os', 'sys', '__future__'})}")


# --- the wiring: which launchers go through it --------------------------------

@pytest.mark.parametrize("launcher", LAUNCHERS)
def test_the_launcher_preloads_before_it_imports_the_package(launcher):
    """Both launchers, and the call comes *before* the package import.

    Order is the whole mechanism: `execv` does not return, so a call placed
    after the package import would re-exec a process that had already imported
    GTK - which is precisely the state the link order has to be fixed *before*.
    """
    source = _launcher(launcher)

    assert _launcher_preloads(source), f"{launcher} does not call the helper"

    preload_at = source.index("preload_layer_shell()")
    for module in ("shani_chronoa.app", "shani_chronoa.automation_overlay"):
        if module in source:
            assert preload_at < source.index(f"from {module} import"), (
                f"{launcher} re-execs after importing {module}, so the loader "
                f"has already made its decision")
            break
    else:
        pytest.fail(f"{launcher} names no package import to order itself against")


@pytest.mark.parametrize("launcher", LAUNCHERS)
def test_no_launcher_carries_its_own_copy_of_the_fix(launcher):
    """One copy. A second `execv` in a launcher is the drift this test exists for."""
    source = _launcher(launcher)

    assert "os.execv" not in source, (
        f"{launcher} re-implements the re-exec; layer_shell_preload is the one")


def test_the_check_can_tell_a_launcher_that_does_nothing():
    """The control on `_launcher_preloads`: neither half alone is enough.

    Without this the wiring test above would pass on a launcher that imported
    the helper and never called it - the shape a refactor that drops one line
    leaves behind.
    """
    imports_only = ("from shani_chronoa.layer_shell_preload import "
                    "preload_layer_shell\nimport os\n")
    call_only = "import os\npreload_layer_shell()\n"

    assert not _launcher_preloads(imports_only), "an uncalled import is not a fix"
    assert not _launcher_preloads(call_only), "an unreachable call is not a fix"
    assert _launcher_preloads(imports_only + call_only)


# --- the reason any of it is here --------------------------------------------

def test_the_mascot_is_created_in_the_apps_own_process():
    """Why `shani-chronoa` is a layer-shell caller, and why this is pinned.

    If this stops being true - the mascot becomes its own process, say - then the
    app's preload is no longer needed and **should** be removed rather than left
    costing every launch 7 ms. That is why the claim is a test and not a note:
    the thing that justifies the change is exactly what can retire it.

    **Through `mascot.create()`, which is the one construction site.** The app
    used to write `mascot.Mascot(self)`, and it now asks the module for a
    companion: `create()` returns the layer-shell `Mascot` where the
    compositor allows one, and the Shell's own notch where it does not. Both
    are built **in the app's process**, so the preload is still load-bearing
    for the first of them - and a test that pinned the old spelling would
    have called a *spelling* a decision, and the preload would have stayed
    out of inertia while the test said it was fine.

    So the claim is asked of the shape rather than of a literal: the app
    builds its companion here, and the module offers exactly one way to
    build one.
    """
    source = package_source("app.application")
    mascot_source = package_source("mascot")

    assert "mascot.create(self)" in source, (
        "the app used to build the mascot in-process; if that has changed, the "
        "preload in usr/bin/shani-chronoa needs a decision rather than inertia")
    assert "def create(" in mascot_source, (
        "the module offers no way to build a companion, so the app's call to "
        "it cannot be the one construction site")
    # **One construction site, so the choice of class is made in one place.**
    # The app used to name `Mascot` directly, and the notch was added
    # afterwards; a second `Mascot(app)` in the app would be a class chosen
    # away from the module that says which one to use.
    assert mascot_source.count("Mascot(app)") == 2, (
        "the module builds a Mascot in more than one place, so the class is "
        "not chosen in one place and the app's preload may not cover the "
        "one it gets")


def test_the_mascot_asks_for_a_layer_surface():
    """The other half: a surface the preload exists to make work."""
    source = package_source("mascot")

    assert "init_for_window" in source, (
        "the mascot is no longer a layer-shell caller, so this whole change has "
        "no subject left")