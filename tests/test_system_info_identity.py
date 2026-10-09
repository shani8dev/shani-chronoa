"""`system_info`: the machine's identity, read without root.

"Which BIOS am I running" had no answer. `dmidecode` is the tool people
reach for and it cannot help an unprivileged process: **measured here**, it
prints *"Permission denied"* and *"Can't read memory from /dev/mem"* while
**exiting 0** - a refusal that reads as success to any caller trusting the
status code. The same facts are world-readable in `/sys/class/dmi/id`, which
is the pattern this package already follows for processes and ports.

Two rules the test file holds, both about what is *not* read:

- **`product_serial` and its friends are never read.** They are root-only by
  design; a table that reports half its rows as `Permission denied` reads as
  a broken answer rather than as a permission boundary. A test asserts no
  serial-ish path is requested at all, so the next person adding a field does
  not add one that cannot be answered.
- **A missing file is `unknown`, never blank.** In a container the whole tree
  is absent, and that is reported as the container sentence rather than as an
  empty machine line.
"""

from __future__ import annotations

import pathlib
import sys

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "usr/lib/shani-chronoa"))

from shani_chronoa.skills import system_info as si  # noqa: E402

DMI = {
    "sys_vendor": "LENOVO\n",
    "product_name": "20TAS19000\n",
    "product_version": "ThinkPad E14 Gen 2\n",
    "board_name": "20TAS19000\n",
    "board_vendor": "LENOVO\n",
    "bios_version": "R1EET47W(1.47 )\n",
    "bios_date": "01/21/2022\n",
}


@pytest.fixture
def dmi(monkeypatch):
    """Point `_read` at a dict of DMI values, and record what is asked for."""
    asked: list = []

    def _fake(path):
        asked.append(path)
        name = path.rsplit("/", 1)[-1]
        return DMI.get(name)

    monkeypatch.setattr(si, "_read", _fake)
    return asked


def test_the_full_machine_line_names_bios_and_board(dmi):
    out = si._identity()
    assert "vendor: LENOVO" in out
    assert "model: 20TAS19000" in out
    assert "board: 20TAS19000" in out
    assert "bios: R1EET47W(1.47 )" in out      # the value this is for
    assert "bios date: 01/21/2022" in out


def test_the_answer_carries_the_identity(dmi):
    """End to end through `_run`: a mutation that drops the call, or drops the
    line from the joined output, is caught here rather than in the unit above.
    """
    out = si._run({})
    assert "machine: vendor: LENOVO" in out
    # The rest of the answer is still there - this is one line, not a new skill.
    assert "kernel: " in out
    assert "distribution: " in out


def test_a_trailing_space_in_a_dmi_field_is_kept_not_duplicated(dmi):
    """`bios_version` really ends in a space (\"R1EET47W(1.47 )\"), and the
    kernel's file ends in a newline. Stripping is the tool's job, done once."""
    assert "bios: R1EET47W(1.47 )" in si._identity()
    assert "\n" not in si._identity()


def test_no_root_only_field_is_ever_requested(dmi):
    """**The control that keeps the answer answerable.**

    `product_serial`, `product_uuid`, `product_sku` and `board_serial` are
    root-only. Requesting one would put `Permission denied` - or a fabricated
    empty value - in the answer. The paths asked for are recorded, so a future
    added field that cannot be read fails this test instead of shipping a
    blank.
    """
    si._identity()
    forbidden = ("serial", "uuid", "sku", "asset_tag")
    for path in dmi:
        assert not any(word in path.rsplit("/", 1)[-1] for word in forbidden), path


def test_a_container_with_no_dmi_says_so(monkeypatch):
    monkeypatch.setattr(si, "_read", lambda path: None)
    out = si._identity()
    assert "machine: unknown" in out
    assert "container" in out
    # Not a machine line of blanks.
    assert "vendor: " not in out


def test_a_field_the_kernel_does_not_publish_is_unknown(monkeypatch):
    missing = dict(DMI)
    del missing["bios_version"]
    monkeypatch.setattr(si, "_read", lambda path: missing.get(path.rsplit("/", 1)[-1]))
    out = si._identity()
    assert "bios: unknown" in out
    assert "vendor: LENOVO" in out        # the other fields still answer


def test_a_whitespace_only_field_is_unknown(monkeypatch):
    blank = dict(DMI)
    blank["bios_date"] = "   \n"
    monkeypatch.setattr(si, "_read", lambda path: blank.get(path.rsplit("/", 1)[-1]))
    assert "bios date: unknown" in si._identity()
