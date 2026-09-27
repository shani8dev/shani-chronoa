"""The sandbox must never silently pretend it confined anything.

`executor.py` used to route LEVEL_1/LEVEL_2 to plain host execution whenever
bubblewrap was missing, via `or not self.bwrap_available`. Every skill defaults
to LEVEL_3, so the practical effect on a machine without bwrap was that a
caller asking for a sandbox got none, and the only symptom was that the command
worked. That is the same shape as the `set_timer` bug this repo already
documented: reporting success while doing something else.
"""

import pytest
from pathlib import Path

from shani_chronoa.sandbox.executor import (
    DANGEROUS_BINARIES,
    SandboxExecutor,
    _first_blocked_binary,
)
from shani_chronoa.sandbox.models import SandboxConfig, SandboxLevel

REPO_ROOT = Path(__file__).resolve().parent.parent
EXIT_SECURITY_ERROR = 126


@pytest.fixture
def no_bwrap(monkeypatch):
    """A machine where bubblewrap is not installed."""
    executor = SandboxExecutor()
    monkeypatch.setattr(executor, "bwrap_available", False, raising=False)
    return executor


def test_levels_that_promise_isolation_refuse_without_bubblewrap(no_bwrap):
    for level in (SandboxLevel.LEVEL_1_READONLY, SandboxLevel.LEVEL_2_ISOLATED_DEV):
        code, out, _ = no_bwrap.execute(
            "echo hi", SandboxConfig(level=level, timeout_seconds=5), "test"
        )

        assert code == EXIT_SECURITY_ERROR, f"{level} ran unisolated instead of refusing"
        assert "bubblewrap" in out, "the refusal must name the missing package"


def test_the_refusal_never_reveals_command_output(no_bwrap):
    code, out, _ = no_bwrap.execute(
        "echo SECRET-CANARY", SandboxConfig(level=SandboxLevel.LEVEL_1_READONLY, timeout_seconds=5), "t"
    )

    assert code == EXIT_SECURITY_ERROR
    assert "SECRET-CANARY" not in out, "a refused command must not be executed first"


def test_level_3_still_runs_because_it_promises_no_isolation(no_bwrap):
    # LEVEL_3 means "host as this user" on purpose: skills need the session bus
    # and display to reach `speak` and `open_application`. Refusing it would
    # break every skill, so the exemption is deliberate, not an oversight.
    code, out, _ = no_bwrap.execute(
        "echo hi", SandboxConfig(level=SandboxLevel.LEVEL_3_HOST_USER, timeout_seconds=5), "t"
    )

    assert code == 0
    assert out.strip() == "hi"


def test_blocked_binaries_are_refused_even_with_bubblewrap_present(monkeypatch):
    executor = SandboxExecutor()
    monkeypatch.setattr(executor, "bwrap_available", True, raising=False)

    code, out, _ = executor.execute(
        "mkfs.ext4 /dev/sda", SandboxConfig(level=SandboxLevel.LEVEL_1_READONLY, timeout_seconds=5), "t"
    )

    assert code == EXIT_SECURITY_ERROR
    assert "blocked" in out.lower()


@pytest.mark.parametrize(
    "command",
    [
        "mkfs.ext4 /dev/sda",
        "/sbin/mkfs.btrfs /dev/sdb",
        "dd if=/dev/zero of=/dev/sda",
        "/bin/dd if=/dev/zero of=/dev/sda",
        "mount /dev/sda /mnt",
        "/bin/mount -o loop /dev/loop0 /mnt",
        "umount /mnt",
        "shutdown -h now",
        "reboot",
    ],
)
def test_dangerous_binaries_cannot_avoid_the_blocklist_by_being_realistic(command):
    # `blocked in command.split()` matched whole tokens, so it blocked the bare
    # word `mkfs` and let `mkfs.ext4` through - which is the only form anyone
    # actually types. Verified live: `mkfs.ext4 /dev/sda` reached the executor
    # and ran. These are the forms that must now be caught.
    assert _first_blocked_binary(command, DANGEROUS_BINARIES) is not None


@pytest.mark.parametrize(
    "command", ["echo add", "ddrescue /dev/sda", "echo mounting", "ls /mnt", "get_datetime"]
)
def test_ordinary_commands_are_not_false_positives(command):
    # No substring matching: `add` and `ddrescue` are not `dd`.
    assert _first_blocked_binary(command, DANGEROUS_BINARIES) is None


def test_a_refused_command_is_never_executed_even_when_the_binary_exists(monkeypatch):
    # Regression guard for the bypass above, end to end through execute() with
    # bubblewrap "available", so this fails if the check is ever moved after
    # dispatch or weakened back to exact-token matching.
    executor = SandboxExecutor()
    monkeypatch.setattr(executor, "bwrap_available", True, raising=False)

    for command in ("mkfs.ext4 /dev/sda", "/sbin/mkfs.btrfs /dev/sdb", "/bin/dd if=/dev/zero of=/dev/sda"):
        code, out, _ = executor.execute(
            command, SandboxConfig(level=SandboxLevel.LEVEL_1_READONLY, timeout_seconds=5), "t"
        )
        assert code == EXIT_SECURITY_ERROR, f"{command!r} was not blocked"
        assert "/dev/sda" not in out or "blocked" in out.lower()


def test_the_packaging_manifests_actually_declare_the_sandbox():
    # The regression that made the above reachable in the field: bubblewrap
    # was absent from this repo's own PKGBUILD/DEBIAN control, so a package
    # built from source had no sandbox binary and every skill ran unconfined.
    for path in (REPO_ROOT / "PKGBUILD", REPO_ROOT / "DEBIAN/control"):
        text = path.read_text().lower()
        assert "bubblewrap" in text, f"{path.name} does not declare bubblewrap"
        assert "libsecret" in text, f"{path.name} does not declare libsecret"
