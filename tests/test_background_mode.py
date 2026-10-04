"""Background mode: the daemon, the one-runner lock, and the unit sync.

The property that matters most is that the window and the daemon never both
run the rules - two engines on one rules file fire every rule twice.
"""

import os
import signal
import stat
import subprocess
import sys
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

REPO = Path(__file__).resolve().parent.parent


def test_only_one_process_runs_the_rules(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_RUNTIME_DIR", str(tmp_path))
    holder = subprocess.Popen([sys.executable, "-c", (
        "import sys, time; sys.path.insert(0, %r)\n"
        "from shani_chronoa import runner_lock\n"
        "print(runner_lock.holds(), flush=True); time.sleep(30)") % str(REPO / "usr/lib/shani-chronoa")],
        stdout=subprocess.PIPE, text=True, env={**os.environ, "XDG_RUNTIME_DIR": str(tmp_path)})
    try:
        assert holder.stdout.readline().strip() == "True"
        from shani_chronoa import runner_lock
        runner_lock._held["fd"] = None
        assert runner_lock.holds() is False, "the other process holds the rules"
    finally:
        holder.kill()
        holder.wait()
    assert runner_lock.holds() is True, "a dead holder's lock is released by the kernel"
    runner_lock.release()


def test_the_scheduler_leaves_the_rules_alone_without_the_lock(monkeypatch):
    from shani_chronoa import runner_lock
    from shani_chronoa.senses.scheduler import AmbientScheduler
    polled = []
    engine = SimpleNamespace(poll=lambda: polled.append(1) or [], run_due=lambda: [], store=lambda: SimpleNamespace(all=list))
    sched = AmbientScheduler(senses={}, store=None, event_engine=engine)
    monkeypatch.setattr(runner_lock, "holds", lambda: False)
    sched._last_event_poll = -1e9
    sched._poll_event_rules(time.monotonic())
    assert polled == [], "without the lock the rules are left to the other process"
    monkeypatch.setattr(runner_lock, "holds", lambda: True)
    sched._last_event_poll = -1e9
    sched._poll_event_rules(time.monotonic())
    assert polled == [1]


def test_the_daemon_starts_and_stops_on_sigterm(tmp_path):
    env = {**os.environ, "XDG_RUNTIME_DIR": str(tmp_path / "run"), "XDG_STATE_HOME": str(tmp_path / "state"),
           "XDG_DATA_HOME": str(tmp_path / "data"), "XDG_CONFIG_HOME": str(tmp_path / "config"),
           "HOME": str(tmp_path), "PYTHONDONTWRITEBYTECODE": "1"}
    proc = subprocess.Popen([sys.executable, str(REPO / "usr/bin/shani-chronoa-daemon")], env=env,
                            stderr=subprocess.PIPE, text=True)
    try:
        deadline = time.time() + 20
        line = ""
        while "background mode running" not in line and time.time() < deadline:
            line = proc.stderr.readline()
        assert "no microphone" in line
        proc.send_signal(signal.SIGTERM)
        assert proc.wait(timeout=10) == 0
    finally:
        if proc.poll() is None:
            proc.kill()


def test_the_unit_is_installed_disabled_and_packaged():
    unit = (REPO / "usr/lib/systemd/user/shani-chronoa-daemon.service").read_text()
    assert "ExecStart=/usr/bin/shani-chronoa-daemon" in unit and "WantedBy=graphical-session.target" in unit
    pkgbuild = REPO.parent / "shani-pkgbuilds/shani-chronoa/PKGBUILD"
    if pkgbuild.exists():
        text = pkgbuild.read_text()
        assert "usr/bin/shani-chronoa-daemon" in text and "usr/lib/systemd/user/shani-chronoa-daemon.service" in text


def test_the_setting_drives_the_unit(tmp_path, monkeypatch):
    from shani_chronoa.app import ChronoaApplication
    log = tmp_path / "calls"
    state = tmp_path / "state"
    state.write_text("disabled")
    stub = tmp_path / "systemctl"
    stub.write_text(f'#!/bin/sh\necho "$@" >> {log}\ncase "$*" in *is-enabled*) cat {state};; esac\n')
    stub.chmod(stub.stat().st_mode | stat.S_IXUSR)
    monkeypatch.setenv("PATH", f"{tmp_path}:/usr/bin:/bin")

    def app(on):
        return SimpleNamespace(config=SimpleNamespace(get_bool=lambda k, d=False: on),
                               _BACKGROUND_UNIT=ChronoaApplication._BACKGROUND_UNIT)
    ChronoaApplication._sync_background_mode(app(True))
    assert "--user enable --now shani-chronoa-daemon.service" in log.read_text()
    state.write_text("enabled")
    log.write_text("")
    ChronoaApplication._sync_background_mode(app(True))
    assert "enable --now" not in log.read_text(), "already enabled: nothing to do"
    ChronoaApplication._sync_background_mode(app(False))
    assert "--user disable --now shani-chronoa-daemon.service" in log.read_text()
