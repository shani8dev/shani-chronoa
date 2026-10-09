"""Window control on a real GNOME Shell, through the skills as chat runs them.

A private, headless `gnome-shell` (mutter's virtual monitor, its own D-Bus
daemon, HOME and runtime dir) with real GTK4 windows in it. Nothing reaches the
desktop the tests run on: no window, no extension change, no shared bus.

Every call goes through `tools.execute_tool`, so it runs where a chat request
runs - in the sandboxed skill child - and the compositor's own window list is
the oracle, never the skill's sentence about itself.

Two routes, both measured here:
- the Chronoa Shell extension enabled: every verb;
- the extension off: the accessibility bus, which closes, minimizes and
  toggles maximize on GTK4 windows and cannot focus.

Skipped where there is no `gnome-shell` to run.
"""

from __future__ import annotations

import json
import os
import shutil
import signal
import subprocess
import sys
import tempfile
import time
from pathlib import Path

import pytest

_REPO = Path(__file__).resolve().parents[1]
_EXTENSION = _REPO / "usr" / "share" / "gnome-shell" / "extensions" / "chronoa-windows@shani.dev"
_UUID = "chronoa-windows@shani.dev"

pytestmark = pytest.mark.skipif(
    not (shutil.which("gnome-shell") and shutil.which("dbus-daemon") and shutil.which("gdbus")),
    reason="needs gnome-shell, dbus-daemon and gdbus")

_WINDOW_APP = """
import sys, gi
gi.require_version("Gtk", "4.0")
from gi.repository import Gtk
app = Gtk.Application()
def go(a):
    w = Gtk.ApplicationWindow(application=a, title=sys.argv[1])
    w.set_default_size(320, 200)
    w.present()
app.connect("activate", go)
app.run([])
"""


