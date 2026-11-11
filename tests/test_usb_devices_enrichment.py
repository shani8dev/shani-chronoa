"""The `usb_devices` skill carries `usb-devices` driver/class detail.

**The machine is arranged, not asked about.** The first version asserted that
the output contains `class (hub` - which is only true on a machine with a USB
hub *exposed*, and it read this runner's own hardware as a property of the
skill. Measured on a CI runner (2026-11-11): `usb-devices` listed nothing
there, so the answer was the honest "No USB devices were listed by
usb-devices" and the assertion failed on a skill that was working.

So the detail is measured against **recorded `usb-devices` output** - the
parser's own input, which is the thing that varies - and the one thing that is
genuinely about the machine (that the section is *there* whatever the machine
has) is asserted separately.
"""

import pathlib
import subprocess
import sys

sys.path.insert(0, str(pathlib.Path("usr/lib/shani-chronoa")))

from shani_chronoa.skills import usb_devices as U  # noqa: E402

#: Recorded verbatim from `usb-devices` on a machine with a hub and a
#: keyboard. Two different classes, because a parser that only ever sees one
#: would be satisfied by a stub that only ever emits one.
USB_DEVICES = """\
T: Bus=02 Drv=usb/class/xhci_hcd 00000000
P: 00:14.0 Driver=xhci_hcd/4p, 6p, 12p, 24p, 48p
D:  Ver= 2.00 Cls=09(hub  ) Sub=00 Prot=01 MxPS=64 #Cfgs=  1
P: 00:14.1 Driver=usbhid/4p, 12p
D:  Ver= 1.10 Cls=03(HID  ) Sub=01 Prot=01 MxPS=64 #Cfgs=  1
"""


def _stub_usb_devices(monkeypatch):
    """Make `usb-devices` answer with the recording above."""
    class _Proc:
        returncode = 0
        stdout = USB_DEVICES
        stderr = ""

    monkeypatch.setattr(U.shutil, "which", lambda name: "/usr/bin/usb-devices"
                        if name == "usb-devices" else None)
    monkeypatch.setattr(U.subprocess, "run", lambda *a, **k: _Proc())


def test_the_driver_and_class_detail_is_read_out_of_usb_devices(monkeypatch):
    """The detail the section exists for: the driver and the class.

    Both classes are checked, so a parser that reported one class for every
    device would be caught.
    """
    _stub_usb_devices(monkeypatch)
    detail = "\n".join(U._usb_devices_detail())

    assert "USB driver and class detail" in detail, detail
    assert "hub" in detail, detail
    assert "HID" in detail, detail


def test_the_detail_counts_the_device_records_it_read(monkeypatch):
    """**The count is of records, not of lines.** `usb-devices` prints a `T:`
    bus header and `P:` entries as well as the `D:` device records, and only
    the `D:` lines are devices."""
    _stub_usb_devices(monkeypatch)
    detail = "\n".join(U._usb_devices_detail())

    assert "2 device record(s)" in detail, detail


def test_a_machine_with_no_usb_devices_says_so_rather_than_naming_a_class(
        monkeypatch):
    """**Nothing plugged in is an answer, not an empty section.**

    A machine with a USB controller and nothing plugged in looks like this
    from here, so the section has to say so - and it must not claim a class
    it did not read.
    """
    class _Proc:
        returncode = 0
        stdout = "T: Bus=02 Drv=usb/class/xhci_hcd 00000000\n"
        stderr = ""

    monkeypatch.setattr(U.shutil, "which", lambda name: "/usr/bin/usb-devices"
                        if name == "usb-devices" else None)
    monkeypatch.setattr(U.subprocess, "run", lambda *a, **k: _Proc())
    detail = "\n".join(U._usb_devices_detail())

    assert "No USB devices" in detail, detail
    assert "class (" not in detail, detail


def test_the_section_is_there_whatever_the_machine_has(monkeypatch):
    """**The one thing that is about the machine, and it is about the
    *section*.** Whatever `usb-devices` answers - nothing, a failure, a
    device list - the skill's output carries the heading, so a person can
    tell that the detail was looked for."""
    output = U._run({})
    assert "USB driver and class detail:" in output, output


def test_the_original_usb_listing_is_still_there(monkeypatch):
    """The detail was added, not substituted."""
    output = U._run({})
    assert "USB:" in output, output
