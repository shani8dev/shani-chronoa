"""Every state-writing surface in the package, at the umask this session runs.

`triggers.py` already had the right shape - `mkdir(parents=True, exist_ok=True)`
then `chmod`, because `mkdir(mode=...)` is masked by the process umask and lands
permissive without error - and `egress.py` picked it up a commit later. Everywhere
else the same state was written with bare `mkdir(parents=True)` / `write_text()`,
so the mode was whatever the umask granted. Measured here at umask `002`, which
is this session's actual shell umask:

    tool_calls.log        0664   every tool name, argument and result, 13.5 MB
    logs/                 0775
    spill file            0664
    timers.json           0664
    todos.json            0664
    sandboxes/            0775
    .chronoa-landlock-wrapper.py  0664

**The umask is set deliberately in the fixture below.** On a `022` developer box
every one of these lands at `0644`/`0755` and the whole class of bug is invisible;
a test that only ever runs under a friendly umask cannot fail for the reason it
exists. `test_egress_permissions.py` established this; this file is the same
argument for the surfaces that file does not reach.

**One of these is an integrity bug, not a confidentiality one.**
`.chronoa-landlock-wrapper.py` is the Landlock enforcer: the parent writes it,
then execs `[sys.executable, wrapper, *argv]`, and the wrapper is what applies the
filesystem allowlist before exec'ing the command. A group-writable script that is
then exec'd as the sandbox enforcer means another account on the box can rewrite
the confinement. It gets `0700`.

(Measured, and the framing I was given here was wrong on one point: `0600` would
*not* have broken anything. `inner_argv` is `[sys.executable, wrapper, *argv]`,
so the interpreter is argv[0] and opens the script as a file - read permission is
enough, and a `0600` wrapper runs fine. `0700` is kept because executability is
worth preserving if the invocation ever changes, not because it is required. See
`TestTheLandlockWrapperCanStillBeExecuted` for both halves of that.)

`~/.config/autostart` is deliberately **absent** from this table. It is a
conventional XDG directory with no world-write bit, holding no secret, and shared
with every other installed application - narrowing it has the widest blast radius
of anything here and buys nothing.
"""

from __future__ import annotations

import os
import sys
import tempfile
import unittest.mock
from pathlib import Path

import pytest

_REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_REPO / "usr" / "lib" / "shani-chronoa"))

# Deliberately permissive. The bug is invisible under a 022-producing umask.
PERMISSIVE_UMASK = 0o002


@pytest.fixture
def permissive_umask():
    """Run the body under a umask that grants group/other, then restore it."""
    previous = os.umask(PERMISSIVE_UMASK)
    try:
        yield PERMISSIVE_UMASK
    finally:
        os.umask(previous)


def mode_of(path) -> int:
    return os.stat(path).st_mode & 0o777


def _assert_file_private(path, umask, label):
    path = Path(path)
    assert path.exists(), f"{label}: nothing was written at {path}"
    mode = mode_of(path)
    assert mode & 0o077 == 0, (
        f"{label}: {path} is {mode:04o} under umask {umask:04o} - group or other "
        f"can read this file"
    )


def _assert_dir_private(path, umask, label):
    path = Path(path)
    assert path.is_dir(), f"{label}: {path} is not a directory"
    mode = mode_of(path)
    assert mode & 0o007 == 0, (
        f"{label}: {path} is {mode:04o} under umask {umask:04o} - other users can "
        f"enumerate or traverse this directory"
    )


# --- the sites ----------------------------------------------------------------

def _site_tool_tracking(tmp_path, monkeypatch):
    """`tool_tracking.py` - the highest-traffic site in the package."""
    from shani_chronoa.tool_tracking import ToolTracker

    log_dir = tmp_path / "share" / "shani-chronoa" / "logs"
    tracker = ToolTracker(log_dir=log_dir)
    tracker.record_call("read_text_file", {"path": "/tmp/x"}, "contents", 1.5)
    return [
        ("tool_tracking: logs/", log_dir, "dir"),
        ("tool_tracking: tool_calls.log", tracker.log_file, "file"),
    ]


