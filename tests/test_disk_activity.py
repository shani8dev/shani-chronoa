"""`disk_activity`: how hard the disks are working.

No Chronoa skill answered "why is the machine slow" from the *disk's* point of
view — `disk_usage` says how full it is, the `storage` sense says what disks
exist, and the number that explains "slow" (utilisation, wait) was read
nowhere.

`iostat -x` from `sysstat` reads it unprivileged, and `sysstat` is in
`shani-tools-extra`, so the binary is on every image by design.

**Measured here, and the parse was wrong twice before it was right:** the
second report is the now (`iostat`'s first is since-boot); the wait columns are
`r_await` and `w_await`, not a combined `await`; and `loop`/`ram`/`zram`
devices are not disks — `lsblk` had already learned that lesson in the
`storage` sense.
"""

from __future__ import annotations

import os
import pathlib
import sys

import pytest

_REPO = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_REPO / "usr" / "lib" / "shani-chronoa"))

from shani_chronoa.skills import disk_activity as DA  # noqa: E402

#: Real `iostat -x 1 2` output: the since-boot report, then the live one, with
#: an `avg-cpu` line between them that is not part of the device table.
REAL = """Linux 7.0.0-38-generic (host) \t10/10/2026 \t_x86_64_\t(8 CPU)

avg-cpu:  %user   %nice %system %iowait  %steal   %idle
          0.10    0.00    0.30    0.05    0.00   99.55

Device            r/s     rkB/s   rrqm/s  %rrqm r_await rareq-sz     w/s     wkB/s   wrqm/s  %wrqm w_await wareq-sz     d/s     dkB/s   drqm/s  %drqm d_await dareq-sz     f/s f_await  aqu-sz  %util
nvme0n1            0.00      0.00     0.00   0.00    0.00     0.00    4.00    140.00     0.00   0.00    5.50    35.00    0.00      0.00     0.00   0.00    0.00     0.00    0.00    0.00    0.08   2.20
loop0              0.00      0.00     0.00   0.00    0.00     0.00    0.00      0.00     0.00   0.00    0.00     0.00    0.00      0.00     0.00   0.00    0.00     0.00    0.00    0.00    0.00   0.00

Device            r/s     rkB/s   rrqm/s  %rrqm r_await rareq-sz     w/s     wkB/s   wrqm/s  %wrqm w_await wareq-sz     d/s     dkB/s   drqm/s  %drqm d_await dareq-sz     f/s f_await  aqu-sz  %util
nvme0n1            1.00     40.00     0.00   0.00    0.80    40.00    3.00    120.00     0.00   0.00    6.10    40.00    0.00      0.00     0.00   0.00    0.00     0.00    0.00    0.00    0.11   3.40
loop0              0.00      0.00     0.00   0.00    0.00     0.00    0.00      0.00     0.00   0.00    0.00     0.00    0.00      0.00     0.00   0.00    0.00     0.00    0.00    0.00    0.00   0.00

"""


@pytest.fixture
def granted(monkeypatch):
    class On:
        def sense_allowed(self, name):
            return name == "storage"

        def sense_allowed_reason(self, name):
            return f"'{name}-sense-enabled' is off"

    monkeypatch.setattr(DA, "ChronoaConfig", On)
    return True


@pytest.fixture
def refused(monkeypatch):
    class Off:
        def sense_allowed(self, name):
            return False

        def sense_allowed_reason(self, name):
            return f"'{name}-sense-enabled' is off"

    monkeypatch.setattr(DA, "ChronoaConfig", Off)


def _fake_iostat(tmp_path, monkeypatch, stdout=REAL, code=0, timeout=False):
    bindir = tmp_path / "bin"
    bindir.mkdir(exist_ok=True)
    data = tmp_path / "iostat.txt"
    data.write_text(stdout)
    script = bindir / "iostat"
    script.write_text(
        "#!/usr/bin/env python3\n"
        f"print(open({str(data)!r}).read(), end='')\n"
        f"raise SystemExit({code})\n"
    )
    script.chmod(0o755)
    monkeypatch.setenv("PATH", f"{bindir}:{os.environ['PATH']}")
    if timeout:
        monkeypatch.setattr(DA.subprocess, "run",
                            lambda *a, **k: (_ for _ in ()).throw(
                                __import__("subprocess").TimeoutExpired("iostat", 30)))


