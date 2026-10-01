"""Skills built on what every Shanios image already ships: words, man-db,
sane+tesseract, iputils, vnstat, GNOME's a11y settings, plocate, logind/ddcutil.

The tools are substituted with their real output shapes here; each was also
run against the real package in an Arch container (spelling, man pages,
SANE's test scanner, the index, dconf-backed settings) and logind's
SetBrightness on real hardware.
"""

import os
import subprocess
from pathlib import Path

import pytest

from shani_chronoa.skills import accessibility as a11y
from shani_chronoa.skills import brightness
from shani_chronoa.skills import data_usage as du
from shani_chronoa.skills import explain_command as ec
from shani_chronoa.skills import find_files as ff
from shani_chronoa.skills import network_check as nc
from shani_chronoa.skills import spell


def _cp(out="", rc=0, err=""):
    return subprocess.CompletedProcess([], rc, out, err)


@pytest.fixture
def words(tmp_path, monkeypatch):
    f = tmp_path / "words"
    f.write_text("\n".join(["the", "tech", "ten", "and", "Aden", "receive", "relieve", "separate",
                            "friend", "because", "necessary", "an"]) + "\n")
    monkeypatch.setattr(spell, "WORDS", str(f))
    spell._words.cache_clear()
    spell._common.clear()
    yield
    spell._words.cache_clear()


@pytest.mark.parametrize("typo, want", [("teh", "the"), ("recieve", "receive"), ("adn", "and"),
                                        ("seperate", "separate"), ("freind", "friend")])
def test_the_first_suggestion_is_the_word_meant(words, typo, want):
    assert spell.suggest(typo)[0] == want


def test_a_correct_word_is_spelled_out(words):
    assert spell._run({"word": "necessary"}).endswith("N - E - C - E - S - S - A - R - Y.")


def test_a_command_name_is_checked_before_man_runs(monkeypatch):
    called = []
    monkeypatch.setattr(ec, "_out", lambda *a, **k: called.append(a) or "")
    assert "not a command name" in ec._run({"command": "tar; rm -rf ~"}) and not called


def test_internet_stops_at_the_first_failing_step(monkeypatch):
    monkeypatch.setattr(nc, "_gateway", lambda: (None, None))
    assert nc._run({}).startswith("No network connection")


def test_privacy_mode_keeps_the_check_on_this_network(monkeypatch):
    monkeypatch.setattr(nc, "_gateway", lambda: ("192.168.1.1", "wlan0"))
    monkeypatch.setattr(nc, "_ping", lambda h: 2.0)
    monkeypatch.setattr("shani_chronoa.egress.privacy_mode_enabled", lambda: True)
    out = nc._run({})
    assert "router answers" in out and "did not test anything beyond" in out


def test_data_usage_reads_vnstat_json(monkeypatch):
    data = '{"interfaces":[{"name":"wlan0","traffic":{"day":[{"rx":1500000000,"tx":200000000}],' \
           '"month":[{"rx":42000000000,"tx":3100000000}]}}]}'
    monkeypatch.setattr(du.subprocess, "run", lambda *a, **k: _cp(data))
    assert du._run({}) == ("wlan0: this month 42.0 GB down and 3.1 GB up; today 1.5 GB down and 200.0 MB up.")


def test_accessibility_sets_gnome_keys(monkeypatch):
    calls = []
    monkeypatch.setattr(a11y.shutil, "which", lambda b: "/usr/bin/" + b)
    monkeypatch.setattr(a11y, "_gsettings", lambda *a: calls.append(a) or _cp())
    assert a11y._run({"feature": "screen_reader", "enabled": True}) == "Screen reader is now on."
    assert calls[-1] == ("set", "org.gnome.desktop.a11y.applications", "screen-reader-enabled", "true")


def test_brightness_falls_back_to_logind_when_sysfs_is_root_only(monkeypatch):
    def denied(self, _):
        raise PermissionError
    monkeypatch.setattr(Path, "write_text", denied)
    seen = []
    monkeypatch.setattr(brightness.subprocess, "run", lambda argv, **k: seen.append(argv) or _cp())
    assert brightness._set("intel_backlight", 9514) == ""
    assert seen[0][-3:] == ["backlight", "intel_backlight", "9514"] and "SetBrightness" in seen[0]


def test_find_files_uses_the_index_within_scope(monkeypatch, tmp_path):
    inside = tmp_path / "work" / "report-2026.pdf"
    inside.parent.mkdir()
    inside.write_text("")
    hidden = tmp_path / ".cache" / "report-old.pdf"
    hidden.parent.mkdir()
    hidden.write_text("")
    listing = f"{inside}\n{hidden}\n/etc/report-x.pdf\n{tmp_path}/gone/report-stale.pdf\n"
    monkeypatch.setattr(ff.shutil, "which", lambda b: "/usr/bin/plocate")
    monkeypatch.setattr(ff, "_PLOCATE_DB", tmp_path / "work" / "report-2026.pdf")   # any existing file
    monkeypatch.setattr(ff.subprocess, "run", lambda *a, **k: _cp(listing))
    assert ff._from_index(tmp_path, "report*.pdf", False, 10) == [inside]
