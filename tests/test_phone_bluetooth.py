"""The phone over plain Bluetooth (`phone_bluez`): the parts checkable without one.

Measured against a real phone on 2026-10-08 (see AGENTS.md); these pin the
formats so a later edit cannot quietly break them.
"""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "usr" / "lib" / "shani-chronoa"))

from shani_chronoa import phone as ph  # noqa: E402
from shani_chronoa import phone_bluez as pb  # noqa: E402


















class _Run:
    """`_run` stand-in: answers get-property from a state the calls change."""

    def __init__(self, state="paused", obeys=True):
        self.state, self.obeys, self.calls = state, obeys, []

    def __call__(self, argv):
        import subprocess
        self.calls.append(argv)
        if "get-property" in argv:
            prop = argv[-1]
            out = f's "{self.state}"' if prop == "Status" else 'a{sv} 2 "Title" s "Song" "Artist" s "Band"'
            return subprocess.CompletedProcess(argv, 0, out, "")
        if "call" in argv and self.obeys:
            self.state = {"Play": "playing", "Pause": "paused"}.get(argv[-1], self.state)
        return subprocess.CompletedProcess(argv, 0, "", "")






def test_media_control_falls_back_to_the_phone_when_no_mpris_player_runs(monkeypatch):
    from shani_chronoa.skills import media_control as mc
    monkeypatch.setattr(mc._engine, "players", lambda: [])
    monkeypatch.setattr(pb, "avrcp_players", lambda: [("/p", "acer ZX")])
    monkeypatch.setattr(pb, "_run", _Run(state="playing"))
    assert mc._run({"action": "status"}) == "acer ZX is playing: Song by Band."
    assert mc._run({"action": "pause"}) == "Done: pause on acer ZX."
