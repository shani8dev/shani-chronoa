"""The phone surface: GSConnect/KDE Connect, with no phone needed - the link is faked at its CLI and Python seams.

"No phone link", "no paired phone" and "paired but unreachable" are three
answers, and each must come back as itself.
"""

import stat

import pytest

from shani_chronoa import phone as ph
from shani_chronoa import triggers as trig
from shani_chronoa.skills import phone as skill


def _stub(bindir, name, script):
    p = bindir / name
    p.write_text("#!/bin/sh\n" + script)
    p.chmod(p.stat().st_mode | stat.S_IXUSR)


@pytest.fixture
def kde(tmp_path, monkeypatch):
    d = tmp_path / "bin"
    d.mkdir()
    calls = tmp_path / "calls"
    _stub(d, "kdeconnect-cli", f'''echo "$@" >> {calls}
case "$*" in
  "-l --id-name-only") echo "abc123 Pixel 8";;
  "-a --id-only") cat {tmp_path}/up 2>/dev/null;;
  *) ;;
esac''')
    _stub(d, "busctl", 'case "$*" in *charge*) echo "i 15";; *isCharging*) echo "b false";; esac')
    monkeypatch.setenv("PATH", f"{d}:/usr/bin:/bin")
    monkeypatch.setenv("XDG_CURRENT_DESKTOP", "KDE")
    monkeypatch.setattr(skill, "_consent", lambda c: (True, ""))
    return tmp_path, calls


def test_status_and_unreachable(kde):
    tmp, _ = kde
    assert "Pixel 8: not reachable" in skill._run({"action": "status"})
    assert "not reachable right now" in skill._run({"action": "ring"})
    (tmp / "up").write_text("abc123\n")
    assert "Pixel 8: reachable, battery 15%" in skill._run({"action": "status"})


def test_ring_and_share_reach_the_cli(kde, tmp_path):
    tmp, calls = kde
    (tmp / "up").write_text("abc123\n")
    f = tmp_path / "photo.jpg"
    f.write_text("x")
    assert skill._run({"action": "ring"}) == "Ringing Pixel 8."
    assert "Sent Pixel 8: photo.jpg" in skill._run({"action": "share", "target": str(f)})
    log = calls.read_text()
    assert "-d abc123 --ring" in log and f"-d abc123 --share {f}" in log
    assert "not a file" in skill._run({"action": "share", "target": str(tmp_path / "nope")})


def test_no_link_is_not_no_phone(monkeypatch, tmp_path):
    monkeypatch.setenv("PATH", str(tmp_path))
    monkeypatch.setattr(ph, "_gsconnect_daemon", lambda: None)
    monkeypatch.setattr(skill, "_consent", lambda c: (True, ""))
    assert "cannot reach a phone link" in skill._run({"action": "status"})
    assert trig.read_phone("connected").status == trig.SIGNAL_UNAVAILABLE


def test_phone_triggers(kde):
    tmp, _ = kde
    assert trig.read_phone("connected").event is None
    (tmp / "up").write_text("abc123\n")
    assert trig.read_phone("connected:pixel").event is not None
    assert trig.read_phone("disconnected").event is None
    low = trig.read_phone("battery-below:20")
    assert low.event is not None and "15%" in low.event.summary
    assert trig.read_phone("battery-below:10").event is None
    assert trig.read_phone("connected:iphone").status == trig.SIGNAL_UNAVAILABLE
    assert trig.read_phone("nearby").status == trig.SIGNAL_UNAVAILABLE


def test_gated_off_by_default(monkeypatch):
    class Off:
        def get_bool(self, key, default=False):
            return False
    monkeypatch.setattr(skill, "ChronoaConfig", Off)
    assert "phone-control-enabled" in skill._run({"action": "status"})