def test_the_second_report_is_the_now(tmp_path, monkeypatch, granted):
    """**The first report is since boot.** Both reports carry a disk with
    different numbers, and only the second describes the present. Reading the
    first would report a machine's history as its current state.
    """
    _fake_iostat(tmp_path, monkeypatch)
    rows, _problem = DA._sample()
    assert len(rows) == 1
    assert rows[0]["Device"] == "nvme0n1"
    assert rows[0]["r/s"] == "1.00"        # the LIVE sample, not the boot one
    assert rows[0]["%util"] == "3.40"


def test_read_and_write_wait_are_reported_separately(tmp_path, monkeypatch, granted):
    """sysstat's real columns are `r_await` and `w_await`; a parser looking for
    `await` prints a question mark beside every disk, which reads as a broken
    probe rather than as a field that is not there.
    """
    _fake_iostat(tmp_path, monkeypatch)
    out = DA._run({})
    assert "wait 0.80 ms" in out          # r_await
    assert "wait 6.10 ms" in out          # w_await
    assert "ms)" not in out.split("\n", 1)[0] or "?" not in out


def test_loop_and_ram_devices_are_not_disks(tmp_path, monkeypatch, granted):
    """A loop device is a file mounted as a block device; reporting its zeros
    as "one more idle disk" is noise, and the storage sense already learned
    this from lsblk.
    """
    _fake_iostat(tmp_path, monkeypatch)
    rows, _ = DA._sample()
    assert all(r["Device"] == "nvme0n1" for r in rows), rows


def test_a_disk_at_high_utilisation_is_named_as_the_cause(tmp_path, monkeypatch, granted):
    """The answer to "why is it slow" is the utilisation, and a saturated disk
    is called out rather than left in a table of zeros.
    """
    busy_lines = []
    for line in REAL.splitlines():
        if line.startswith("nvme0n1") and "3.40" in line:
            busy_lines.append(
                "nvme0n1            5.00    200.00     0.00   0.00   40.00    40.00    "
                "9.00    300.00     0.00   0.00   90.00    33.00    0.00      0.00     "
                "0.00   0.00    0.00     0.00    0.00    0.00    1.50  99.00")
        else:
            busy_lines.append(line)
    _fake_iostat(tmp_path, monkeypatch, stdout="\n".join(busy_lines))
    out = DA._run({})
    assert "Busy right now" in out
    assert "nvme0n1" in out
    assert "99.00% used" in out


def test_the_avg_cpu_line_is_not_a_device(tmp_path, monkeypatch, granted):
    """`iostat -x` prints a CPU summary between the device tables; taking its
    first token as a device name would put `avg-cpu:` in the answer.
    """
    _fake_iostat(tmp_path, monkeypatch)
    rows, _ = DA._sample()
    assert not any("cpu" in r["Device"].lower() for r in rows), rows


def test_the_storage_sense_gates_it(refused, tmp_path, monkeypatch):
    _fake_iostat(tmp_path, monkeypatch)
    out = DA._run({})
    assert "storage" in out
    assert "sense" in out


def test_a_missing_iostat_names_the_package(tmp_path, monkeypatch):
    empty = tmp_path / "no-iostat"
    empty.mkdir()
    monkeypatch.setenv("PATH", str(empty))
    out = DA._run({})
    assert "iostat" in out
    assert "sysstat" in out          # the package that provides it


def test_a_timeout_is_unknown(tmp_path, monkeypatch, granted):
    _fake_iostat(tmp_path, monkeypatch, timeout=True)
    out = DA._run({})
    assert "UNKNOWN" in out
    assert "did not answer" in out


def test_a_failing_iostat_says_its_own_words(tmp_path, monkeypatch, granted):
    _fake_iostat(tmp_path, monkeypatch, stdout="iostat: command not found\n", code=1)
    out = DA._run({})
    assert "UNKNOWN" in out
    assert "iostat said" in out


def test_a_report_with_no_disks_is_not_the_same_as_an_idle_disk(tmp_path, monkeypatch, granted):
    """Only loop devices in the report means the reports were all files
    pretending to be disks - not that the machine's disk is idle.
    """
    _fake_iostat(tmp_path, monkeypatch,
                 stdout="Device  r/s  %util\nloop0   0.00   0.00\n\n")
    out = DA._run({})
    assert "no block devices doing work" in out.lower()
    assert "not that the disk is idle" in out


def test_the_real_binary_is_used_when_present():
    if not pathlib.Path("/usr/bin/iostat").exists():
        pytest.skip("iostat is not installed here")
    rows, problem = DA._sample()
    assert problem == "", problem
    # Every row is a disk the kernel actually reports, and every one has the
    # columns the answer reads.
    for row in rows:
        assert "Device" in row and "r_await" in row and "%util" in row, row