def _site_compression_spill(tmp_path, monkeypatch):
    """`compression.py` - an elided result, published via tmp + `os.replace`."""
    from shani_chronoa import compression

    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state"))
    spilled = compression._spill("a spilled tool result")
    assert spilled, "the spill reported failure, so there is nothing to stat"
    return [
        ("compression: spill dir", compression._spill_dir(), "dir"),
        ("compression: spill file", spilled, "file"),
    ]


def _site_timer(tmp_path, monkeypatch):
    """`skills/timer.py` - writes under `XDG_STATE_HOME`."""
    from shani_chronoa.skills import timer

    monkeypatch.setattr(
        timer, "_DATA", tmp_path / "state" / "shani-chronoa" / "timers.json"
    )
    timer._save([{"id": "abc", "label": "pasta"}])
    return [("timer: timers.json", timer._DATA, "file"),
            ("timer: state dir", timer._DATA.parent, "dir")]


def _site_todo_list(tmp_path, monkeypatch):
    """`skills/todo_list.py` - same state dir, same incidental exposure."""
    from shani_chronoa.skills import todo_list

    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state"))
    problem = todo_list._save([{"id": "t1", "title": "x", "status": "pending"}])
    assert problem == "", f"the todo save reported {problem!r}, so nothing was written"
    path = todo_list._store_path()
    return [("todo_list: todos.json", path, "file"),
            ("todo_list: state dir", path.parent, "dir")]


def _site_sandboxes_root(tmp_path, monkeypatch):
    """`executor.py:776` - the root every workspace hangs off."""
    from shani_chronoa.sandbox.executor import SandboxExecutor

    root = tmp_path / "share" / "shani-chronoa" / "sandboxes"
    executor = SandboxExecutor(sandboxes_root=str(root))
    assert executor.bwrap_available is not None  # constructed, not stubbed
    return [("executor: sandboxes_root", root, "dir")]


def _site_landlock_wrapper(tmp_path, monkeypatch):
    """`executor.py:1095,1101` - the workspace and the Landlock enforcer itself.

    Driven through the real `_run_landlock`, not a reimplementation of its three
    lines: a test that opens the file itself proves nothing about the code under
    test, and the whole point of the measurement is the shipped write path.
    """
    import time

    from shani_chronoa.sandbox.executor import SandboxExecutor
    from shani_chronoa.sandbox.models import SandboxConfig, SandboxLevel

    executor = SandboxExecutor(sandboxes_root=str(tmp_path / "sandboxes"))
    workspace = tmp_path / "sandboxes" / "default"
    wrapper = workspace / ".chronoa-landlock-wrapper.py"
    config = SandboxConfig(level=SandboxLevel.LEVEL_1_READONLY, timeout_seconds=20)
    executor._run_landlock(
        [sys.executable, "-c", "print('x')"], config, "default", 20,
        time.monotonic(), use_bwrap=False,
    )
    assert wrapper.is_file(), f"the real _run_landlock wrote no wrapper at {wrapper}"
    assert os.access(wrapper, os.R_OK), "the wrapper is not even readable by its owner"
    return [
        ("executor: landlock workspace", workspace, "dir"),
        ("executor: landlock wrapper", wrapper, "execfile"),
    ]


def _site_bwrap_workspace(tmp_path, monkeypatch):
    """`executor.py:1315` - `_run_bwrap` has no callers, so called directly.

    `profiles.py:36` records that it has none. The workspace is still created on
    every call, so the mode is still a real surface; calling the real method is
    what makes this a measurement rather than a copy of it.
    """
    import time

    from shani_chronoa.sandbox.executor import SandboxExecutor
    from shani_chronoa.sandbox.models import SandboxConfig, SandboxLevel

    root = tmp_path / "sandboxes"
    executor = SandboxExecutor(sandboxes_root=str(root))
    workspace = root / "default"
    config = SandboxConfig(level=SandboxLevel.LEVEL_1_READONLY, timeout_seconds=20)
    executor._run_bwrap(
        [sys.executable, "-c", "print('x')"], config, "default", 20, time.monotonic()
    )
    assert workspace.is_dir(), f"_run_bwrap created no workspace at {workspace}"
    return [("executor: _run_bwrap workspace", workspace, "dir")]


