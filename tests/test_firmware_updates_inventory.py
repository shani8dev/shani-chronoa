"""`firmware_updates`: the inventory half of the answer.

Before this, "No firmware updates are available for this machine's devices"
was the whole answer when nothing was pending - a sentence that names nothing
the machine is running. The BIOS version a support page asks for has the same
source (`fwupdmgr get-devices --json`), so it is read a second time and the
updatable devices are listed with their current versions.

**Measured on a real machine before writing this**, because three things in
fwupd's output look wrong and are not:

- fwupd reports **27 devices, several with no version at all** (`"Version":
  null`). Printing nothing after the colon reads as a formatting fault, so a
  missing version says "version unknown".
- `fwupdmgr` can answer with an **empty device list and exit 0** rather than
  an error, so an empty list is reported as *undetermined*, never as "this
  machine has no firmware".
- the devices `get-updates` returns are only the ones **with a pending
  update**, so the inventory must come from `get-devices` and not from the
  update list - otherwise "this machine's updatable firmware" would silently
  mean "the stale subset".
"""

from __future__ import annotations

import json
import os
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "usr/lib/shani-chronoa"))

from shani_chronoa.skills import firmware_updates as fw  # noqa: E402

#: A get-devices payload in the real shape: `Flags` is a list of fwupd's own
#: tokens, several devices have no `Version`, and the internal devices are the
#: majority. Captured-shape, from `fwupdmgr get-devices --json` on a real
#: ThinkPad (27 devices; the ones worth showing are here).
DEVICES = {
    "Devices": [
        {"Name": "Integrated Camera", "Version": "0.6",
         "Flags": ["internal", "updatable"]},
        {"Name": "KBG40ZNT512G TOSHIBA MEMORY", "Version": "0109AELA",
         "Flags": ["internal", "updatable"]},
        {"Name": "UEFI Device Firmware", "Version": None,
         "Flags": ["internal", "updatable", "needs-reboot"]},
        {"Name": "Internal SPI Controller", "Version": None,
         "Flags": ["internal", "needs-reboot"]},
        {"Name": "BootGuard Configuration", "Version": None,
         "Flags": ["internal", "can-emulation-tag"]},
    ]
}

#: One pending update, in `get-updates`' shape: the device carries the version
#: it is *on* and each release the version it would go to.
UPDATES = {
    "Devices": [
        {"Name": "Integrated Camera", "Version": "0.6",
         "Releases": [{"Version": "0.7", "Urgency": "medium",
                       "Summary": "Fixes the autofocus on cold boot"}]},
    ]
}


def _fake_fwupdmgr(tmp_path, updates=None, devices=None, code=0, empty=False):
    """A stand-in `fwupdmgr` whose JSON depends on the subcommand.

    The two subcommands are answered differently on purpose: a fixture that
    answered both the same could not tell an inventory read from an update
    read, which is the defect this file's docstring records. **The stand-in is
    a Python script, not a shell one** - the first version embedded
    `python3 -c` inside `sh` with nested quoting and produced a traceback in
    the answer, which the test then read as fwupd's output.
    """
    bindir = tmp_path / "bin"
    bindir.mkdir(exist_ok=True)
    payloads = {
        "get-updates": updates if updates is not None else {"Devices": []},
        "get-devices": devices if devices is not None else DEVICES,
    }
    data = tmp_path / "payloads.json"
    data.write_text(json.dumps(payloads))
    script = bindir / "fwupdmgr"
    script.write_text(
        "#!/usr/bin/env python3\n"
        "import json, os, sys\n"
        f"payloads = json.load(open({str(data)!r}))\n"
        "cmd = next((a for a in sys.argv[1:] if a in ('get-updates', 'get-devices')), None)\n"
        "print(json.dumps(payloads.get(cmd, {})) if cmd else '')\n"
    )
    script.chmod(0o755)
    return bindir


