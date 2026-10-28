"""Window control on a real niri, the AI desktop's compositor.

A private compositor stack: headless weston as the parent output, a nested
niri on it (its own runtime dir and socket), two real `foot` windows. The
backend acts by window id; niri's own window list is the oracle, never the
backend's word about itself.

Skipped where weston, niri or foot is missing (they are in shani-shell's dev
image, where this runs).
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import tempfile
import time

import pytest

pytestmark = pytest.mark.skipif(not all(shutil.which(t) for t in ("weston", "niri", "foot")),
                                reason="needs weston, niri and foot")


def wait(cond, timeout=10.0):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        try:
            if cond():
                return True
        except Exception:  # noqa: BLE001 - not up yet
            pass
        time.sleep(0.2)
    return False


@pytest.fixture
def niri_session(monkeypatch):
    run = tempfile.mkdtemp(prefix="niri-live-")
    os.chmod(run, 0o700)
    env = dict(os.environ, XDG_RUNTIME_DIR=run)
    env.pop("WAYLAND_DISPLAY", None)
    env.pop("NIRI_SOCKET", None)
    procs = [subprocess.Popen(["weston", "--backend=headless-backend.so", "--socket=parent", "--width=1920",
                               "--height=1080", "--idle-time=0", "--no-config"], env=env,
                              stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)]
    assert wait(lambda: os.path.exists(f"{run}/parent"))
    conf = os.path.join(run, "niri.kdl")
    with open(conf, "w") as f:
        f.write('hotkey-overlay { skip-at-startup; }\nanimations { off; }\n')
    procs.append(subprocess.Popen(["niri", "-c", conf], env=dict(env, WAYLAND_DISPLAY="parent"),
                                  stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL))
    assert wait(lambda: any(n.startswith("niri.") and n.endswith(".sock") for n in os.listdir(run)))
    sock = next(os.path.join(run, n) for n in os.listdir(run) if n.startswith("niri.") and n.endswith(".sock"))
    wl = next((n for n in os.listdir(run) if n.startswith("wayland-") and not n.endswith(".lock")), "wayland-1")
    monkeypatch.setenv("NIRI_SOCKET", sock)
    monkeypatch.setenv("XDG_RUNTIME_DIR", run)
    monkeypatch.setenv("WAYLAND_DISPLAY", wl)
    for title in ("alpha-term", "beta-term"):
        procs.append(subprocess.Popen(["foot", "--title", title, "sh", "-c", "sleep 60"],
                                      env=dict(env, WAYLAND_DISPLAY=wl), stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL))
    yield
    for p in reversed(procs):
        p.kill()
    shutil.rmtree(run, ignore_errors=True)


def niri_windows():
    return json.loads(subprocess.run(["niri", "msg", "-j", "windows"], capture_output=True, text=True).stdout)


def test_detect_picks_niri_and_acts_by_id(niri_session, monkeypatch):
    from shani_chronoa import windows
    from shani_chronoa.windows import niri

    monkeypatch.setenv("XDG_SESSION_TYPE", "wayland")
    monkeypatch.setenv("XDG_CURRENT_DESKTOP", "niri")
    backend, why = windows.detect()
    assert isinstance(backend, niri.NiriBackend), why

    assert wait(lambda: len(backend.list()) == 2)
    alpha = windows.find(backend.list(), title_contains="alpha")
    beta = windows.find(backend.list(), title_contains="beta")
    assert alpha.workspace >= 1 and alpha.app_id == "foot"

    backend.focus(alpha.id)
    assert wait(lambda: next(w for w in niri_windows() if str(w["id"]) == alpha.id)["is_focused"])
    backend.focus(beta.id)
    assert wait(lambda: next(w for w in niri_windows() if str(w["id"]) == beta.id)["is_focused"])

    # the other window, not the focused one, moves to workspace 2 and is closed
    backend.set_workspace(alpha.id, 2)
    assert wait(lambda: windows.find(backend.list(), title_contains="alpha").workspace == 2)
    assert next(w for w in niri_windows() if str(w["id"]) == beta.id)["is_focused"], "focus moved with it"
    backend.close(alpha.id)
    assert wait(lambda: [w["title"] for w in niri_windows()] == ["beta-term"])

    with pytest.raises(windows.WindowError, match="no minimized"):
        backend.minimize(beta.id)


def test_without_a_niri_session_it_says_why(monkeypatch):
    from shani_chronoa.windows import niri

    monkeypatch.delenv("NIRI_SOCKET", raising=False)
    backend, why = niri.available()
    assert backend is None and ("NIRI_SOCKET" in why or "not installed" in why)