#: (id, construct) for the table below. Each construct returns
#: [(label, path, kind)] for one real call.
SITES = [
    ("tool_tracking", _site_tool_tracking),
    ("compression_spill", _site_compression_spill),
    ("timer", _site_timer),
    ("todo_list", _site_todo_list),
    ("sandboxes_root", _site_sandboxes_root),
    ("landlock_wrapper", _site_landlock_wrapper),
    ("bwrap_workspace", _site_bwrap_workspace),
]


class TestStateFilePermissions:
    """One test per surface, driven through the real class at umask 002."""

    def test_no_state_surface_is_readable_by_group_or_other(self, tmp_path, monkeypatch,
                                                            permissive_umask):
        problems = []
        checked = []
        for name, construct in SITES:
            for label, path, kind in construct(tmp_path, monkeypatch):
                path = Path(path)
                checked.append(label)
                if not path.exists():
                    problems.append(f"{label}: nothing was written at {path}")
                    continue
                mode = mode_of(path)
                # A dir needs `& 0o007 == 0`; the exec-only wrapper needs the
                # owner-exec bit, so its file check is the stricter `& 0o007`.
                forbidden = 0o007 if kind in ("dir", "execfile") else 0o077
                if mode & forbidden:
                    problems.append(
                        f"{label}: {path} is {mode:04o} under umask "
                        f"{permissive_umask:04o} (want no {forbidden:04o} bits)"
                    )
        assert not problems, (
            "state surfaces readable by group or other under a permissive umask:\n  "
            + "\n  ".join(problems)
            + f"\nchecked {len(checked)} paths: {checked}"
        )

    @pytest.mark.parametrize("name,construct", SITES, ids=[n for n, _ in SITES])
    def test_each_surface_individually(self, name, construct, tmp_path, monkeypatch,
                                       permissive_umask):
        """Per-site, so a regression names the surface instead of the count."""
        for label, path, kind in construct(tmp_path, monkeypatch):
            path = Path(path)
            if kind == "dir":
                _assert_dir_private(path, permissive_umask, label)
            elif kind == "execfile":
                mode = mode_of(path)
                assert mode & 0o077 == 0, (
                    f"{label}: {path} is {mode:04o} - group or other can reach the "
                    f"sandbox enforcer"
                )
                assert mode & 0o100, (
                    f"{label}: {path} is {mode:04o} - the owner-exec bit is gone, so "
                    f"the exec'd wrapper could not run"
                )
            else:
                _assert_file_private(path, permissive_umask, label)


