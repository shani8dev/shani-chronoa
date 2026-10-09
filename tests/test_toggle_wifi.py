"""toggle_wifi against a fake nmcli and a fake rfkill + sysfs tree.

Never the real radio: PATH holds *only* the fake bin directory, and every test
that could switch asserts first that `nmcli`/`rfkill` resolve there. The fake
nmcli keeps its radio state in a file, so the skill's read-back is exercised
against something that actually changed (or, in the controls, did not).
"""

import shutil
import stat
from pathlib import Path

import pytest

from shani_chronoa.skills import airplane_mode as air
from shani_chronoa.skills import toggle_wifi as tw


def _w(p: Path, **files):
    p.mkdir(parents=True, exist_ok=True)
    for k, v in files.items():
        (p / k).write_text(f"{v}\n")


class _On:
    def get_bool(self, key, default=False):
        return key == "radio-control-enabled"


class _Off:
    def get_bool(self, key, default=False):
        return False


@pytest.fixture
def env(tmp_path, monkeypatch):
    root = tmp_path / "rfkill"
    _w(root / "rfkill0", type="wlan", name="phy0", soft=0, hard=0)
    _w(root / "rfkill1", type="bluetooth", name="hci0", soft=0, hard=0)
    monkeypatch.setattr(air, "RFKILL_DIR", root)
    bindir = tmp_path / "bin"
    bindir.mkdir()
    monkeypatch.setenv("PATH", str(bindir))
    return {"rf": root, "bin": bindir, "log": tmp_path / "argv.log", "state": tmp_path / "nm.state"}


def _fake_nmcli(env, initial="enabled", obey=True, fail=False):
    env["state"].write_text(initial + "\n")
    change = (f'case "$3" in on) echo enabled > "{env["state"]}";; off) echo disabled > "{env["state"]}";; esac'
              if obey else ":")
    script = env["bin"] / "nmcli"
    script.write_text(
        "#!/bin/sh\n"
        f'echo "nmcli $*" >> "{env["log"]}"\n'
        + ('echo "Error: not authorized" >&2; [ $# -eq 3 ] && exit 1\n' if fail else "")
        + f'if [ "$1 $2" = "radio wifi" ] && [ $# -eq 2 ]; then read s < "{env["state"]}"; echo "$s"; exit 0; fi\n'
        + f'if [ "$1 $2" = "radio wifi" ] && [ $# -eq 3 ]; then {change}; exit 0; fi\n'
        "exit 2\n")
    script.chmod(script.stat().st_mode | stat.S_IXUSR)
    assert shutil.which("nmcli") == str(script), "the fake nmcli must be the one found"


def _fake_rfkill(env):
    script = env["bin"] / "rfkill"
    script.write_text(
        "#!/bin/sh\n"
        f'echo "rfkill $*" >> "{env["log"]}"\n'
        f'v=0; [ "$1" = block ] && v=1\n'
        f'[ "$2" = wlan ] && echo $v > "{env["rf"]}/rfkill0/soft"\n')
    script.chmod(script.stat().st_mode | stat.S_IXUSR)
    assert shutil.which("rfkill") == str(script)


def _log(env):
    return env["log"].read_text().splitlines() if env["log"].exists() else []


def test_status_is_free_and_reads_networkmanager(env, monkeypatch):
    monkeypatch.setattr(tw, "ChronoaConfig", _Off)
    _fake_nmcli(env, "enabled")
    assert tw._run({"action": "status"}) == "Wi-Fi is on (according to NetworkManager)."
    assert tw._run({}).startswith("Wi-Fi is on")
    assert _log(env) and all(line == "nmcli radio wifi" for line in _log(env)), "status must only read"


def test_switching_is_refused_without_consent_and_nothing_runs(env, monkeypatch):
    monkeypatch.setattr(tw, "ChronoaConfig", _Off)
    _fake_nmcli(env, "enabled")
    out = tw._run({"action": "off"})
    assert "Refusing" in out and "radio-control-enabled" in out
    assert "nmcli radio wifi off" not in _log(env)
    assert env["state"].read_text().strip() == "enabled"


