"""open_settings against fake gnome-control-center / systemsettings / kcmshell6.

Nothing opens on the real desktop: PATH holds only the fake bin directory, and
each fake just logs its argv. The fakes model the three outcomes the skill
tells apart - a new window that stays up, an already-running Settings that
takes the request and exits 0, and a launcher that fails.
"""

import shutil
import stat

import pytest

from shani_chronoa.skills import open_settings as st

#: the GNOME panel ids verified on this machine (gnome-control-center 50.3,
#: `gnome-control-center --list` plus the panels' .desktop Exec= lines)
GNOME_50_PANELS = {"applications", "background", "bluetooth", "color", "display", "keyboard", "mouse",
                   "multitasking", "network", "wifi", "notifications", "online-accounts", "power",
                   "printers", "privacy", "search", "sharing", "sound", "system", "universal-access",
                   "wacom", "wellbeing", "wwan"}
GNOME_50_SYSTEM_SUBPAGES = {"about", "datetime", "region", "users"}


@pytest.fixture
def fake(tmp_path, monkeypatch):
    bindir = tmp_path / "bin"
    bindir.mkdir()
    log = tmp_path / "argv.log"
    monkeypatch.setenv("PATH", str(bindir))
    monkeypatch.setattr(st, "ALIVE_SECONDS", 0.3)

    def make(name, behaviour="exit0"):
        body = {"exit0": "exit 0", "stay": "exec sleep 2", "fail": 'echo "Unable to init server" >&2; exit 1'}
        script = bindir / name
        # `exec sleep` needs sleep: the only absolute path in here
        script.write_text(f'#!/bin/sh\necho "{name} $*" >> "{log}"\n'
                          + body[behaviour].replace("exec sleep", "exec /bin/sleep") + "\n")
        script.chmod(script.stat().st_mode | stat.S_IXUSR)
        assert shutil.which(name) == str(script)
        return script

    make.log = log
    make.lines = lambda: log.read_text().splitlines() if log.exists() else []
    return make


@pytest.mark.parametrize("spoken,argv", [
    ("Wi-Fi", "gnome-control-center wifi"),
    ("open wifi settings", "gnome-control-center wifi"),
    ("display settings", "gnome-control-center display"),
    ("sound", "gnome-control-center sound"),
    ("Bluetooth settings", "gnome-control-center bluetooth"),
    ("power", "gnome-control-center power"),
    ("battery", "gnome-control-center power"),
    ("privacy", "gnome-control-center privacy"),
    ("keyboard", "gnome-control-center keyboard"),
    ("mouse/touchpad", "gnome-control-center mouse"),
    ("touchpad", "gnome-control-center mouse"),
    ("printers", "gnome-control-center printers"),
    ("users", "gnome-control-center system users"),
    ("about", "gnome-control-center system about"),
    ("date & time", "gnome-control-center system datetime"),
])
def test_gnome_pages(fake, monkeypatch, spoken, argv):
    monkeypatch.setenv("XDG_CURRENT_DESKTOP", "ubuntu:GNOME")
    fake("gnome-control-center")
    fake("systemsettings")
    out = st._run({"page": spoken})
    assert out.startswith("Asked the running Settings window"), out
    assert fake.lines() == [argv]


def test_every_gnome_mapping_is_a_real_gnome_50_panel():
    for page, (args, _kcm, _label) in st.PAGES.items():
        assert args[0] in GNOME_50_PANELS, page
        if len(args) > 1:
            assert args[0] == "system" and args[1] in GNOME_50_SYSTEM_SUBPAGES, page