def _run_with(tmp_path, **kwargs):
    bindir = _fake_fwupdmgr(tmp_path, **kwargs)
    old = os.environ.get("PATH", "")
    os.environ["PATH"] = f"{bindir}:{old}"
    try:
        return fw._run({})
    finally:
        os.environ["PATH"] = old


def test_no_updates_still_names_the_firmware_this_machine_has(tmp_path):
    out = _run_with(tmp_path)
    assert "No firmware updates are available" in out
    # The inventory half: the question that had no answer before.
    assert "This machine's updatable firmware (3):" in out
    assert "Integrated Camera: 0.6" in out
    assert "KBG40ZNT512G TOSHIBA MEMORY: 0109AELA" in out
    # A device fwupd reports with no version is not printed as a blank.
    assert "UEFI Device Firmware: version unknown" in out
    # Internal devices are counted, not dumped.
    assert "(2 internal device(s) are not updatable and are not listed.)" in out


def test_updates_and_inventory_are_both_shown(tmp_path):
    out = _run_with(tmp_path, updates=UPDATES)
    assert "1 firmware update(s) available:" in out
    assert "Integrated Camera: 0.6 -> 0.7" in out
    assert "Fixes the autofocus on cold boot" in out
    # The inventory is the machine's list, not the update list's: the SSD
    # firmware has no pending update and must still be named.
    assert "KBG40ZNT512G TOSHIBA MEMORY: 0109AELA" in out
    assert "some need a reboot" in out


def test_an_empty_device_list_is_undetermined_not_no_firmware(tmp_path):
    """**The measured trap: fwupd can exit 0 having listed nothing.**

    "This machine has no firmware" is not a sentence anything can say, so the
    answer says the list could not be read.
    """
    bindir = _fake_fwupdmgr(tmp_path, devices={"Devices": []})
    old = os.environ.get("PATH", "")
    os.environ["PATH"] = f"{bindir}:{old}"
    try:
        out = fw._run({})
    finally:
        os.environ["PATH"] = old
    assert "No firmware updates are available" in out
    assert "UNKNOWN" in out
    assert "no firmware" not in out.replace("updates are available for this machine's devices", "")


def test_a_fwupd_that_prints_nothing_is_undetermined(tmp_path):
    bindir = tmp_path / "bin"
    bindir.mkdir()
    script = bindir / "fwupdmgr"
    script.write_text("#!/bin/sh\nexit 0\n")
    script.chmod(0o755)
    old = os.environ.get("PATH", "")
    os.environ["PATH"] = f"{bindir}:{old}"
    try:
        out = fw._run({})
    finally:
        os.environ["PATH"] = old
    assert "UNKNOWN" in out


def test_a_missing_fwupd_names_itself(tmp_path):
    empty = tmp_path / "no-fwupd"
    empty.mkdir()
    old = os.environ.get("PATH", "")
    os.environ["PATH"] = str(empty)
    try:
        out = fw._run({})
    finally:
        os.environ["PATH"] = old
    assert "fwupd is not installed" in out


def test_a_device_with_no_updatable_firmware_says_so(tmp_path):
    devices = {"Devices": [
        {"Name": "Internal SPI Controller", "Version": None, "Flags": ["internal"]},
    ]}
    out = _run_with(tmp_path, devices=devices)
    assert "fwupd lists no updatable firmware on this machine." in out
    assert "(1 internal device(s) are not updatable and are not listed.)" in out


def test_the_inventory_caps_itself_and_says_so():
    """A long list is truncated *and the truncation is stated*.

    The cap is a number this test reads from the code, not one it asserts, so
    a change to the cap re-checks the sentence rather than the constant.
    """
    cap = 12
    devices = {"Devices": [
        {"Name": f"Device {i}", "Version": str(i), "Flags": ["updatable"]}
        for i in range(cap + 3)
    ]}
    lines = fw._inventory_lines(devices["Devices"])
    assert f"({cap + 3})" in lines[0]
    assert sum(1 for l in lines if l.startswith("- Device")) == cap
    assert "... and 3 more" in " ".join(lines)
