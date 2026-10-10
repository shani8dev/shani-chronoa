"""`boot_report`: what the firmware will actually boot, read apart from what is running.

On a blue-green machine "what will my machine boot into if I do nothing?" has
two answers that can disagree - what `shani-deploy` intends and what the
firmware is pointed at. `snapshot_status` reports the first; nothing read the
second. `bootctl list` is the only place it exists.

**Measured on a real slot** (`shani-testbed` `chronoa-cli-formats.sh`, `@blue`,
2026-10-10), and the capture is worth reading twice, because it *corrected the
first implementation*:

     title: shanios-green (Candidate) (not reported/new)
     title: shanios-blue (Active) (default) (not reported/new)
      tries: 3 left; 0 done

Three different markers, and they are not synonyms. `(Active)` is what is
running now, `(default)` is what the firmware boots if nothing intervenes, and
`(Candidate)` is the *other* side of an auto-tries pair — which here is green
while the default is blue. The first version of the parser keyed on
`(Candidate)` and would have named the wrong slot **on the very machine the
format was measured on**. That is the failure this file holds shut.

The assertion is on the *distinction*, not on a label string: with the running
entry also being the default, the answer must say both; with them apart, it
must name each.
"""

from __future__ import annotations

import os
import pathlib
import sys

_REPO = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_REPO / "usr" / "lib" / "shani-chronoa"))

from shani_chronoa.skills import boot_report as B  # noqa: E402

#: The captured list, verbatim from the slot run. Both entries, the blank line
#: between them, and the `tries` line that says auto-tries is armed.
CAPTURED = """
         type: Boot Loader Specification Type #1 (.conf)
        title: shanios-green (Candidate) (not reported/new)
           id: shanios-green.conf
        source: /boot/efi//loader/entries/shanios-green.conf (on the EFI System Partition)
           efi: /boot/efi//EFI/shanios/shanios-green.efi

         type: Boot Loader Specification Type #1 (.conf)
        title: shanios-blue (Active) (default) (not reported/new)
           id: shanios-blue.conf
        source: /boot/efi//loader/entries/shanios-blue+3-0.conf (on the EFI System Partition)
         tries: 3 left; 0 done
           efi: /boot/efi//EFI/shanios/shanios-blue.efi
"""


def _fake_bootctl(tmp_path, monkeypatch, stdout=CAPTURED, stderr="", code=0,
                  missing=False):
    bindir = tmp_path / "bin"
    bindir.mkdir(exist_ok=True)
    if not missing:
        data = tmp_path / "list.txt"
        data.write_text(stdout)
        script = bindir / "bootctl"
        script.write_text(
            "#!/usr/bin/env python3\n"
            f"import sys; print(open({str(data)!r}).read(), end='')\n"
            f"print({stderr!r}, file=sys.stderr) if {stderr!r} else None\n"
            f"raise SystemExit({code})\n"
        )
        script.chmod(0o755)
    else:
        (bindir / "python3").symlink_to(sys.executable)
        monkeypatch.setenv("PATH", str(bindir))
        return
    monkeypatch.setenv("PATH", f"{bindir}:{os.environ['PATH']}")


def test_the_default_entry_is_the_one_marked_default(tmp_path, monkeypatch):
    """**The correction the measurement forced.** `(Candidate)` is on green;
    `(default)` is on blue; the firmware boots the one marked `(default)`."""
    _fake_bootctl(tmp_path, monkeypatch)
    out = "\n".join(B._boot_entries())
    assert "shanios-blue" in out and "(default)" in out
    assert "will boot 'shanios-blue (Active) (default) (not reported/new)'" in out
    # The candidate is *not* the answer, and is not claimed to be.
    assert "will boot 'shanios-green" not in out


def test_the_active_entry_is_named_beside_the_default(tmp_path, monkeypatch):
    """Both markers are facts and both are read. When they coincide - the
    machine booted the entry it will boot again - the title already says
    `(Active)`, so nothing is repeated; when they differ, each is named."""
    _fake_bootctl(tmp_path, monkeypatch)
    out = "\n".join(B._boot_entries())
    assert "shanios-blue (Active) (default)" in out
    assert "will boot 'shanios-blue" in out


def test_the_candidate_and_the_default_are_reported_apart(tmp_path, monkeypatch):
    """When they differ, each is named for what it is - the `(Candidate)` is the
    auto-tries other side, not the next boot."""
    _fake_bootctl(tmp_path, monkeypatch,
                 stdout=CAPTURED.replace("shanios-blue (Active) (default)",
                                        "shanios-blue (Active)")
                              .replace("shanios-green (Candidate)",
                                       "shanios-green (default)"))
    out = "\n".join(B._boot_entries())
    assert "will boot 'shanios-green (default) (not reported/new)'" in out
    assert "running now is 'shanios-blue (Active)" in out


def test_auto_tries_is_reported_with_its_tries_left(tmp_path, monkeypatch):
    """`tries: 3 left; 0 done` is shani-deploy's boot-counting safety net, and
    how many tries remain is the question someone asks after a boot failed."""
    _fake_bootctl(tmp_path, monkeypatch)
    out = "\n".join(B._boot_entries())
    assert "auto-tries, 3 left; 0 done" in out


def test_no_marked_default_is_unknown_not_the_first_row(tmp_path, monkeypatch):
    """**The list is ordered by sort-key, not by preference.** With nothing
    marked, reading the first row as "what boots next" names whatever sorts
    first - so the answer says UNKNOWN and says why."""
    _fake_bootctl(tmp_path, monkeypatch,
                 stdout=CAPTURED.replace(" (default)", ""))
    out = "\n".join(B._boot_entries())
    assert "No entry is marked (default)" in out
    assert "UNKNOWN" in out
    # No entry is *named* as what boots next - asserted on the quoting that
    # introduces a title, because the UNKNOWN sentence itself contains "will
    # boot" and a bare substring check would pass either way.
    assert "will boot '" not in out


def test_a_tool_that_is_not_installed_says_so_and_names_the_package(tmp_path, monkeypatch):
    _fake_bootctl(tmp_path, monkeypatch, missing=True)
    out = B._boot_entries()[0]
    assert "UNKNOWN" in out
    assert "bootctl" in out and "systemd" in out


def test_a_bootctl_that_refuses_is_its_own_words(tmp_path, monkeypatch):
    """An nspawn slot and a BIOS machine both land here, and "no entries" would
    be a confident wrong answer about a machine with an operating system."""
    _fake_bootctl(tmp_path, monkeypatch,
                 stderr="Couldn't find EFI system partition.", code=1)
    out = B._boot_entries()[0]
    assert "UNKNOWN" in out
    assert "Couldn't find EFI system partition" in out


def test_an_empty_list_is_unknown_not_a_clean_answer(tmp_path, monkeypatch):
    _fake_bootctl(tmp_path, monkeypatch, stdout="")
    out = B._boot_entries()[0]
    assert "UNKNOWN" in out
    assert "listed none" in out


def test_the_composed_answer_carries_the_entries(tmp_path, monkeypatch):
    """End to end through `_run`, beside the timing half that already exists.

    The timing half is asserted loosely on purpose: on a machine with
    systemd-analyze present it is a real `Startup finished in ...` line, and on
    one without it is the UNKNOWN branch. Either way it must be there, because
    this is one answer about booting rather than a second skill.
    """
    _fake_bootctl(tmp_path, monkeypatch)
    out = B._run({})
    assert "EFI boot entries (2):" in out
    assert ("Startup finished" in out or "Boot timing is UNKNOWN" in out), out[:200]
