"""Chronoa Remote: the HID reports, the GATT tree, and the gates (no Bluetooth needed).

Measured live 2026-10-09 with an Android phone: it paired over LE, subscribed to
both input reports, and took volume up/down and typed text.
"""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "usr" / "lib" / "shani-chronoa"))
pytest.importorskip("gi")

from shani_chronoa import ble_peripheral as bp  # noqa: E402
















def test_the_skill_presses_nothing_while_its_switch_is_off(monkeypatch):
    from shani_chronoa.skills import phone_remote as skill

    class Off:
        def get_bool(self, key, default=False):
            return False
    monkeypatch.setattr(skill, "ChronoaConfig", Off)
    monkeypatch.setattr(bp, "ask", lambda *a, **k: pytest.fail("the remote was asked with the switch off"))
    assert "phone-remote-enabled" in skill._run({"action": "press", "key": "shutter"})
