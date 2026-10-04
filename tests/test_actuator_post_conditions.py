"""Post-conditions: an actuator's effect read back from real state, not from its own report.

Until 2026-10-01 one actuator of sixty declared a POST_CONDITION, so every
other one returned UNVERIFIED and its result string was the only evidence.
Each test below proves both directions against the real thing the check reads
(a file, a process, a stub of the binary on PATH): the effect present is
VERIFIED, and the effect absent is FAILED - a check that cannot fail is not
a check.

The other half is the clipboard bug this fixed: `verify()` was not told
which tool ran, so reading the clipboard was checked as a write of "" and any
non-empty clipboard reported the READ as FAILED.
"""

import os
import stat
import subprocess
import sys
import time

import pytest

from shani_chronoa import verification
from shani_chronoa.verification import Verdict


def _verify(module, arguments, tool=None):
    return verification.verify(f"shani_chronoa.skills.{module}", arguments, tool=tool)


def _stub(bindir, name, script):
    path = bindir / name
    path.write_text("#!/bin/sh\n" + script)
    path.chmod(path.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)


@pytest.fixture
def bindir(tmp_path, monkeypatch):
    d = tmp_path / "bin"
    d.mkdir()
    monkeypatch.setenv("PATH", f"{d}:{os.environ.get('PATH', '')}")
    return d


# --- the contract --------------------------------------------------------------

def test_a_check_that_returns_none_is_unverified_not_failed(monkeypatch):
    import types
    mod = types.ModuleType("fake_pc")
    mod.POST_CONDITION = lambda arguments, tool=None: None
    monkeypatch.setitem(sys.modules, "fake_pc", mod)
    assert verification.verify("fake_pc", {}).verdict is Verdict.UNVERIFIED


def test_the_tool_name_reaches_a_two_argument_check(monkeypatch):
    import types
    seen = []
    mod = types.ModuleType("fake_pc2")
    mod.POST_CONDITION = lambda arguments, tool: seen.append(tool) or True
    monkeypatch.setitem(sys.modules, "fake_pc2", mod)
    verification.verify("fake_pc2", {}, tool="set_thing")
    assert seen == ["set_thing"]


def test_a_one_argument_check_still_works(monkeypatch):
    import types
    mod = types.ModuleType("fake_pc3")
    mod.POST_CONDITION = lambda arguments: (False, "nope")
    monkeypatch.setitem(sys.modules, "fake_pc3", mod)
    assert verification.verify("fake_pc3", {}, tool="x").verdict is Verdict.FAILED


def test_reading_the_clipboard_is_not_a_failed_write(monkeypatch):
    from shani_chronoa.skills import clipboard
    monkeypatch.setattr(clipboard, "_detect_backend", lambda: ("x11", "/bin/true", "/bin/echo"))
    assert _verify("clipboard", {}, tool="get_clipboard").verdict is Verdict.UNVERIFIED
    # and the write is still checked, both ways (echo prints its args plus a newline)
    assert _verify("clipboard", {"text": "-selection clipboard -o\n"}, tool="set_clipboard").verdict is Verdict.VERIFIED
    assert _verify("clipboard", {"text": "something else"}, tool="set_clipboard").verdict is Verdict.FAILED


# --- files -----------------------------------------------------------------------

def test_write_text_file(tmp_path):
    f = tmp_path / "note.txt"
    f.write_text("hello")
    assert _verify("write_text_file", {"path": str(f), "content": "hello"}).verdict is Verdict.VERIFIED
    assert _verify("write_text_file", {"path": str(f), "content": "other"}).verdict is Verdict.FAILED
    assert _verify("write_text_file", {"path": str(f), "content": "llo", "append": True}).verdict is Verdict.VERIFIED


def test_create_directory(tmp_path):
    assert _verify("create_directory", {"path": str(tmp_path)}).verdict is Verdict.VERIFIED
    assert _verify("create_directory", {"path": str(tmp_path / "nope")}).verdict is Verdict.FAILED


@pytest.mark.parametrize("module", ["delete_file", "trash_file"])
def test_removed_means_gone(tmp_path, module):
    f = tmp_path / "x"
    f.write_text("x")
    assert _verify(module, {"path": str(f)}).verdict is Verdict.FAILED
    f.unlink()
    assert _verify(module, {"path": str(f)}).verdict is Verdict.VERIFIED


def test_a_dangling_symlink_is_not_gone(tmp_path):
    link = tmp_path / "link"
    link.symlink_to(tmp_path / "missing-target")
    assert _verify("delete_file", {"path": str(link)}).verdict is Verdict.FAILED


def test_move_and_copy(tmp_path):
    src, dst = tmp_path / "a", tmp_path / "b"
    src.write_text("x")
    assert _verify("move_or_copy_file", {"source": str(src), "destination": str(dst), "mode": "copy"}).verdict \
        is Verdict.FAILED
    dst.write_text("x")
    assert _verify("move_or_copy_file", {"source": str(src), "destination": str(dst), "mode": "copy"}).verdict \
        is Verdict.VERIFIED
    assert _verify("move_or_copy_file", {"source": str(src), "destination": str(dst), "mode": "move"}).verdict \
        is Verdict.FAILED, "a move that left the source behind is not a move"
    src.unlink()
    assert _verify("move_or_copy_file", {"source": str(src), "destination": str(dst), "mode": "move"}).verdict \
        is Verdict.VERIFIED


