"""`print_queue`'s device half: what this machine can print *to*.

`lpstat -a` lists the **configured** queues, so a printer the machine can
reach but nobody has added is invisible — and "my printer isn't there" is the
usual symptom of that, not of a broken printer. `lpinfo -v` asks CUPS what it
can *reach*.

**Measured here**, and the distinction it forces is real:

    network beh
    network lpd
    network ipp
    network https
    direct hp

**Those are transports, not printers.** `network ipp` says CUPS *can* speak
IPP, not that anything is waiting. So the answer calls them what they are, and
a genuinely discovered destination — a real `network ipp://host/queue` line —
is reported separately, because that one *is* a printer.
"""

from __future__ import annotations

import os
import pathlib
import sys

import pytest

_REPO = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_REPO / "usr" / "lib" / "shani-chronoa"))

from shani_chronoa.skills import print_queue as PQ  # noqa: E402

#: Real `lpinfo -v` output on a machine with no printers configured: five
#: transports, no destinations.
REAL_V = """network beh
network lpd
network ipp
network https
direct hp
network http
"""

#: The same, with a discovered network printer in it — the one case where a
#: `network` line *is* a device rather than a transport.
WITH_DISCOVERED = REAL_V + "network ipp://Office-Laser.local.:631/ipp/print\n"


def _fake_lpinfo(tmp_path, monkeypatch, stdout=REAL_V, code=0):
    bindir = tmp_path / "bin"
    bindir.mkdir(exist_ok=True)
    data = tmp_path / "v.txt"
    data.write_text(stdout)
    script = bindir / "lpinfo"
    script.write_text(
        "#!/usr/bin/env python3\n"
        f"print(open({str(data)!r}).read(), end='')\n"
        f"raise SystemExit({code})\n"
    )
    script.chmod(0o755)
    monkeypatch.setenv("PATH", f"{bindir}:{os.environ['PATH']}")
    return data


def test_transports_are_called_transports_not_printers(tmp_path, monkeypatch):
    """**The honest answer.** Calling `network ipp` a printer would be the
    confident wrong answer this module refuses everywhere else.
    """
    _fake_lpinfo(tmp_path, monkeypatch)
    out = "\n".join(PQ._available_devices())
    assert "backends CUPS has loaded (2)" in out      # direct, network
    assert "Those are transports, not printers" in out
    assert "not that anything is waiting" in out


def test_a_discovered_printer_is_named_as_one(tmp_path, monkeypatch):
    """A real `ipp://` line is a device, and it is the answer to "my printer
    isn't there" when it *is* there.
    """
    _fake_lpinfo(tmp_path, monkeypatch, stdout=WITH_DISCOVERED)
    out = "\n".join(PQ._available_devices())
    assert "Discovered on the network right now (1)" in out
    assert "Office-Laser.local.:631" in out


def test_nothing_discovered_is_stated_not_left_ambiguous(tmp_path, monkeypatch):
    _fake_lpinfo(tmp_path, monkeypatch)
    out = "\n".join(PQ._available_devices())
    assert "Nothing was discovered on the network" in out
    assert "added by address" in out


def test_a_missing_lpinfo_names_its_package(tmp_path, monkeypatch):
    empty = tmp_path / "no-lpinfo"
    empty.mkdir()
    monkeypatch.setenv("PATH", str(empty))
    out = PQ._available_devices()[0]
    assert "UNKNOWN" in out
    assert "lpinfo" in out and "cups" in out


def test_a_failing_lpinfo_reports_its_own_words(tmp_path, monkeypatch):
    _fake_lpinfo(tmp_path, monkeypatch, stdout="lpinfo: Bad destination\n", code=1)
    out = PQ._available_devices()[0]
    assert "UNKNOWN" in out
    assert "Bad destination" in out


def test_no_backends_at_all_is_unknown(tmp_path, monkeypatch):
    _fake_lpinfo(tmp_path, monkeypatch, stdout="\n", code=0)
    out = PQ._available_devices()[0]
    assert "UNKNOWN" in out
    assert "no printing backends" in out


def test_the_real_binary_is_used_when_present():
    """lpinfo ships with cups, which this box has, so the classification of a
    real backends list is verified rather than only the fixture's.
    """
    if not pathlib.Path("/usr/bin/lpinfo").exists():
        pytest.skip("lpinfo is not installed here")
    out = "\n".join(PQ._available_devices())
    assert "backends CUPS has loaded" in out
    assert "transports, not printers" in out


def test_the_list_answer_carries_the_destinations():
    """End to end: the configured queues and the reachable backends are one
    answer about printing, not two skills.
    """
    out = PQ._list()
    assert "backends CUPS has loaded" in out or "UNKNOWN" in out, out[:200]
