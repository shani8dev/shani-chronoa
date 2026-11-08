"""ngrep as a capture engine, arpwatch for network changes, gobuster for
content discovery.

Three tools that were on the image but invoked by nothing:

- `capture_packets` gains `engine: "ngrep"` - grep live traffic for a
  pattern. Same consent gate as tcpdump (it reads packet contents too);
  the pattern is validated (at least two characters) because a pattern
  that matches everything is a payload dump.
- `network_watch` runs `arpwatch` for a few seconds and reports new
  IP/MAC pairs - who joined or changed on this network. Unprivileged
  attempt, honest failure: arpwatch needs to open the interface.
- `content_discovery` runs `gobuster dir` against a URL the person names,
  for testing their own server. Gated exactly like `scan_network` (the
  `network` sense) and restricted to private/loopback targets, so it
  cannot be talked into fuzzing the internet.
"""

import os
import pathlib
import sys

import pytest

_REPO = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_REPO / "usr" / "lib" / "shani-chronoa"))

from shani_chronoa.skills import capture_packets as CP  # noqa: E402
from shani_chronoa.skills import content_discovery as CD  # noqa: E402
from shani_chronoa.skills import network_watch as NW  # noqa: E402


def _fake(tmp_path, monkeypatch, name, code=0, out="", err=""):
    bindir = tmp_path / "bin"
    bindir.mkdir(exist_ok=True)
    script = bindir / name
    script.write_text(
        "#!/usr/bin/env python3\n"
        "import sys\n"
        f"sys.stdout.write({out!r})\n"
        f"sys.stderr.write({err!r})\n"
        f"sys.exit({code})\n")
    script.chmod(0o755)
    monkeypatch.setenv("PATH", f"{bindir}:{os.environ['PATH']}")


# ------------------------------------------------------------------ ngrep

def test_ngrep_engine_reports_matches(tmp_path, monkeypatch):
    _fake(tmp_path, monkeypatch, "ngrep", out="10.0.0.1 > 10.0.0.2: SYN\n")
    monkeypatch.setattr(CP, "_consent", lambda config: (True, ""))
    said = CP._run({"engine": "ngrep", "pattern": "SYN", "seconds": 2, "interface": "eth0"})
    assert "1 match" in said and "SYN" in said


def test_ngrep_needs_a_real_pattern(tmp_path, monkeypatch):
    _fake(tmp_path, monkeypatch, "ngrep")
    monkeypatch.setattr(CP, "_consent", lambda config: (True, ""))
    said = CP._run({"engine": "ngrep", "pattern": "x", "seconds": 2, "interface": "eth0"})
    assert "at least two" in said


def test_ngrep_no_matches_says_so(tmp_path, monkeypatch):
    _fake(tmp_path, monkeypatch, "ngrep", out="")
    monkeypatch.setattr(CP, "_consent", lambda config: (True, ""))
    said = CP._run({"engine": "ngrep", "pattern": "XYZZY", "seconds": 2, "interface": "eth0"})
    assert "Nothing matched" in said


def test_unknown_engine_is_refused(tmp_path, monkeypatch):
    monkeypatch.setattr(CP, "_consent", lambda config: (True, ""))
    said = CP._run({"engine": "wireshark", "seconds": 2, "interface": "eth0"})
    assert "tcpdump or ngrep" in said


# ------------------------------------------------------------------ arpwatch

def test_network_watch_reports_new_devices(tmp_path, monkeypatch):
    out = ("Hostname IP address mac address Ethernet IP address [time] "
           "Changed\neth0 10.0.0.5 00:11:22:33:44:55 10.0.0.1 [never] new\n")
    _fake(tmp_path, monkeypatch, "arpwatch", out=out)
    said = NW._run({"interface": "eth0", "seconds": 2})
    assert "10.0.0.5" in said and "00:11:22:33:44:55" in said


def test_network_watch_quiet_network_says_so(tmp_path, monkeypatch):
    _fake(tmp_path, monkeypatch, "arpwatch", out="")
    said = NW._run({"interface": "eth0", "seconds": 2})
    assert "no new" in said.lower()


def test_network_watch_missing_tool_names_it(tmp_path, monkeypatch):
    monkeypatch.setenv("PATH", str(tmp_path))
    said = NW._run({"interface": "eth0"})
    assert "arpwatch" in said


# ------------------------------------------------------------------ gobuster

def test_content_discovery_finds_paths(tmp_path, monkeypatch):
    out = "/admin (Status: 200)\n/login (Status: 302)\n"
    _fake(tmp_path, monkeypatch, "gobuster", out=out)
    monkeypatch.setattr(CD, "_network_allowed", lambda: (True, ""))
    wordlist = tmp_path / "common.txt"
    wordlist.write_text("admin\nlogin\n")
    said = CD._run({"url": "http://192.168.1.10", "wordlist": str(wordlist)})
    assert "/admin" in said and "200" in said


def test_content_discovery_refuses_the_internet(tmp_path, monkeypatch):
    monkeypatch.setattr(CD, "_network_allowed", lambda: (True, ""))
    said = CD._run({"url": "http://example.com"})
    assert "private" in said.lower()


def test_content_discovery_needs_the_network_switch(tmp_path, monkeypatch):
    monkeypatch.setattr(CD, "_network_allowed", lambda: (False, "off"))
    said = CD._run({"url": "http://127.0.0.1"})
    assert "Refusing" in said