class _Shell:
    """The private shell, and the processes it owns, by recorded PID."""

    def __init__(self, root: Path, monitor: str = "1280x800") -> None:
        self.root = root
        self.home = root / "home"
        self.home.mkdir()
        # A Wayland socket path must fit in 108 bytes, and pytest's tmp paths
        # do not - so the runtime dir alone is a short one in /tmp.
        self.run = Path(tempfile.mkdtemp(prefix="cwm."))
        os.chmod(self.run, 0o700)
        ext_dir = self.home / ".local" / "share" / "gnome-shell" / "extensions"
        ext_dir.mkdir(parents=True)
        shutil.copytree(_EXTENSION, ext_dir / _UUID)
        self.env = {
            "PATH": "/usr/bin:/bin", "HOME": str(self.home), "LANG": "C.UTF-8",
            "XDG_RUNTIME_DIR": str(self.run),
            "XDG_CONFIG_HOME": str(self.home / ".config"),
            "XDG_DATA_HOME": str(self.home / ".local" / "share"),
            "XDG_CACHE_HOME": str(self.home / ".cache"),
        }
        out = subprocess.run(
            ["dbus-daemon", "--session", "--fork", "--print-address=1", "--print-pid=1"],
            env=self.env, capture_output=True, text=True, timeout=20, check=True).stdout.split()
        self.bus, self.bus_pid = out[0], int(out[1])
        self.env["DBUS_SESSION_BUS_ADDRESS"] = self.bus
        self.shell = subprocess.Popen(
            ["gnome-shell", "--headless", "--wayland", "--no-x11",
             "--virtual-monitor", monitor, "--wayland-display", "wl-0"],
            env=self.env, stdout=open(root / "shell.log", "w"), stderr=subprocess.STDOUT,
            start_new_session=True)
        self.apps: dict = {}
        self._wait(lambda: self._owned("org.gnome.Shell"), 40, "gnome-shell did not start")

    def _owned(self, name: str) -> bool:
        out = subprocess.run(
            ["gdbus", "call", "--session", "--dest", "org.freedesktop.DBus",
             "--object-path", "/org/freedesktop/DBus", "--method",
             "org.freedesktop.DBus.NameHasOwner", name],
            env=self.env, capture_output=True, text=True, timeout=10)
        return "true" in out.stdout

    @staticmethod
    def _wait(cond, timeout: float, why: str) -> None:
        end = time.monotonic() + timeout
        while time.monotonic() < end:
            if cond():
                return
            time.sleep(0.2)
        raise AssertionError(why)

    def extension(self, on: bool) -> None:
        subprocess.run(["gnome-extensions", "enable" if on else "disable", _UUID],
                       env=self.env, capture_output=True, timeout=20, check=True)
        self._wait(lambda: self._owned("org.shani.Chronoa.Windows") == on, 20,
                   f"the extension did not {'appear' if on else 'go away'} on the bus")

    def window(self, title: str) -> int:
        script = self.root / "window_app.py"
        script.write_text(_WINDOW_APP)
        proc = subprocess.Popen([sys.executable, str(script), title],
                                env={**self.env, "WAYLAND_DISPLAY": "wl-0", "GDK_BACKEND": "wayland"},
                                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        self.apps[title] = proc
        return proc.pid

    def windows(self) -> dict:
        """The compositor's list, through the extension - the oracle."""
        out = subprocess.run(
            ["gdbus", "call", "--session", "--dest", "org.shani.Chronoa.Windows",
             "--object-path", "/org/shani/Chronoa/Windows", "--method",
             "org.shani.Chronoa.Windows.List"],
            env=self.env, capture_output=True, text=True, timeout=10, check=True).stdout.strip()
        raw = out[out.index("'") + 1:out.rindex("'")].encode().decode("unicode_escape")
        return {w["title"]: w for w in json.loads(raw)}

    def stop(self) -> None:
        for proc in self.apps.values():
            if proc.poll() is None:
                proc.terminate()
        try:
            os.killpg(self.shell.pid, signal.SIGTERM)
        except ProcessLookupError:
            pass
        try:
            self.shell.wait(timeout=15)
        except subprocess.TimeoutExpired:
            os.killpg(self.shell.pid, signal.SIGKILL)
        # Services the private bus activated (ibus starts its own session) are
        # found by this run's own runtime dir - a path nothing else uses - and
        # killed by PID; then the bus itself.
        marker = f"XDG_RUNTIME_DIR={self.run}".encode()
        for entry in Path("/proc").iterdir():
            if entry.name.isdigit() and int(entry.name) != os.getpid():
                try:
                    if marker in (entry / "environ").read_bytes().split(b"\0"):
                        os.kill(int(entry.name), signal.SIGTERM)
                except (OSError, ValueError):
                    pass
        try:
            os.kill(self.bus_pid, signal.SIGTERM)
        except ProcessLookupError:
            pass
        time.sleep(1)
        gvfs = self.run / "gvfs"
        if os.path.ismount(gvfs):
            subprocess.run(["fusermount3", "-u", str(gvfs)], capture_output=True)
            subprocess.run(["fusermount", "-u", str(gvfs)], capture_output=True)
        shutil.rmtree(self.run, ignore_errors=True)


@pytest.fixture(scope="module")
def shell(tmp_path_factory):
    s = _Shell(tmp_path_factory.mktemp("gnome"))
    try:
        yield s
    finally:
        s.stop()


@pytest.fixture
def on_that_desktop(shell, gsettings_env, monkeypatch):
    """Point this process (and so the skill child) at the private shell."""
    from shani_chronoa.config import ChronoaConfig
    for k in ("DBUS_SESSION_BUS_ADDRESS", "XDG_RUNTIME_DIR"):
        monkeypatch.setenv(k, shell.env[k])
    monkeypatch.setenv("WAYLAND_DISPLAY", "wl-0")
    monkeypatch.setenv("XDG_SESSION_TYPE", "wayland")
    monkeypatch.setenv("XDG_CURRENT_DESKTOP", "GNOME")
    monkeypatch.delenv("DISPLAY", raising=False)
    config = ChronoaConfig()
    config.set("window-close-enabled", "true")
    config.set("accessibility-sense-enabled", "true")
    return shell


def _skill(name: str, **arguments) -> str:
    from shani_chronoa import tools
    return tools.execute_tool(name, arguments, origin=tools.ORIGIN_USER)


def _settle(shell, cond, why: str, timeout: float = 8.0):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        state = shell.windows()
        if cond(state):
            return state
        time.sleep(0.2)
    raise AssertionError(f"{why}: {shell.windows()}")


def test_every_verb_through_the_extension(on_that_desktop):
    shell = on_that_desktop
    shell.extension(True)
    shell.window("alpha-notes")
    shell.window("beta-report")
    _settle(shell, lambda s: {"alpha-notes", "beta-report"} <= set(s), "the windows did not open")

    listed = _skill("list_windows")
    assert "from GNOME" in listed and "alpha-notes" in listed and "beta-report" in listed, listed

    # A window that has just mapped takes focus a moment later on its own, and
    # that can land after our first Focus call (seen once in a full-suite run:
    # "focus did not move to beta"). Wait until one window holds focus and that
    # has been stable for half a second before asking for anything.
    holder, since = None, time.monotonic()
    while time.monotonic() - since < 0.5:
        now = next((t for t, w in shell.windows().items() if w["focused"]), None)
        if now != holder:
            holder, since = now, time.monotonic()
        time.sleep(0.1)
    # Focus one, then move focus to the other: switching away from a focused
    # window is what mutter's focus-stealing guard can refuse, and a first
    # focus on an unfocused desktop passes even when switching is broken.
    for title, other in (("beta", "beta-report"), ("alpha", "alpha-notes")):
        said = _skill("focus_window", title_contains=title)
        assert said.startswith("Focused"), said
        _settle(shell, lambda s: s[other]["focused"], f"focus did not move to {title}")

    assert _skill("arrange_window", action="move", title_contains="alpha", x=40, y=60).startswith("Moved")
    assert _skill("arrange_window", action="resize", title_contains="alpha",
                  width=600, height=400).startswith("Resized")
    _settle(shell, lambda s: (s["alpha-notes"]["x"], s["alpha-notes"]["y"],
                              s["alpha-notes"]["width"], s["alpha-notes"]["height"]) == (40, 60, 600, 400),
            "move/resize did not land")

    assert _skill("arrange_window", action="maximize", title_contains="beta").startswith("Maximized")
    _settle(shell, lambda s: s["beta-report"]["maximized"], "beta did not maximize")
    _skill("arrange_window", action="unmaximize", title_contains="beta")
    _settle(shell, lambda s: not s["beta-report"]["maximized"], "beta did not unmaximize")

    _skill("arrange_window", action="fullscreen", title_contains="alpha")
    _settle(shell, lambda s: s["alpha-notes"]["fullscreen"], "alpha did not go fullscreen")
    _skill("arrange_window", action="unfullscreen", title_contains="alpha")
    _settle(shell, lambda s: not s["alpha-notes"]["fullscreen"], "alpha stayed fullscreen")

    _skill("arrange_window", action="set_workspace", title_contains="alpha", workspace=1)
    _settle(shell, lambda s: s["alpha-notes"]["workspace"] == 1, "alpha did not change workspace")
    bad = _skill("arrange_window", action="set_workspace", title_contains="alpha", workspace=40)
    assert "does not exist" in bad, bad

    _skill("arrange_window", action="minimize", title_contains="beta")
    _settle(shell, lambda s: s["beta-report"]["minimized"], "beta did not minimize")

    said = _skill("close_window", title_contains="beta")
    assert said.startswith("Asked"), said
    _settle(shell, lambda s: "beta-report" not in s, "beta did not close")
    shell.apps["beta-report"].wait(timeout=10)


def test_an_ambiguous_title_acts_on_nothing(on_that_desktop):
    shell = on_that_desktop
    shell.extension(True)
    shell.window("twin-doc one")
    shell.window("twin-doc two")
    _settle(shell, lambda s: {"twin-doc one", "twin-doc two"} <= set(s), "the windows did not open")
    said = _skill("close_window", title_contains="twin-doc")
    assert "2 windows match" in said, said
    time.sleep(1)
    assert {"twin-doc one", "twin-doc two"} <= set(shell.windows()), "a window closed on a guess"


def test_without_the_extension_the_accessibility_bus_does_what_it_can(on_that_desktop):
    shell = on_that_desktop
    shell.extension(True)  # only for the oracle's view; switched off below
    shell.window("gamma-sheet")
    _settle(shell, lambda s: "gamma-sheet" in s, "the window did not open")
    before = shell.windows()["gamma-sheet"]
    shell.extension(False)

    said = _skill("focus_window", title_contains="gamma")
    assert said.startswith("Could not focus") and _UUID in said, said

    said = _skill("arrange_window", action="maximize", title_contains="gamma")
    assert said.startswith("Toggled maximize"), said
    said = _skill("arrange_window", action="minimize", title_contains="gamma")
    assert said.startswith("Minimized"), said
    shell.extension(True)
    after = _settle(shell, lambda s: s["gamma-sheet"]["minimized"], "gamma did not minimize")["gamma-sheet"]
    assert after["maximized"] != before["maximized"], "maximize was reported toggled but did not change"
    shell.extension(False)

    said = _skill("close_window", title_contains="gamma")
    assert said.startswith("Asked") and "accessibility bus" in said, said
    shell.apps["gamma-sheet"].wait(timeout=10)
