"""`download_file` with `wget` instead of `curl`.

The first version had **no fallback**, so a machine with `wget` and no `curl` -
the normal Debian-family layout - could not download anything from a skill whose
only job is downloading. `wget` ships in the same `shani-tools-network` package.

All exit codes and stderr lines below are measured against the real `wget`
(1.25) on this box.

**The two tools' contracts are opposite, which is the whole of the work:**

                            curl                       wget --no-verbose
    success                 rc=0, silent               rc=0, silent
    a 404                   rc=22, "curl: (22)"        rc=8, "ERROR 404: Not Found."
    unresolvable address    rc=6, "curl: (6)"          rc=4, "unable to resolve host"
"""
from __future__ import annotations

import os
import pathlib
import shutil
import sys
import subprocess
from pathlib import Path

import pytest

_REPO = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_REPO / "usr" / "lib" / "shani-chronoa"))

from shani_chronoa.skills import download_file as DF  # noqa: E402


@pytest.fixture
def no_curl(monkeypatch):
    """`curl` absent, `wget` present - the situation the fallback is for."""
    real = shutil.which
    monkeypatch.setattr(DF.shutil, "which",
                        lambda name: None if name == "curl" else real(name))
    return real


@pytest.fixture
def no_tool(monkeypatch):
    monkeypatch.setattr(DF.shutil, "which", lambda name: None)


# --- the chain -----------------------------------------------------------------

def test_curl_still_wins_when_both_are_present(monkeypatch, tmp_path):
    """`wget` is a fallback, not a replacement - the curl path reports a rate
    and an HTTP status that the wget path deliberately does not."""
    target = tmp_path / "via-curl.html"
    out = DF._run({"url": "https://example.com/", "destination": str(target)})
    assert "with wget" not in out, out
    assert "HTTP" in out or "bytes" in out, out


def test_wget_is_used_when_curl_is_absent(no_curl, tmp_path):
    target = tmp_path / "via-wget.html"
    out = DF._run({"url": "https://example.com/", "destination": str(target)})
    assert "with wget" in out, out
    assert target.stat().st_size > 0


def test_no_tool_at_all_names_the_package(no_tool, tmp_path):
    out = DF._run({"url": "https://example.com/",
                   "destination": str(tmp_path / "x")})
    assert "curl" in out
    assert "not installed" in out


# --- wget's own diagnostics are quoted, not invented --------------------------

def test_a_404_quotes_wget_not_curl(no_curl, tmp_path):
    """**The status codes are not the same tool's.** A 404 is 22 for curl and 8
    for wget, so a table copied between them misreports every server error."""
    out = DF._run({"url": "https://example.com/nosuchpage",
                   "destination": str(tmp_path / "missing.html")})
    assert "error status" in out
    assert "404" in out, out
    assert "Not Found" in out, out
    assert "curl" not in out.lower().replace("curl is not installed", "")


def test_an_unresolvable_address_is_reported_as_such(no_curl, tmp_path):
    out = DF._run({"url": "https://nosuchdomain.invalid/",
                   "destination": str(tmp_path / "nope.html")})
    assert "could not be resolved" in out
    assert "unable to resolve host" in out, out


def test_a_success_reports_a_real_size(no_curl, tmp_path):
    target = tmp_path / "sized.html"
    out = DF._run({"url": "https://example.com/", "destination": str(target)})
    assert str(target.stat().st_size) in out


# --- the shapes that are easy to get wrong -------------------------------------

def test_no_verbose_is_used_never_quiet(no_curl, tmp_path, monkeypatch):
    """**`wget -q` produces an exit code and nothing on stderr** - measured, so a
    reader that quotes stderr quotes nothing and every failure becomes
    "reason unknown"."""
    argv = []
    real = subprocess.run

    def watch(command, *args, **kwargs):
        if command and command[0] == "wget":
            argv.append(command)
        return real(command, *args, **kwargs)

    monkeypatch.setattr(DF.subprocess, "run", watch)
    DF._run({"url": "https://example.com/",
             "destination": str(tmp_path / "flags.html")})
    assert argv, "wget was never run"
    flags = argv[0]
    assert "--no-verbose" in flags, flags
    assert "-q" not in flags and "--quiet" not in flags, flags
    # And the timeout is passed **with the skill's own value**, because an
    # unbounded wget is the one that hangs a skill for minutes. The first version
    # asserted only that the flag existed, which `--timeout=3600` also satisfies -
    # a control that cannot fail.
    timeout = [f for f in flags if f.startswith("--timeout=")]
    assert timeout, flags
    assert timeout[0] == "--timeout=%d" % DF._TIMEOUT, timeout


def test_a_zero_byte_success_is_not_claimed_as_an_empty_file(no_curl, tmp_path):
    """A zero exit with a zero-byte file is also what a server sending only
    headers looks like, and claiming to have downloaded an empty file is the
    confident answer."""
    target = tmp_path / "empty.html"
    monkey = None

    class Done:
        returncode = 0
        stdout = ""
        stderr = ""

    import shani_chronoa.skills.download_file as module
    old_run = module.subprocess.run
    module.subprocess.run = lambda *a, **k: Done()
    try:
        target.write_bytes(b"")
        out = module._run({"url": "https://example.com/",
                           "destination": str(target)})
    finally:
        module.subprocess.run = old_run
    assert "0 bytes" in out
    assert "nothing here claims which" in out


def test_a_timeout_is_not_a_404(no_curl, tmp_path, monkeypatch):
    def boom(*a, **k):
        raise subprocess.TimeoutExpired("wget", 60)

    monkeypatch.setattr(DF.subprocess, "run", boom)
    out = DF._run({"url": "https://example.com/",
                   "destination": str(tmp_path / "slow.html")})
    assert "did not finish" in out
    assert "not a statement about whether the file exists" in out


def test_the_url_is_still_validated_before_wget_runs(no_curl, tmp_path):
    """The guard is the curl path's and it does not move because the tool does."""
    out = DF._run({"url": "file:///etc/passwd",
                   "destination": str(tmp_path / "x")})
    assert "file://" in out or "scheme" in out.lower(), out


def test_the_codes_are_wgets_not_curls():
    """**A 404 is 8 here and 22 for curl.** Asserted against the measured table
    rather than against a shared constant, because copying the table between the
    two tools is the mistake this whole module guards."""
    assert DF._WGET_CODES[8] is not None and "error status" in DF._WGET_CODES[8]
    assert DF._WGET_CODES[4] is not None and "resolved" in DF._WGET_CODES[4]
    assert DF._WGET_CODES[0] is None
    assert 22 not in DF._WGET_CODES, "22 is curl's 404, not wget's"
    assert 404 not in DF._WGET_CODES