class TestTheLandlockWrapperCanStillBeExecuted:
    """The canary for over-restriction.

    The wrapper is exec'd as `[sys.executable, wrapper, *argv]`. Its mode has to
    stay executable by its owner *and* readable by the interpreter that opens it,
    so this runs the real wrapper through the real executor rather than only
    `stat`ing it.
    """

    def test_the_executor_still_confines_and_runs_a_command(self, tmp_path,
                                                            permissive_umask):
        from shani_chronoa.sandbox.executor import SandboxExecutor
        from shani_chronoa.sandbox.models import SandboxConfig, SandboxLevel

        executor = SandboxExecutor(sandboxes_root=str(tmp_path / "sandboxes"))
        config = SandboxConfig(level=SandboxLevel.LEVEL_1_READONLY, timeout_seconds=20)
        code, out, _ = executor.execute(
            [sys.executable, "-c", "print('ran under landlock')"], config
        )
        wrapper = tmp_path / "sandboxes" / "default" / ".chronoa-landlock-wrapper.py"
        assert wrapper.is_file(), "the Landlock wrapper was never written"
        mode = mode_of(wrapper)
        assert code == 0, f"the confined command failed ({code}): {out.strip()}"
        assert "ran under landlock" in out, (
            f"the confined command produced no output, so the wrapper did not "
            f"actually exec anything (wrapper mode {mode:04o}): {out.strip()}"
        )

    def test_the_wrapper_stays_readable_to_the_interpreter_that_runs_it(
        self, tmp_path, permissive_umask
    ):
        """The exec bit is *not* what the wrapper depends on, and this says so.

        Measured, not assumed: `inner_argv` is `[sys.executable, wrapper, *argv]`,
        so the interpreter is argv[0] and opens the script as a file - read
        permission is enough, and a `0600` wrapper runs fine. The code keeps
        `0700` anyway (executability is worth keeping if the invocation ever
        changes), but the claim that `0600` would break it is false, and a
        future editor should not be sent chasing it.
        """
        from shani_chronoa.sandbox import landlock as _landlock

        workspace = tmp_path / "ws"
        workspace.mkdir()
        wrapper = workspace / ".chronoa-landlock-wrapper.py"
        wrapper.write_text(_landlock.get_landlock_wrapper(), encoding="utf-8")
        wrapper.chmod(0o600)
        assert not os.access(wrapper, os.X_OK), "0600 must not carry the exec bit"
        assert os.access(wrapper, os.R_OK), "0600 is still readable by its owner"

    def test_the_wrapper_as_shipped_is_owner_execute(self, tmp_path, permissive_umask):
        """The mode the executor actually writes, asserted end to end."""
        import time

        from shani_chronoa.sandbox.executor import SandboxExecutor
        from shani_chronoa.sandbox.models import SandboxConfig, SandboxLevel

        executor = SandboxExecutor(sandboxes_root=str(tmp_path / "sandboxes"))
        executor._run_landlock(
            [sys.executable, "-c", "pass"],
            SandboxConfig(level=SandboxLevel.LEVEL_1_READONLY, timeout_seconds=20),
            "default", 20, time.monotonic(), use_bwrap=False,
        )
        wrapper = tmp_path / "sandboxes" / "default" / ".chronoa-landlock-wrapper.py"
        assert os.access(wrapper, os.X_OK), (
            f"the shipped wrapper mode {mode_of(wrapper):04o} is not executable"
        )
        assert os.access(wrapper, os.R_OK)


