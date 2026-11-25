"""The timezone actuator's post-condition, from the root suite's actuator file.

`set_timezone` moved to shani-sysadmin with its post-condition; this is the
same three-case check (effect present / absent / nothing to check), run against
the module at its new path.
"""

from __future__ import annotations

import os

import pytest

from shani_chronoa import verification
from shani_chronoa.verification import Verdict


@pytest.fixture
def bindir(tmp_path, monkeypatch):
    d = tmp_path / "bin"
    d.mkdir()
    monkeypatch.setenv("PATH", f"{d}:{os.environ.get('PATH', '')}")
    return d


def _verify(module, arguments, tool=None):
    return verification.verify(f"shani_sysadmin.skills.{module}", arguments, tool=tool)


def test_set_timezone(bindir):
    path = bindir / "timedatectl"
    path.write_text("#!/bin/sh\necho Asia/Kolkata\n")
    path.chmod(path.stat().st_mode | 0o111)
    assert (
        _verify("set_timezone", {"action": "set", "timezone": "Asia/Kolkata"}).verdict
        is Verdict.VERIFIED
    )
    assert (
        _verify("set_timezone", {"action": "set", "timezone": "Europe/Paris"}).verdict
        is Verdict.FAILED
    )
    assert _verify("set_timezone", {"action": "status"}).verdict is Verdict.UNVERIFIED