def test_off_and_on_through_nmcli_are_read_back(env, monkeypatch):
    monkeypatch.setattr(tw, "ChronoaConfig", _On)
    _fake_nmcli(env, "enabled")
    assert tw._run({"action": "off"}) == "Wi-Fi is now off (verified with nmcli)."
    assert "nmcli radio wifi off" in _log(env)
    assert tw._post_condition({"action": "off"}) == (True, "Wi-Fi reads off (NetworkManager)")
    assert tw._run({"action": "ON "}) == "Wi-Fi is now on (verified with nmcli)."
    assert "nmcli radio wifi on" in _log(env)
    assert not any(line.startswith("rfkill") for line in _log(env)), "NetworkManager owns the radio"


def test_already_in_state_changes_nothing(env, monkeypatch):
    monkeypatch.setattr(tw, "ChronoaConfig", _On)
    _fake_nmcli(env, "disabled")
    assert "already off" in tw._run({"action": "off"})
    assert "nmcli radio wifi off" not in _log(env)


def test_control_a_radio_that_ignores_the_command_is_not_called_off(env, monkeypatch):
    """The read-back is what decides: an nmcli that exits 0 and changes nothing
    must not produce 'now off'."""
    monkeypatch.setattr(tw, "ChronoaConfig", _On)
    _fake_nmcli(env, "enabled", obey=False)
    out = tw._run({"action": "off"})
    assert "still reads on" in out and "now off" not in out
    assert tw._post_condition({"action": "off"})[0] is False


def test_a_refusing_nmcli_is_reported(env, monkeypatch):
    monkeypatch.setattr(tw, "ChronoaConfig", _On)
    _fake_nmcli(env, "enabled", fail=True)
    out = tw._run({"action": "off"})
    assert out.startswith("nmcli refused to turn Wi-Fi off") and "not authorized" in out


def test_rfkill_fallback_without_networkmanager(env, monkeypatch):
    monkeypatch.setattr(tw, "ChronoaConfig", _On)
    _fake_rfkill(env)
    assert shutil.which("nmcli") is None
    assert tw._run({"action": "status"}) == "Wi-Fi is on (according to rfkill)."
    assert tw._run({"action": "off"}) == "Wi-Fi is now off (verified with rfkill)."
    assert "rfkill block wlan" in _log(env)
    assert (env["rf"] / "rfkill1" / "soft").read_text().strip() == "0", "Bluetooth must be left alone"
    assert tw._run({"action": "on"}) == "Wi-Fi is now on (verified with rfkill)."
    assert "rfkill unblock wlan" in _log(env)


def test_hard_block_is_never_reported_as_on(env, monkeypatch):
    monkeypatch.setattr(tw, "ChronoaConfig", _On)
    _w(env["rf"] / "rfkill0", hard=1)
    _fake_nmcli(env, "enabled")
    status = tw._run({})
    assert status.startswith("Wi-Fi is off.") and "hardware switch or the BIOS" in status
    assert "cannot be turned on from software" in tw._run({"action": "on"})
    assert "nmcli radio wifi on" not in _log(env)


def test_no_tools_and_no_radio(env, monkeypatch):
    monkeypatch.setattr(tw, "ChronoaConfig", _On)
    for d in env["rf"].iterdir():
        for f in d.iterdir():
            f.unlink()
        d.rmdir()
    assert "could not be read" in tw._run({"action": "status"})
    assert "nothing to switch" in tw._run({"action": "off"})


def test_malformed_arguments_get_a_sentence(env, monkeypatch):
    monkeypatch.setattr(tw, "ChronoaConfig", _On)
    _fake_nmcli(env, "enabled")
    assert "status, on or off" in tw._run({"action": "toggle"})
    assert "status, on or off" in tw._run({"action": 5})
    assert "status, on or off" in tw._run(None)  # type: ignore[arg-type]
    assert tw._post_condition({"action": "status"}) is None
    assert not any("radio wifi o" in line for line in _log(env))


def test_registered_as_a_skill():
    assert [s.name for s in tw.SKILLS] == ["toggle_wifi"]
    assert tw.SCHEMA["function"]["name"] == "toggle_wifi"