@pytest.mark.parametrize("spoken,kcm", [
    ("wifi", "kcm_networkmanagement"), ("display", "kcm_kscreen"), ("sound", "kcm_pulseaudio"),
    ("bluetooth", "kcm_bluetooth"), ("power", "kcm_powerdevilprofilesconfig"), ("keyboard", "kcm_keyboard"),
    ("mouse", "kcm_mouse"), ("touchpad", "kcm_touchpad"), ("printers", "kcm_printer_manager"),
    ("users", "kcm_users"), ("about", "kcm_about-distro"),
])
def test_plasma_pages_use_systemsettings(fake, monkeypatch, spoken, kcm):
    monkeypatch.setenv("XDG_CURRENT_DESKTOP", "KDE")
    fake("gnome-control-center")   # installed too: the session decides, not the PATH
    fake("systemsettings", "stay")
    out = st._run({"page": spoken})
    assert out.startswith("Opened ") and kcm in out
    assert fake.lines() == [f"systemsettings {kcm}"]


def test_plasma_falls_back_to_kcmshell6(fake, monkeypatch):
    monkeypatch.setenv("XDG_CURRENT_DESKTOP", "KDE")
    fake("kcmshell6", "stay")
    assert st._run({"page": "display"}) == "Opened Display settings (kcmshell6 kcm_kscreen)."
    assert fake.lines() == ["kcmshell6 kcm_kscreen"]


def test_plasma_privacy_is_refused_by_name_not_opened_somewhere_else(fake, monkeypatch):
    monkeypatch.setenv("XDG_CURRENT_DESKTOP", "KDE")
    fake("systemsettings")
    assert "no single Privacy settings page" in st._run({"page": "privacy"})
    assert fake.lines() == []


def test_unknown_page_is_refused_with_the_list(fake, monkeypatch):
    monkeypatch.setenv("XDG_CURRENT_DESKTOP", "GNOME")
    fake("gnome-control-center")
    out = st._run({"page": "flux capacitor"})
    assert out.startswith("There is no settings page called 'flux capacitor'")
    for page in ("wifi", "display", "sound", "bluetooth", "about"):
        assert page in out
    assert fake.lines() == [], "an unknown page must not open Settings at its front page"


def test_a_failing_launcher_is_reported_not_called_opened(fake, monkeypatch):
    monkeypatch.setenv("XDG_CURRENT_DESKTOP", "GNOME")
    fake("gnome-control-center", "fail")
    out = st._run({"page": "wifi"})
    assert "could not open Wi-Fi settings (exit 1)" in out and "Unable to init server" in out
    assert "Opened" not in out and "Asked" not in out


def test_a_window_that_stays_up_is_opened(fake, monkeypatch):
    monkeypatch.setenv("XDG_CURRENT_DESKTOP", "GNOME")
    fake("gnome-control-center", "stay")
    assert st._run({"page": "sound"}) == "Opened Sound settings (gnome-control-center sound)."


def test_missing_settings_app(fake, monkeypatch):
    monkeypatch.setenv("XDG_CURRENT_DESKTOP", "GNOME")
    assert "gnome-control-center is not installed" in st._run({"page": "wifi"})
    monkeypatch.setenv("XDG_CURRENT_DESKTOP", "XFCE")
    assert "No supported Settings app" in st._run({"page": "wifi"})


def test_unknown_session_uses_what_is_installed(fake, monkeypatch):
    monkeypatch.setenv("XDG_CURRENT_DESKTOP", "")
    fake("systemsettings")
    st._run({"page": "bluetooth"})
    assert fake.lines() == ["systemsettings kcm_bluetooth"]


def test_malformed_arguments(fake, monkeypatch):
    monkeypatch.setenv("XDG_CURRENT_DESKTOP", "GNOME")
    fake("gnome-control-center")
    assert st._run({}).startswith("No settings page given. Known pages:")
    assert st._run({"page": 3}).startswith("No settings page given")
    assert st._run(None).startswith("open_settings needs a page")  # type: ignore[arg-type]
    assert fake.lines() == []
    assert st._post_condition({"page": "nonsense"}) is None


def test_registered_as_a_skill():
    assert [s.name for s in st.SKILLS] == ["open_settings"]
    assert st.SCHEMA["function"]["parameters"]["required"] == ["page"]
