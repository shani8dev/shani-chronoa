"""qr_code (zbar/qrencode, real binaries when present) and android_device's consent.

The adb engine and its fake-adb tests are shani-link's (tests/test_android.py there)."""

import os
import shutil
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


def test_android_is_gated():
    """**The key is shut by this test.** `phone-control-enabled` is in the
    `everyday` preset and ships on, so the refusal below was never reached."""
    from shani_chronoa.config import ChronoaConfig

    ChronoaConfig().set("phone-control-enabled", "false")
    assert ad._run({"action": "devices"}).startswith("Refusing")


def test_android_with_its_switch_on_asks_the_engine(monkeypatch):
    """The skill is consent plus shani-link's engine (shani_link/android.py,
    whose own tests drive a fake adb): with the switch on, the arguments reach it."""
    ChronoaConfig().set("phone-control-enabled", "true")
    seen = []
    monkeypatch.setattr(ad._engine, "_run", lambda arguments: seen.append(arguments) or "Connected: R58N123")
    assert ad._run({"action": "devices"}) == "Connected: R58N123"
    assert seen == [{"action": "devices"}]


def test_android_switch_off_never_reaches_the_engine(monkeypatch):
    ChronoaConfig().set("phone-control-enabled", "false")
    monkeypatch.setattr(ad._engine, "_run", lambda arguments: pytest.fail("the engine ran with the switch off"))
    assert ad._run({"action": "screenshot"}).startswith("Refusing")