# --- processes and services --------------------------------------------------------

def test_kill_process_reads_the_process_table():
    proc = subprocess.Popen(["sleep", "30"])
    try:
        args = {"pid": proc.pid, "signal_name": "TERM"}
        assert _verify("kill_process", args).verdict is Verdict.FAILED
        proc.terminate()
        proc.wait(timeout=5)
        assert _verify("kill_process", args).verdict is Verdict.VERIFIED
        assert _verify("kill_process", {"pid": proc.pid, "signal_name": "STOP"}).verdict is Verdict.UNVERIFIED
    finally:
        proc.kill()


def test_control_service(bindir, tmp_path):
    state = tmp_path / "state"
    state.write_text("inactive")
    _stub(bindir, "systemctl", f"cat {state}\n")
    assert _verify("control_service", {"unit": "x", "action": "start"}).verdict is Verdict.FAILED
    assert _verify("control_service", {"unit": "x", "action": "stop"}).verdict is Verdict.VERIFIED
    state.write_text("active")
    assert _verify("control_service", {"unit": "x", "action": "restart"}).verdict is Verdict.VERIFIED
    assert _verify("control_service", {"unit": "x", "action": "status"}).verdict is Verdict.UNVERIFIED


# --- audio, power, time, desktop -----------------------------------------------------

def test_volume_level_and_mute(bindir, tmp_path):
    out = tmp_path / "vol"
    out.write_text("Volume: 0.50")
    _stub(bindir, "wpctl", f"cat {out}\n")
    assert _verify("volume", {"percent": 50}, tool="set_volume").verdict is Verdict.VERIFIED
    assert _verify("volume", {"percent": 80}, tool="set_volume").verdict is Verdict.FAILED
    assert _verify("volume", {"mute": True}, tool="set_mute").verdict is Verdict.FAILED
    out.write_text("Volume: 0.50 [MUTED]")
    assert _verify("volume", {"mute": True}, tool="set_mute").verdict is Verdict.VERIFIED
    assert _verify("volume", {}, tool="get_volume").verdict is Verdict.UNVERIFIED


def test_power_profile(bindir):
    _stub(bindir, "powerprofilesctl", 'echo balanced\n')
    assert _verify("power_profile", {"profile": "balanced"}).verdict is Verdict.VERIFIED
    assert _verify("power_profile", {"profile": "performance"}).verdict is Verdict.FAILED
    assert _verify("power_profile", {}).verdict is Verdict.UNVERIFIED


def test_set_timezone(bindir):
    _stub(bindir, "timedatectl", 'echo Asia/Kolkata\n')
    assert _verify("set_timezone", {"action": "set", "timezone": "Asia/Kolkata"}).verdict is Verdict.VERIFIED
    assert _verify("set_timezone", {"action": "set", "timezone": "Europe/Paris"}).verdict is Verdict.FAILED
    assert _verify("set_timezone", {"action": "status"}).verdict is Verdict.UNVERIFIED


def test_do_not_disturb(bindir, tmp_path):
    banners = tmp_path / "banners"
    banners.write_text("false")
    _stub(bindir, "gsettings", f"cat {banners}\n")
    assert _verify("do_not_disturb", {"enabled": True}).verdict is Verdict.VERIFIED
    assert _verify("do_not_disturb", {"enabled": False}).verdict is Verdict.FAILED
    assert _verify("do_not_disturb", {}).verdict is Verdict.UNVERIFIED


def test_mic_mute(monkeypatch):
    from shani_chronoa.skills import set_mic_mute
    monkeypatch.setattr(set_mic_mute, "_default_source", lambda: ("51", "Built-in mic"))
    monkeypatch.setattr(set_mic_mute, "_is_muted", lambda ident: True)
    assert _verify("set_mic_mute", {"action": "mute"}).verdict is Verdict.VERIFIED
    assert _verify("set_mic_mute", {"action": "unmute"}).verdict is Verdict.FAILED
    monkeypatch.setattr(set_mic_mute, "_is_muted", lambda ident: None)
    assert _verify("set_mic_mute", {"action": "mute"}).verdict is Verdict.UNVERIFIED


def test_every_post_condition_is_read_only_by_name():
    """A post-condition reads; one that writes would be an actuator nobody consented to."""
    import inspect
    import importlib
    for mod in ("volume", "set_mic_mute", "power_profile", "set_timezone", "control_service", "kill_process",
                "create_directory", "delete_file", "trash_file", "write_text_file", "move_or_copy_file",
                "do_not_disturb", "clipboard"):
        src = inspect.getsource(importlib.import_module(f"shani_chronoa.skills.{mod}").POST_CONDITION)
        for verb in ('"set-volume"', '"set-mute"', '"set-timezone"', '"set", SCHEMA_ID', "_run_ppd(\"set",
                     "write_text(", "unlink(", "os.kill(", "rmtree(", "send2trash", '"trash"'):
            assert verb not in src, f"{mod}'s post-condition contains {verb}"
