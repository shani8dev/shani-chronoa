"""The Sandbox page: its probe runs in the real sandbox and the page shows it.

`sandbox.selfcheck.run` is executed for real (it starts a confined child the
way `run_command` does), so this is the README's security table checked on the
machine running the suite - with skips, not failures, where a layer is absent.
"""
import pathlib
import sys

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "usr" / "lib" / "shani-chronoa"))

gi = pytest.importorskip("gi")
gi.require_version("Gtk", "4.0")
from gi.repository import Gtk  # noqa: E402

from shani_chronoa.gui import surfaces  # noqa: E402
from shani_chronoa.gui.surfaces import sandbox as page_mod  # noqa: E402
from shani_chronoa.sandbox import selfcheck  # noqa: E402


class _Config:
    def __init__(self):
        self.written = []

    def get_bool(self, key, default=False):
        return False

    def set(self, key, value):
        self.written.append((key, value))


class _App:
    def __init__(self):
        self.config = _Config()


def _texts(widget):
    out, stack = [], [widget]
    while stack:
        w = stack.pop()
        for getter in ("get_label", "get_title", "get_subtitle"):
            fn = getattr(w, getter, None)
            if fn is not None:
                try:
                    value = fn()
                except TypeError:
                    continue
                if isinstance(value, str) and value:
                    out.append(value)
        child = w.get_first_child()
        while child is not None:
            stack.append(child)
            child = child.get_next_sibling()
    return out


def test_the_page_is_in_the_sidebar():
    assert "sandbox" in surfaces.SURFACE_IDS
    assert surfaces.sections()["sandbox"] == "Health and trust"


def test_the_probe_holds_what_the_readme_promises(tmp_path):
    if not any(c.ok for c in selfcheck.layers() if c.name == "bubblewrap"):
        pytest.skip("bubblewrap cannot create namespaces on this machine")
    project = tmp_path / "project"
    project.mkdir()
    result = selfcheck.run(str(project))
    assert result.ran, result.error
    by_name = {c.name: c for c in result.checks}
    assert by_name["The network is cut"].ok is True, by_name["The network is cut"]
    assert by_name["The system is read-only"].ok is True
    assert by_name["The project is writable"].ok is True
    assert list(project.iterdir()) == [], "the probe left a file in the project"
    private = by_name["Your private files cannot be read"]
    assert private.ok is not False, private


def test_pressing_test_shows_what_the_probe_found(tmp_path):
    page = page_mod.build(_App())
    surface = page.sandbox
    result = selfcheck.SelfCheck(True, "", [
        selfcheck.Check("The network is cut", True, "only the loopback interface is visible"),
        selfcheck.Check("The system is read-only", False, "a file could be written under /usr"),
    ])
    surface.show_result(result)
    texts = _texts(surface.results)
    assert "What the test found" in texts
    assert "a file could be written under /usr" in texts
    assert page.status() == "attention", "a failed check left the sidebar dot green"
    assert surface.test_button.get_label() == "Test again"


def test_the_seccomp_switch_writes_its_key():
    app = _App()
    page = page_mod.build(app)
    # Two switches since the Limits group (low priority) joined the page.
    switches = [w for w in _walk(page) if isinstance(w, Gtk.Switch)]
    assert len(switches) == 2
    for switch in switches:
        switch.set_active(not switch.get_active())
    assert ("sandbox-seccomp-enabled", "true") in app.config.written, app.config.written


def _walk(widget):
    stack = [widget]
    while stack:
        w = stack.pop()
        yield w
        child = w.get_first_child()
        while child is not None:
            stack.append(child)
            child = child.get_next_sibling()


def test_a_leaky_report_reads_as_leaky(tmp_path):
    """The other direction: what the child saw is what the page says."""
    secret = tmp_path / ".ssh"
    secret.mkdir()
    checks = {c.name: c for c in selfcheck._checks(
        {"private_listable": True, "private_readable": True,
         "interfaces": ["eth0", "lo"], "system_writable": True,
         "project_writable": False}, str(secret), str(tmp_path))}
    assert checks["Your private files cannot be read"].ok is False
    assert checks["The network is cut"].ok is False
    assert "eth0" in checks["The network is cut"].detail
    assert checks["The system is read-only"].ok is False
    assert checks["The project is writable"].ok is False
