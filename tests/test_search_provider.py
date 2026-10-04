"""The desktop search provider: GNOME Shell's overview and Plasma's KRunner.

The overview sends every keystroke to every enabled provider, so the tests hold
the provider to doing nothing a keystroke should not cause - no network, no
model, nothing for a two-letter query - while still answering arithmetic and
unit conversions locally, and opening Chronoa only when a result is activated.
"""

import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

import pytest

from shani_chronoa import search_provider as sp

REPO = Path(__file__).resolve().parent.parent


def test_short_queries_produce_nothing():
    assert sp.results_for("hi") == [] and sp.results_for("   ") == []


@pytest.mark.parametrize("query, answer", [("2+2*3", "8"), ("12*12", "144"), ("5 km to miles", "3.10686")])
def test_arithmetic_and_units_are_answered_locally(query, answer):
    results = sp.results_for(query)
    assert results[0]["id"].startswith("answer:") and answer in results[0]["name"]
    assert results[-1]["id"] == f"ask:{query}"


def test_a_question_only_offers_to_ask():
    assert [r["id"] for r in sp.results_for("what is the weather in pune")] == ["ask:what is the weather in pune"]


def test_no_network_or_model_is_touched(monkeypatch):
    import socket
    def refuse(*a, **k):
        raise AssertionError("the search provider opened a network connection")
    monkeypatch.setattr(socket, "create_connection", refuse)
    monkeypatch.setattr(socket.socket, "connect", refuse)
    for q in ("5 km to miles", "2^10", "tell me a joke"):
        sp.results_for(q)


def test_activation_opens_chronoa_with_the_question(tmp_path, monkeypatch):
    stub = tmp_path / "shani-chronoa"
    out = tmp_path / "argv"
    stub.write_text(f"#!/bin/sh\nprintf '%s\\n' \"$@\" > {out}\n")
    stub.chmod(0o755)
    monkeypatch.setenv("PATH", f"{tmp_path}:{os.environ['PATH']}")
    assert sp.activate("ask:remind me about lunch")
    for _ in range(50):
        if out.exists():
            break
        time.sleep(0.05)
    assert out.read_text().strip() == "--ask=remind me about lunch"
    assert not sp.activate("ask:")


def test_both_interfaces_through_the_handler():
    p = sp.Provider()
    sig, (ids,) = p.handle("org.gnome.Shell.SearchProvider2", "GetInitialResultSet", (["2+2"],))
    assert sig == "(as)" and ids[0] == "answer:2+2"
    sig, (metas,) = p.handle("org.gnome.Shell.SearchProvider2", "GetResultMetas", (ids,))
    assert metas[0]["name"].unpack() == "4"
    sig, (matches,) = p.handle("org.kde.krunner1", "Match", ("2+2",))
    assert sig == "(a(sssida{sv}))" and matches[0][1] == "4" and matches[0][4] > matches[1][4]
    with pytest.raises(ValueError):
        p.handle("org.kde.krunner1", "Nope", ())


def test_the_desktop_files_point_at_the_service():
    ini = (REPO / "usr/share/gnome-shell/search-providers/shani-chronoa.ini").read_text()
    runner = (REPO / "usr/share/krunner/dbusplugins/shani-chronoa.desktop").read_text()
    svc = (REPO / "usr/share/dbus-1/services/dev.shani.chronoa.SearchProvider.service").read_text()
    assert f"BusName={sp.BUS_NAME}" in ini and f"ObjectPath={sp.GNOME_PATH}" in ini
    assert "DesktopId=shani-chronoa.desktop" in ini and (REPO / "usr/share/applications/shani-chronoa.desktop").exists()
    assert f"X-Plasma-DBusRunner-Service={sp.BUS_NAME}" in runner and f"X-Plasma-DBusRunner-Path={sp.KRUNNER_PATH}" in runner
    assert f"Name={sp.BUS_NAME}" in svc and "Exec=/usr/bin/shani-chronoa-search" in svc


def test_the_pkgbuild_installs_every_file():
    pkgbuild = REPO.parent / "shani-pkgbuilds/shani-chronoa/PKGBUILD"
    if not pkgbuild.exists():
        pytest.skip("shani-pkgbuilds is not checked out beside this repo")
    text = pkgbuild.read_text()
    for f in ("usr/bin/shani-chronoa-search", "usr/share/dbus-1/services/dev.shani.chronoa.SearchProvider.service",
              "usr/share/gnome-shell/search-providers/shani-chronoa.ini",
              "usr/share/krunner/dbusplugins/shani-chronoa.desktop"):
        assert f in text, f"the PKGBUILD does not install {f}"


@pytest.mark.skipif(shutil.which("dbus-daemon") is None or shutil.which("gdbus") is None,
                    reason="needs dbus-daemon and gdbus")
def test_a_real_round_trip_on_a_private_bus(tmp_path):
    """The launcher, a private session bus (never the user's), and gdbus as the shell would call it."""
    daemon = subprocess.Popen(["dbus-daemon", "--session", "--print-address", "--nofork"],
                              stdout=subprocess.PIPE, text=True)
    try:
        address = daemon.stdout.readline().strip()
        env = {**os.environ, "DBUS_SESSION_BUS_ADDRESS": address, "PYTHONDONTWRITEBYTECODE": "1"}
        svc = subprocess.Popen([sys.executable, str(REPO / "usr/bin/shani-chronoa-search")], env=env)
        try:
            out = ""
            for _ in range(40):
                proc = subprocess.run(["gdbus", "call", "--session", "--dest", sp.BUS_NAME, "--object-path",
                                       sp.KRUNNER_PATH, "--method", "org.kde.krunner1.Match", "6*7"],
                                      capture_output=True, text=True, env=env)
                out = proc.stdout
                if proc.returncode == 0:
                    break
                time.sleep(0.1)
            assert "'42'" in out, out
        finally:
            svc.terminate()
            svc.wait(timeout=5)
    finally:
        daemon.terminate()
        daemon.wait(timeout=5)
