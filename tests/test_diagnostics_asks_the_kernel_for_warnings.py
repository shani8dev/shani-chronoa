"""The kernel was asked about errors only, and "no errors" read as "all quiet".

`kernel_log.read_ring()` existed and had **no caller in the tree**. Its docstring
is one line: *"Public entry, so a caller can ask for a lower level than `err`."*

The sense does not use it. Line 174 of that module hardcodes

    lines = _read_dmesg("err") if route == "dmesg" else _read_kmsg(3)

so the level parameter nothing ever passed is the whole point of the function. The
sense's wording when there are no errors is *"The kernel ring buffer holds nothing
at error level or worse"* - which a person reads as **my machine is quiet**. Most
of what somebody opens Diagnostics to see logs at **warning**: USB resets, GPU
faults, throttling, deprecation notices.

## `None` is not `[]`, and that is the whole test

`read_ring` returns `None` when the ring could not be read and `[]` when it was
read and was empty. The module that owns it insists on the difference in its own
docstring - *"Reads UNKNOWN on a machine where the ring cannot be read, never
zero"* - and a truthiness check would throw it away, which is how a permission
problem gets reported as a clean machine.

So an unreadable ring is `STATUS_UNKNOWN`, and **only a ring read to be empty**
goes on to ask about warnings. Asking first would make a quiet kernel look busy
with warnings every time somebody looked.

## The levels actually asked are asserted

Not just the words. A probe that always answered `working` with a reassuring
sentence would pass a status-only test, and the level parameter would still never
be used.
"""

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "usr/lib/shani-chronoa"))

from shani_chronoa.gui.surfaces import diagnostics as D  # noqa: E402
from shani_chronoa.senses import kernel_log  # noqa: E402


def _ring(monkeypatch, *answers):
    """Answer `read_ring` from a fixed list, recording the levels asked for."""
    asked = []
    remaining = list(answers)

    def read_ring(level=3):
        asked.append(level)
        return remaining.pop(0) if remaining else None

    monkeypatch.setattr(kernel_log, "read_ring", read_ring)
    return asked


def test_unreadable_is_not_a_quiet_machine(monkeypatch):
    """**The `None` vs `[]` distinction.** Measured on this box, which cannot read
    dmesg at all: the real answer is `None`."""
    asked = _ring(monkeypatch, None)
    status, detail = D._kernel()

    assert status == D.STATUS_UNKNOWN, f"{status}: {detail}"
    assert "could not be read" in detail, detail
    assert "not evidence that the kernel is quiet" in detail, (
        f"an unreadable ring is being reported as a quiet kernel: {detail!r}")
    assert asked == [3], (
        "the warnings level was asked for on an unreadable ring; it cannot be "
        f"reached from there, so this was a wasted read: {asked}")


def test_errors_are_reported_without_asking_about_warnings(monkeypatch):
    asked = _ring(monkeypatch, ["[12345.678] usb 1-2: device descriptor read error"])
    status, detail = D._kernel()

    assert status == D.STATUS_NOT_WORKING, status
    assert "1 message(s) at error level or worse" in detail, detail
    assert "usb 1-2" in detail, f"the newest message is not shown: {detail!r}"
    assert asked == [3], (
        f"asked {asked}; once errors are known the warning level adds nothing and "
        f"costs a second read of the ring")


def test_a_clean_kernel_asks_about_warnings(monkeypatch):
    """**What `read_ring`'s level parameter is for.** Nothing else asks."""
    asked = _ring(monkeypatch, [], ["[   12.1] gpu: fell off the bus",
                                    "[   13.0] cpu: throttling activated"])
    status, detail = D._kernel()

    assert asked == [3, 4], f"the lower level was never requested: {asked}"
    assert status == D.STATUS_NOT_WORKING, f"{status}: {detail}"
    assert "nothing at error level" in detail, detail
    assert "2 at warning level" in detail, detail
    # **The last element is the newest**, because `read_ring` returns the ring in
    # chronological order. The sense's own code proves it - it prints
    # `reversed(shown[-8:])` under the words "Most recent first". So the row shows
    # `warnings[-1]`. The first version of this test asserted the *first* element
    # was shown and failed against correct code.
    assert "throttling activated" in detail, (
        f"the newest warning is not the one shown: {detail!r}")
    assert "fell off the bus" not in detail, (
        f"the oldest of two is being passed off as the most recent: {detail!r}")


def test_warnings_unreadable_after_a_clean_error_read(monkeypatch):
    """**The asymmetry that a truthiness check loses.** The error read succeeded and
    the warning read did not, so the answer is *could not determine* - not
    *working*."""
    _ring(monkeypatch, [], None)
    status, detail = D._kernel()

    assert status == D.STATUS_UNKNOWN, (
        f"a warning-level read that failed is being reported as {status!r}: {detail!r}")
    assert "could not be read at warning level" in detail, detail
    assert "not ruled out" in detail, detail


def test_quiet_at_both_levels(monkeypatch):
    asked = _ring(monkeypatch, [], [])
    status, detail = D._kernel()

    assert asked == [3, 4], asked
    assert status == D.STATUS_WORKING, f"{status}: {detail}"
    assert "nothing at error level or at warning level" in detail, detail


def test_the_row_in_the_panel_is_this_probe():
    """**Identity, not the title string.** The first version asserted
    `"Kernel ring buffer" in ROW_TITLES` and passed with the table entry replaced by
    `lambda: (STATUS_WORKING, "fine")` - every other test called `D._kernel()`
    directly, so nothing noticed the panel was showing a stub instead of the probe.

    A probe nobody renders, and a row rendering something else, are the same dead
    end with opposite causes. So this binds the *title* to the *function*.
    """
    assert "Kernel ring buffer" in D.ROW_TITLES, D.ROW_TITLES

    bound = [probe for _h, _d, rows in D._SECTIONS
             for title, probe in rows if title == "Kernel ring buffer"]

    assert len(bound) == 1, f"the title is bound {len(bound)} times in _SECTIONS"
    assert bound[0] is D._kernel, (
        "the row named 'Kernel ring buffer' does not call _kernel; the panel is "
        "showing something else entirely")


def test_read_ring_now_has_a_caller():
    """The smallest claim that was false, and the reason this file exists."""
    root = ROOT / "usr/lib/shani-chronoa"
    callers = [p for p in root.rglob("*.py")
               if "read_ring(" in p.read_text(encoding="utf-8", errors="replace")
               and p.name != "kernel_log.py"]
    assert callers, "kernel_log.read_ring() still has no caller"
