"""aria2c as a download engine, and socat for serial devices.

Two tools that were on the image but invoked by nothing:

- `download_file` now takes `engine: "aria2c"` - parallel connections for a
  large file from a server that accepts them. Auto never picks it (parallel
  ranges against a throttling server looks like a slow download).
- `user_driver` gains `device_serial` - read from, or write to, a serial
  device through `socat`, the user-space counterpart of the I2C register
  access already there. Writes are gated; reads are not.

These tests pin the contracts the way the existing suites do: a stand-in
binary, the exit code and the file checked separately.
"""

import os
import pathlib
import sys

import pytest

_REPO = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_REPO / "usr" / "lib" / "shani-chronoa"))

from shani_chronoa.skills import download_file as DL  # noqa: E402
from shani_chronoa.skills import user_driver as UD  # noqa: E402


def _fake_binary(tmp_path, monkeypatch, name, body, code=0, out=""):
    bindir = tmp_path / "bin"
    bindir.mkdir(exist_ok=True)
    script = bindir / name
    script.write_text(
        "#!/usr/bin/env python3\n"
        "import sys\n"
        f"sys.stdout.write({out!r})\n"
        f"sys.exit({code})\n")
    script.chmod(0o755)
    monkeypatch.setenv("PATH", f"{bindir}:{os.environ['PATH']}")
    return script


# -------------------------------------------------------------- aria2c engine

def test_aria2c_success_reports_the_file(tmp_path, monkeypatch):
    bindir = tmp_path / "bin"
    bindir.mkdir(exist_ok=True)
    script = bindir / "aria2c"
    script.write_text(
        "#!/usr/bin/env python3\n"
        "import sys\n"
        "args = sys.argv[1:]\n"
        "d = [a for a in args if a.startswith('--dir=')][0][6:]\n"
        "o = [a for a in args if a.startswith('--out=')][0][6:]\n"
        "open(d + '/' + o, 'w').write('payload')\n")
    script.chmod(0o755)
    monkeypatch.setenv("PATH", f"{bindir}:{os.environ['PATH']}")

    said = DL._run({"url": "https://example.com/big.iso", "engine": "aria2c",
                    "destination": str(tmp_path)})
    assert "payload" not in said
    assert (tmp_path / "big.iso").read_text() == "payload"
    assert "aria2c" in said


def test_aria2c_failure_is_not_a_download(tmp_path, monkeypatch):
    _fake_binary(tmp_path, monkeypatch, "aria2c", "", code=1, out="error")
    said = DL._run({"url": "https://example.com/big.iso", "engine": "aria2c",
                    "destination": str(tmp_path)})
    assert "failed" in said.lower()
    assert not (tmp_path / "big.iso").exists()


def test_aria2c_missing_names_the_package(tmp_path, monkeypatch):
    monkeypatch.setenv("PATH", str(tmp_path))   # empty bindir, no aria2c
    said = DL._run({"url": "https://example.com/big.iso", "engine": "aria2c"})
    assert "aria2c" in said


def test_auto_never_picks_aria2c(tmp_path, monkeypatch):
    """Auto is curl-then-wget by design; parallel ranges are opt-in."""
    _fake_binary(tmp_path, monkeypatch, "aria2c", "", code=0)
    monkeypatch.setattr(DL.shutil, "which", lambda name: None)
    said = DL._run({"url": "https://example.com/x"})
    assert "curl" in said or "install" in said.lower()


# -------------------------------------------------------------- serial devices

class _FakePath:
    """A /dev that exists only for the test; anything else is the real path."""
    from pathlib import Path as _RealPath

    def __init__(self, *args):
        raw = str(args[0]) if args else ""
        self._real = _FakePath._RealPath(raw)
        self._dev = raw == "/dev" or raw.startswith("/dev/")

    def __str__(self):
        return str(self._real)

    def exists(self):
        return True if self._dev else self._real.exists()

    def glob(self, pattern=""):
        if self._dev:
            return [_FakePath("/dev/ttyUSB0"), _FakePath("/dev/ttyACM1")]
        return self._real.glob(pattern)

    def iterdir(self):
        return self._real.iterdir()

    def read_text(self, *a, **k):
        return self._real.read_text(*a, **k)

    @property
    def name(self):
        return self._real.name


def test_probe_lists_serial_ports(tmp_path, monkeypatch):
    monkeypatch.setattr(UD, "Path", _FakePath)
    said = UD._run_probe({})
    assert "ttyUSB0" in said and "ttyACM1" in said


def test_serial_read_reports_what_arrived(tmp_path, monkeypatch):
    _fake_binary(tmp_path, monkeypatch, "socat", "", code=0, out="HELLO\r\n")
    monkeypatch.setattr(UD, "Path", _FakePath)
    said = UD._run_serial({"device": "/dev/ttyUSB0"})
    assert "HELLO" in said


def test_serial_read_of_a_silent_device_says_so(tmp_path, monkeypatch):
    _fake_binary(tmp_path, monkeypatch, "socat", "", code=0, out="")
    monkeypatch.setattr(UD, "Path", _FakePath)
    said = UD._run_serial({"device": "/dev/ttyUSB0"})
    assert "Nothing arrived" in said


def test_serial_write_needs_consent(tmp_path, monkeypatch):
    _fake_binary(tmp_path, monkeypatch, "socat", "", code=0)
    monkeypatch.setattr(UD, "Path", _FakePath)
    monkeypatch.setattr(UD, "_consent", lambda config: (False, "off"))
    said = UD._run_serial({"device": "/dev/ttyUSB0", "action": "write", "data": "AT"})
    assert "Refusing" in said


def test_serial_without_a_device_path_is_a_question(tmp_path):
    assert "full device path" in UD._run_serial({"device": "ttyUSB0"})
