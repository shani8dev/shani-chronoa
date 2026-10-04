"""qr_code (zbar/qrencode, real binaries when present) and android_device (adb, a fake one on PATH)."""

import os
import shutil
import stat
import textwrap
from pathlib import Path

import pytest

from shani_chronoa.config import ChronoaConfig
from shani_chronoa.skills import android_device as ad
from shani_chronoa.skills import qr_code as qr

HOME = lambda: Path(os.environ["HOME"])  # noqa: E731


@pytest.mark.skipif(not (shutil.which("qrencode") and shutil.which("zbarimg")), reason="needs qrencode + zbar")
def test_qr_round_trip_with_the_real_tools():
    out = qr._run({"action": "create", "text": "WIFI:T:WPA;S:home;P:p@ss w0rd;;", "output": "~/q.png"})
    assert "decodes back to the same text" in out, out
    read = qr._run({"action": "read", "path": str(HOME() / "q.png")})
    assert "WIFI:T:WPA;S:home;P:p@ss w0rd;;" in read and "nothing was opened" in read
    assert "already exists" in qr._run({"action": "create", "text": "x", "output": "~/q.png"})


def test_qr_screen_read_needs_vision_consent():
    ChronoaConfig().set("vision-sense-enabled", "false")
    if shutil.which("zbarimg"):
        assert "not permitted" in qr._run({"action": "read", "screen": True})


FAKE_ADB = r"""#!/bin/sh
[ "$1" = "-s" ] && shift 2
case "$1 $2" in
  "devices -l") printf 'List of devices attached\nR58N123 device usb:1-1 product:x model:Pixel_8 device:shiba\n' ;;
  "shell getprop") case "$3" in ro.product.manufacturer) echo Google;; ro.product.model) echo "Pixel 8";;
                   ro.build.version.release) echo 15;; ro.build.version.security_patch) echo 2026-09-05;; esac ;;
  "shell dumpsys") printf 'Current Battery Service state:\n  AC powered: false\n  USB powered: true\n  level: 76\n' ;;
  "shell df") printf 'Filesystem Size Used Avail Use%% Mounted\n/dev/fuse 110G 40G 70G 37%% /storage/emulated\n' ;;
  "shell pm") printf 'package:com.whatsapp\npackage:org.mozilla.firefox\n' ;;
  "exec-out screencap") printf '\211PNG\r\n\032\nfake' ;;
  "shell am") echo "Starting: Intent { act=android.intent.action.VIEW dat=$7 }" ;;
  push*) echo "1 file pushed" ;;
  *) echo "unexpected: $*" >&2; exit 1 ;;
esac
"""


@pytest.fixture
def fake_adb(tmp_path, monkeypatch):
    bindir = tmp_path / "bin"
    bindir.mkdir()
    adb = bindir / "adb"
    adb.write_text(FAKE_ADB)
    adb.chmod(adb.stat().st_mode | stat.S_IEXEC)
    monkeypatch.setenv("PATH", f"{bindir}:{os.environ['PATH']}")
    ChronoaConfig().set("phone-control-enabled", "true")


def test_android_is_gated():
    assert ad._run({"action": "devices"}).startswith("Refusing")


def test_android_info_screenshot_apps_push_open(fake_adb):
    assert "R58N123 Pixel 8 (ready)" in ad._run({"action": "devices"})
    info = ad._run({"action": "info"})
    assert "Google Pixel 8, Android 15" in info and "battery 76%, charging over USB" in info and "70G free" in info
    shot = ad._run({"action": "screenshot", "output": "~/p.png"})
    assert shot.startswith("Saved") and (HOME() / "p.png").read_bytes().startswith(b"\x89PNG")
    assert "com.whatsapp" in ad._run({"action": "apps", "query": "whats"}) 
    (HOME() / "f.txt").write_text("x")
    assert "Copied f.txt" in ad._run({"action": "push", "path": "~/f.txt"})
    assert "/sdcard/" in ad._run({"action": "push", "path": "~/f.txt", "to": "/system/bin"})
    assert "Opened https://shani.dev" in ad._run({"action": "open_url", "url": "https://shani.dev"})
    assert "http" in ad._run({"action": "open_url", "url": "javascript:alert(1)"})


def test_android_no_device_says_how(fake_adb, monkeypatch):
    monkeypatch.setattr(ad, "_devices", lambda: [])
    assert "USB debugging" in ad._run({"action": "info"})
    monkeypatch.setattr(ad, "_devices", lambda: [("X1", "unauthorized", "")])
    assert "allow USB debugging" in ad._run({"action": "info"})