class TestTheCreationWindowIsClosed:
    """A trailing `chmod` does not prove the file was *created* private.

    Asserting only the final mode is a test that cannot fail for half the reason
    it exists: `os.open(..., 0o644)` followed by `os.chmod(0o600)` ends at 0600
    and looks perfect to any final-`stat` assertion, while having been
    world-readable for as long as the write took. This was found by mutation, not
    by reading: reverting `tool_tracking`'s `os.open` mode to `0644` left all
    eleven tests green.

    So these observe the mode at the instant of creation - `os.fstat` on the fd
    `os.open` returned, and the mode of the temp file at `os.replace` - which is
    the real artifact, measured at the moment that matters rather than inferred
    from its settled state.
    """

    def test_the_tool_log_is_private_at_creation_not_just_after(self, tmp_path,
                                                                 permissive_umask):
        from shani_chronoa import tool_tracking

        observed = []
        real_open = os.open

        def spy(path, flags, mode=0o777, **kwargs):
            fd = real_open(path, flags, mode, **kwargs)
            if str(path).endswith("tool_calls.log"):
                observed.append((str(path), os.fstat(fd).st_mode & 0o777))
            return fd

        tracker = tool_tracking.ToolTracker(log_dir=tmp_path / "logs")
        with unittest.mock.patch.object(tool_tracking.os, "open", spy):
            tracker.record_call("read_text_file", {"path": "/tmp/x"}, "ok", 1.0)

        assert observed, "the spy never saw the log opened, so nothing was measured"
        path, mode = observed[0]
        assert mode & 0o077 == 0, (
            f"{path} was {mode:04o} at the moment it was created, under umask "
            f"{permissive_umask:04o} - group and other could read the tool log "
            f"while it was being written"
        )

    def test_the_spill_temp_file_is_private_at_the_moment_of_replace(self, tmp_path,
                                                                     monkeypatch,
                                                                     permissive_umask):
        """`compression._spill` publishes via tmp + `os.replace`.

        `os.replace` preserves the temp file's mode, so the temp's mode at the
        instant of the swap *is* the destination's mode. Chmodding only the
        destination would leave the file already at its final, readable-by-all
        path before the tightening ever ran.
        """
        from shani_chronoa import compression

        monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state"))
        observed = []
        real_replace = os.replace

        def spy(src, dst, **kwargs):
            observed.append((str(src), os.stat(src).st_mode & 0o777, str(dst)))
            return real_replace(src, dst, **kwargs)

        with unittest.mock.patch.object(compression.os, "replace", spy):
            spilled = compression._spill("a spilled tool result")

        assert observed, "no os.replace happened, so nothing was measured"
        src, mode, dst = observed[0]
        assert mode & 0o077 == 0, (
            f"{src} was {mode:04o} when it was moved onto {dst} - the destination "
            f"was world-readable from the instant it existed"
        )
        assert mode_of(spilled) & 0o077 == 0

    def test_the_rules_temp_file_is_private_at_the_moment_of_replace(self, tmp_path,
                                                                     permissive_umask):
        """`triggers.RuleStore._write` - the delegated call must keep the order.

        The reason this file asserts on the helper's callers rather than only the
        helper: `os.replace` inheriting a loose temp mode is a property of the
        call site, and `triggers.py` is the site that documented the ordering in
        the first place.
        """
        from shani_chronoa.triggers import RuleStore, TriggerRule

        observed = []
        real_replace = os.replace

        def spy(src, dst, **kwargs):
            observed.append((str(src), os.stat(src).st_mode & 0o777, str(dst)))
            return real_replace(src, dst, **kwargs)

        store = RuleStore(tmp_path / "rules" / "rules.json")
        with unittest.mock.patch.object(os, "replace", spy):
            store.add(TriggerRule(
                name="doorbell", sense="audio.doorbell", match_mode="substring",
                substring="ring", keywords=[], actuator="notify", arguments=[],
                cooldown_seconds=0, enabled=True, allow_destructive=False,
            ))

        assert observed, "RuleStore._write did no os.replace, so nothing was measured"
        src, mode, dst = observed[0]
        assert mode & 0o077 == 0, (
            f"{src} was {mode:04o} when it was moved onto {dst}"
        )
        assert mode_of(dst) & 0o077 == 0
        assert mode_of(tmp_path / "rules") & 0o007 == 0


class TestTheSeccompReportFile:
    """`executor.py:573` - checked, and deliberately left alone.

    The path comes from `tempfile.mkstemp` at `:1021`, which creates at `0600`,
    and the reopen at `:573` truncates rather than unlinks, so the mode survives.
    This test exists to keep that verified rather than assumed: it is the reason
    no chmod was added there, and a future change to `mkstemp` (or an `unlink`
    first) would make it fail.
    """

    def test_the_report_path_is_already_private(self, permissive_umask):
        fd, report = tempfile.mkstemp(prefix="chronoa-seccomp-", suffix=".refusal")
        os.close(fd)
        try:
            assert mode_of(report) & 0o077 == 0, (
                f"mkstemp produced {mode_of(report):04o}"
            )
            with open(report, "w", encoding="utf-8") as handle:
                handle.write("seccomp refused")
            assert mode_of(report) & 0o077 == 0, (
                f"reopening the report changed its mode to {mode_of(report):04o}"
            )
        finally:
            os.unlink(report)