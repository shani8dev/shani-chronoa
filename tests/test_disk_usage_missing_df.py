"""A missing `df` reported an errno instead of a fact.

`disk_usage` guards `du` with `shutil.which` before running it, and returns a
sentence naming what is missing and what it would have done. `df` had no such
guard: the only handling was an `except OSError` around the call, so a machine
without `df` answered

    Could not read filesystem usage: df could not be run ([Errno 2] No such
    file or directory: 'df')

which is an errno where the useful fact is that `df` is not installed and which
package provides it. The failure is caught, so it does not crash - it is just
unreadable, and the user is left guessing whether something is wrong with the
disk or with the tool.

Same shape for every other external command in the skills: `xdotool`,
`systemctl`, `nmcli`, `wpctl`, `loginctl`, `bluetoothctl` and `notify-send` are
all either guarded or funneled through `session_problem()`. `df` was the one
that was not, and this is the test that says so.
"""

from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path

import pytest

_REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_REPO / "usr" / "lib" / "shani-chronoa"))

from shani_chronoa.skills import disk_usage as D  # noqa: E402


@pytest.fixture
def no_df(monkeypatch):
    """`df` genuinely absent, two ways at once.

    Hiding it from `which()` alone only proves the guard runs; hiding it from
    the exec as well proves the answer does not depend on the guard being
    there. The old code passed the first and failed only the second - which is
    exactly why it looked handled.
    """
    real_which, real_run = shutil.which, subprocess.run

    monkeypatch.setattr(shutil, "which",
                        lambda n, **kw: None if n == "df" else real_which(n, **kw))

    def run(cmd, *a, **kw):
        if isinstance(cmd, (list, tuple)) and cmd and cmd[0] == "df":
            raise FileNotFoundError(2, "No such file or directory: 'df'")
        return real_run(cmd, *a, **kw)

    monkeypatch.setattr(D.subprocess, "run", run)
    yield


class TestAMissingDfIsNamedNotErrored:
    def test_it_says_df_is_not_installed(self, no_df):
        out = D._run({})
        assert "not installed" in out

    def test_it_does_not_leak_an_errno(self, no_df):
        out = D._run({})
        assert "Errno" not in out, (
            "an errno where the useful fact is a missing tool - the user is "
            "left guessing whether the disk or the command is at fault"
        )
        assert "No such file or directory" not in out

    def test_it_says_what_was_not_done(self, no_df):
        assert "nothing was done" in D._run({}).lower()

    def test_and_names_the_provider(self, no_df):
        out = D._run({})
        assert "package" in out.lower(), (
            "the message should say where df comes from, not just that it is "
            "absent - a user can then install it"
        )


class TestDuKeepsItsOwnGuard:
    """Both commands in this skill are guarded the same way, not one of them."""

    def test_du_is_still_checked_before_running(self):
        source = (_REPO / "usr" / "lib" / "shani-chronoa" / "shani_chronoa"
                  / "skills" / "disk_usage.py").read_text()
        assert 'which("df")' in source
        assert 'which("du")' in source


class TestTheGuardIsCheckedNotJustCaught:
    def test_df_is_checked_before_it_is_run(self, monkeypatch):
        """Structural, so the order cannot be quietly reversed.

        Catching OSError is fine as a backstop; the point is that the user gets
        the sentence, not the errno, and that only happens if the absence is
        checked *first*.
        """
        called = []
        real_which = shutil.which

        def run(cmd, *a, **kw):
            called.append(cmd)
            raise FileNotFoundError(2, "No such file or directory: 'df'")

        # `shutil.which` has no `__wrapped__`, so the first version of this
        # lambda would have raised AttributeError the moment a command other
        # than "df" was looked up - and the test passed only because nothing
        # else ever was.
        monkeypatch.setattr(shutil, "which",
                            lambda n, **kw: None if n == "df" else real_which(n, **kw))
        monkeypatch.setattr(D.subprocess, "run", run)
        D._run({})
        assert not [c for c in called if isinstance(c, (list, tuple)) and c[:1] == ["df"]], (
            "df was run despite being known to be absent"
        )


def test_suite_has_no_unguarded_external_commands():
    """The sweep that found this, kept as a test.

    Walks every skill for a command used in a subprocess call and checks the file
    either guards it with `which()` or imports `session_problem`, which does.
    Adding a skill that shells out without a guard should fail here rather than
    waiting for a user on a machine without that tool.
    """
    COMMANDS = ("xdotool", "systemctl", "nmcli", "wpctl", "loginctl",
                "bluetoothctl", "gdbus", "notify-send", "df", "pactl", "amixer")
    offenders = []
    for path in sorted((_REPO / "usr" / "lib" / "shani-chronoa" / "shani_chronoa"
                        / "skills").glob("*.py")):
        src = path.read_text()
        if "session_problem" in src:
            continue  # guarded centrally
        for command in COMMANDS:
            if f'["{command}"' in src and f'which("{command}")' not in src:
                offenders.append(f"{path.name}: {command}")
    assert not offenders, (
        "skills run these commands without checking they exist, so a machine "
        f"without them gets a raw OSError: {offenders}"
    )
