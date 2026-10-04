"""desktop_setting: an allowlist mapped to gsettings (GNOME) and kconfig (Plasma), every change read back.

Faked at the tools (gsettings / kreadconfig6 / kwriteconfig6 keeping state in a
file), so the test never writes the real desktop's settings.
"""

import stat

import pytest

from shani_chronoa.skills import desktop_setting as ds


def _stub(d, name, script):
    p = d / name
    p.write_text("#!/bin/sh\n" + script)
    p.chmod(p.stat().st_mode | stat.S_IXUSR)


@pytest.fixture
def tools(tmp_path, monkeypatch):
    d = tmp_path / "bin"
    d.mkdir()
    st = tmp_path / "state"
    st.mkdir()
    _stub(d, "gsettings", f'if [ "$1" = get ]; then cat {st}/"$3" 2>/dev/null || echo true; else echo "$4" > {st}/"$3"; fi\n')
    _stub(d, "kreadconfig6", f'cat {st}/"$6" 2>/dev/null\n')
    _stub(d, "kwriteconfig6", f'for a; do last="$a"; done; echo "$last" > {st}/"$6"\n')
    monkeypatch.setenv("PATH", f"{d}:/usr/bin:/bin")
    monkeypatch.setattr(ds, "_consent", lambda c: (True, ""))
    return st


def test_gnome_round_trip(tools, monkeypatch):
    monkeypatch.setenv("XDG_CURRENT_DESKTOP", "GNOME")
    assert ds._run({"setting": "animations"}) == "Window and menu animations: True."
    assert "True -> False (verified)" in ds._run({"setting": "animations", "value": "off"})
    assert ds._post_condition({"setting": "animations", "value": "false"})[0] is True
    assert "not a setting this desktop (gnome) has" in ds._run({"setting": "single_click"})


def test_plasma_round_trip(tools, monkeypatch):
    monkeypatch.setenv("XDG_CURRENT_DESKTOP", "KDE")
    assert "True -> False (verified)" in ds._run({"setting": "animations", "value": "false"})
    assert (tools / "AnimationDurationFactor").read_text().strip() == "0"
    assert "24 -> 48 (verified)" in ds._run({"setting": "cursor_size", "value": "48"})
    assert "not a setting this desktop (plasma) has" in ds._run({"setting": "clock_24h"})


def test_values_are_checked_and_the_list_is_closed(tools, monkeypatch):
    monkeypatch.setenv("XDG_CURRENT_DESKTOP", "GNOME")
    (tools / "cursor-size").write_text("int32 24\n")
    assert "between 16 and 128" in ds._run({"setting": "cursor_size", "value": "9000"})
    (tools / "cursor-size").write_text("garbage\n")
    assert "not a value it should hold" in ds._run({"setting": "cursor_size"}), "an odd reading is not a crash"
    assert "expected true or false" in ds._run({"setting": "animations", "value": "maybe"})
    assert "must be one of" in ds._run({"setting": "org.gnome.desktop.interface gtk-theme"})


def test_changing_is_gated(tools, monkeypatch):
    monkeypatch.setenv("XDG_CURRENT_DESKTOP", "GNOME")
    monkeypatch.setattr(ds, "_consent", lambda c: (False, "turned off"))
    assert "Refusing" in ds._run({"setting": "animations", "value": "false"})
    assert ds._run({"setting": "animations"}).endswith("True."), "reading needs no permission"
