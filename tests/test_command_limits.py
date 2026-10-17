"""The memory and time a project command may use come from the Sandbox page.

They were fixed at 2 GB and 600 s (PROFILE_FULL_ACCESS), and run_command also
clamped to a constant 600 - so a large build had no way through and a setting
alone would have been cut back silently. Now both read the same two settings.
"""
import pathlib
import sys

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "usr" / "lib" / "shani-chronoa"))

from shani_chronoa.config import ChronoaConfig  # noqa: E402
from shani_chronoa.sandbox import profiles  # noqa: E402


def test_the_defaults_are_the_old_fixed_numbers():
    assert profiles.command_limits(ChronoaConfig()) == (2048, 600)


def test_a_raised_setting_reaches_the_profile_and_run_commands_ceiling(monkeypatch):
    config = ChronoaConfig()
    config.set(profiles.COMMAND_MEMORY_KEY, "8192")
    config.set(profiles.COMMAND_TIMEOUT_KEY, "1800")
    profile = profiles.command_profile(config)
    assert profile.memory_limit == 8192 and profile.timeout_seconds == 1800
    assert profile.memory_bytes() == 8192 * 1024 * 1024
    from shani_chronoa import tools
    assert tools._command_timeout() == 1800
    assert tools._run_command_sandbox({}).timeout_seconds == 1800


def test_settings_are_kept_in_range():
    config = ChronoaConfig()
    config.set(profiles.COMMAND_MEMORY_KEY, "16")
    config.set(profiles.COMMAND_TIMEOUT_KEY, "999999")
    assert profiles.command_limits(config) == (512, 7200)


def test_the_sandbox_page_writes_the_setting():
    gi = pytest.importorskip("gi")
    gi.require_version("Gtk", "4.0")
    from gi.repository import Gtk
    from shani_chronoa.gui.surfaces import sandbox as page_mod

    class _App:
        config = ChronoaConfig()

    page = page_mod.build(_App())
    spins, stack = [], [page]
    while stack:
        w = stack.pop()
        if isinstance(w, Gtk.SpinButton):
            spins.append(w)
        child = w.get_first_child()
        while child is not None:
            stack.append(child)
            child = child.get_next_sibling()
    assert len(spins) == 3
    memory = next(s for s in spins if s.get_adjustment().get_upper() == 65536)
    memory.set_value(4096)
    assert profiles.command_limits(_App.config)[0] == 4096


def test_a_project_command_really_runs_at_low_priority():
    """Measured in a real child, not read from the profile: nice and I/O class."""
    import os
    from shani_chronoa.sandbox import SandboxConfig, SandboxExecutor, SandboxLevel
    code = ("import os, ctypes, platform; n={'x86_64':252,'aarch64':31}.get(platform.machine()); "
            "io=ctypes.CDLL(None).syscall(n,1,0) if n else -1; print(os.nice(0), io >> 13)")
    config = ChronoaConfig()
    for low, want_nice, want_class in ((True, os.nice(0) + 10, 3), (False, os.nice(0), None)):
        config.set(profiles.COMMAND_LOW_PRIORITY_KEY, "true" if low else "false")
        rc, out, _ = SandboxExecutor().execute(
            ["python3", "-c", code], SandboxConfig(level=SandboxLevel.LEVEL_3_HOST_USER, timeout_seconds=30),
            profile=profiles.command_profile(config))
        assert rc == 0, out
        nice, io_class = (int(x) for x in out.split()[-2:])
        assert nice == min(19, want_nice), (low, out)
        if want_class is not None:
            assert io_class == want_class, (low, out)


def test_cores_set_the_cpu_time_budget():
    config = ChronoaConfig()
    config.set(profiles.COMMAND_CORES_KEY, "8")
    config.set(profiles.COMMAND_TIMEOUT_KEY, "600")
    assert profiles.command_profile(config).cpu_seconds_budget(600) == 8 * 600


def test_a_switch_row_shows_the_state_it_was_given():
    """It ignored `active`, so every such switch read off (21 of them)."""
    gi = pytest.importorskip("gi")
    gi.require_version("Gtk", "4.0")
    from gi.repository import Gtk
    from shani_chronoa.gui.surfaces import common
    calls = []
    for state in (True, False):
        row = common.switch_row("x", "", state, calls.append)
        stack, found = [row], None
        while stack:
            w = stack.pop()
            if isinstance(w, Gtk.Switch):
                found = w
            child = w.get_first_child()
            while child is not None:
                stack.append(child)
                child = child.get_next_sibling()
        assert found.get_active() is state
    assert calls == [], "drawing the row wrote the setting back"
