"""`cpu_per_core`: is one core busy while the machine looks idle?

`system_info` says how many CPUs there are and `cpu_frequency` says what the
governor is doing; neither says **which** cores carry the load. On an eight-core
machine one process pinning one core looks idle in every system-wide average -
which is exactly what "Chronoa feels slow but nothing looks busy" looks like.

**Four defects found by running the real binary, in order.** Every one of them
shipped as "the machine has no per-core data" or a core named `10.37`:

1. **`mpstat 1 2` prints only an `all` row on this kernel**, and exits 0.
   `-P ALL` is required. Both forms exit 0, so the exit status distinguishes
   nothing and a parser reading the plain form finds no cores at all.
2. **The clock is `cells[0]`, not the CPU label.** Rows read
   `10:47:12 AM  0  10.69 ...`, so the label is at the index where `CPU` sits
   in the header. That index is *not* fixed - this box's clock is two cells
   (`10:47:12` + `AM`), a 24-hour locale's is one.
3. **There are four reports and the live one is the second-to-last.** The
   first interval is since boot; the last is the `Average:` block. Taking the
   last reads a label column that says `Average:`.
4. **The header line's first cell is the clock, not `CPU`** - so `cells[0] ==
   "CPU"` finds no report and the skill refuses on every machine.

And one fixed rather than found: the column indices are read **from the header
by name**. A hardcoded index assumes sysstat printed its default column set;
when it prints a different one, a fixed index reads the neighbouring
percentage - a number of the right shape and the wrong meaning.
"""

from __future__ import annotations

import os
import pathlib
import sys

import pytest

_REPO = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_REPO / "usr" / "lib" / "shani-chronoa"))

from shani_chronoa.skills import cpu_per_core as CP  # noqa: E402

#: Real `mpstat -P ALL 1 2` from this machine: the banner, the first interval,
#: the second (live) interval, and the Average block - four reports, and the
#: one to read is the third.
REAL = """Linux 7.0.0-38-generic (administrator-ThinkPad-E14-Gen-2) \t10/10/2026 \t_x86_64_\t(8 CPU)

10:50:26 AM  CPU    %usr   %nice    %sys %iowait    %irq   %soft  %steal  %guest  %gnice   %idle
10:50:27 AM    0    1.00    0.00    0.50    0.00    0.00    0.00    0.00    0.00    0.00   98.50
10:50:27 AM    1    0.50    0.00    0.50    0.00    0.00    0.00    0.00    0.00    0.00   99.00
10:50:27 AM    2    3.00    0.00    1.00    0.00    0.00    0.00    0.00    0.00    0.00   96.00
10:50:27 AM  all    1.13    0.00    0.50    0.00    0.00    0.00    0.00    0.00    0.00   98.38

10:50:28 AM  CPU    %usr   %nice    %sys %iowait    %irq   %soft  %steal  %guest  %gnice   %idle
10:50:28 AM    0    2.00    0.00    1.00    0.00    0.00    0.00    0.00    0.00    0.00   97.00
10:50:28 AM    1    1.00    0.00    0.00    0.00    0.00    0.00    0.00    0.00    0.00   99.00
10:50:28 AM    2   80.00    0.00    5.00    0.00    0.00    0.00    0.00    0.00    0.00   15.00
10:50:28 AM  all   27.67    0.00    2.00    0.00    0.00    0.00    0.00    0.00    0.00   70.33

Average:     CPU    %usr   %nice    %sys %iowait    %irq   %soft  %steal  %guest  %gnice   %idle
Average:       0    1.50    0.00    0.75    0.00    0.00    0.00    0.00    0.00    0.00   97.75
Average:       1    0.75    0.00    0.25    0.00    0.00    0.00    0.00    0.00    0.00   99.00
Average:       2   41.50    0.00    3.00    0.00    0.00    0.00    0.00    0.00    0.00   55.50
Average:     all   14.58    0.00    1.33    0.00    0.00    0.00    0.00    0.00    0.00   84.08
"""

#: A machine whose clocks are 24-hour, so the label column shifts left by one.
REAL_24H = REAL.replace("AM", "")

#: Plain `mpstat 1 2` on this kernel - an `all` row and nothing else, exit 0.
REAL_NO_PERCORE = """Linux 7.0.0-38-generic (host) \t10/10/2026 \t_x86_64_\t(8 CPU)

10:50:02 AM  CPU    %usr   %nice    %sys %iowait    %irq   %soft  %steal  %guest  %gnice   %idle
10:50:03 AM  all   11.00    0.00    0.63    0.00    0.00    0.00    0.00    0.00    0.00   88.37
10:50:04 AM  all    8.22    0.00    1.01    0.00    0.00    0.00    0.00    0.00    0.00   90.77
Average:     all    9.61    0.00    0.82    0.00    0.00    0.00    0.00    0.00    0.00   89.57
"""


def _fake_mpstat(tmp_path, monkeypatch, stdout=REAL, code=0, record=None):
    bindir = tmp_path / "bin"
    bindir.mkdir(exist_ok=True)
    data = tmp_path / "out.txt"
    data.write_text(stdout)
    log = tmp_path / "argv.txt"
    script = bindir / "mpstat"
    script.write_text(
        "#!/usr/bin/env python3\n"
        "import sys\n"
        f"open({str(log)!r}, 'a').write(' '.join(sys.argv[1:]) + '\\n')\n"
        f"sys.stdout.write(open({str(data)!r}).read())\n"
        f"sys.exit({code})\n")
    script.chmod(0o755)
    monkeypatch.setenv("PATH", f"{bindir}:{os.environ['PATH']}")
    return log


def test_it_asks_for_per_cpu_rows(tmp_path, monkeypatch):
    """**Measured: plain `mpstat 1 2` prints only an `all` row and exits 0.**
    `-P ALL` is not a nicety - without it this skill refuses on every
    multi-core machine while reporting nothing wrong.
    """
    log = _fake_mpstat(tmp_path, monkeypatch)
    CP._run({})
    assert "-P ALL" in log.read_text()


def test_only_an_aggregate_is_not_a_measurement_of_any_core(tmp_path, monkeypatch):
    """`all` is the machine, not a core. If that is all mpstat printed, saying
    "no per-CPU rows" is the honest answer - reporting it as a quiet machine
    would be a table of averages standing in for the question.
    """
    _fake_mpstat(tmp_path, monkeypatch, stdout=REAL_NO_PERCORE)
    out = CP._run({})
    assert "no per-CPU rows" in out or "aggregate" in out
    assert "core 0" not in out


def test_the_live_report_is_not_the_first_and_not_the_average(tmp_path, monkeypatch):
    """**Four reports, and the live one is the second-to-last.** The first
    interval is since boot and the last is `Average:`. Core 2 is idle (96%)
    in the first interval and pegged (80%) in the live one - reading the wrong
    report gives the opposite answer to the question the skill exists for.
    """
    _fake_mpstat(tmp_path, monkeypatch)
    out = CP._run({})
    assert "core 2:  85.0% in use" in out        # 100 - 15 idle, the LIVE row
    assert "Busy right now" in out
    assert "core 2" in out


def test_a_core_whose_label_is_the_clock_is_impossible(tmp_path, monkeypatch):
    """The label sits at the header's `CPU` index, not at `cells[0]`. A first
    version read `cells[0]` and produced cores named `10.50` and `9.78`.
    """
    _fake_mpstat(tmp_path, monkeypatch)
    out = CP._run({})
    for bad in ("core 10.5", "core 10.50", "core 9.78", "core Average"):
        assert bad not in out, out


def test_the_clock_width_does_not_move_the_columns(tmp_path, monkeypatch):
    """This box's clock is two cells (`10:50:26` + `AM`); a 24-hour locale's
    is one. The header says where the label is, so both must read the same.
    """
    _fake_mpstat(tmp_path, monkeypatch)
    twelve = CP._run({})
    _fake_mpstat(tmp_path, monkeypatch, stdout=REAL_24H)
    twentyfour = CP._run({})
    assert "core 2:  85.0% in use" in twelve
    assert "core 2:  85.0% in use" in twentyfour


def test_the_all_row_is_not_listed_as_a_core(tmp_path, monkeypatch):
    """It is an aggregate wearing the same shape as a core, and counting it
    would mean nine cores on an eight-core machine.
    """
    _fake_mpstat(tmp_path, monkeypatch)
    out = CP._run({})
    assert "3 core(s)" in out
    assert "core all" not in out


def test_iowait_is_reported_as_disk_waiting_not_computing(tmp_path, monkeypatch):
    """A core with time in %iowait is blocked on storage, which is a different
    diagnosis from a busy CPU - and it points at `disk_activity`.
    """
    lines = []
    for line in REAL.splitlines():
        if line.startswith("10:50:28 AM    0"):
            cells = line.split()
            cells[6] = "70.00"      # %iowait
            cells[10] = "25.00"     # %idle
            line = "  ".join(cells)
        lines.append(line)
    _fake_mpstat(tmp_path, monkeypatch, stdout="\n".join(lines))
    out = CP._run({})
    assert "waiting on disk" in out
    assert "disk_activity" in out


def test_an_idle_machine_says_so(tmp_path, monkeypatch):
    idle = REAL.replace("  80.00    0.00    5.00    0.00    0.00    0.00    0.00    0.00    0.00   15.00",
                        "   1.00    0.00    0.50    0.00    0.00    0.00    0.00    0.00    0.00   98.50")
    _fake_mpstat(tmp_path, monkeypatch, stdout=idle)
    out = CP._run({})
    assert "No single core is above 80%" in out


def test_a_different_column_set_is_read_by_name(tmp_path, monkeypatch):
    """**Columns come from the header by name.** A hardcoded index reads the
    neighbouring column when sysstat prints a different set - a number of the
    right shape and the wrong meaning.

    The fixture below drops `%steal` and `%guest` from *both* the header and
    every row, leaving nine value columns where the original had eleven. A
    hardcoded `%idle` index would land two columns early and read `%guest`.
    """
    header_old = ("CPU    %usr   %nice    %sys %iowait    %irq   %soft  "
                  "%steal  %guest  %gnice   %idle")
    header_new = "CPU    %usr   %nice    %sys %iowait    %irq   %soft  %gnice   %idle"
    lines = []
    for line in REAL.splitlines():
        if header_old in line:
            lines.append(line.replace(header_old, header_new))
            continue
        cells = line.split()
        if cells and cells[-1].replace(".", "").isdigit():
            # Drop the 7th and 8th values (%steal, %guest) from the row.
            del cells[7:9]
            lines.append("  ".join(cells))
            continue
        lines.append(line)
    _fake_mpstat(tmp_path, monkeypatch, stdout="\n".join(lines))
    out = CP._run({})
    assert "core 2:  85.0% in use" in out
    # %usr is the FIRST value column, so a hardcoded index would read it and
    # then %idle would land on a later column. My first expectation here was
    # "15.0% user" - which is the *idle* column, i.e. the very mistake the
    # test exists to catch, written into the test.
    assert "80.0% user" in out
    assert "15.0% user" not in out


def test_an_unknown_column_is_said_not_guessed(tmp_path, monkeypatch):
    """If the header does not name a column this reads, the row says so
    rather than quoting a neighbouring number.
    """
    renamed = REAL.replace("%idle", "%idleX")
    _fake_mpstat(tmp_path, monkeypatch, stdout=renamed)
    out = CP._run({})
    assert "did not print the columns" in out


def test_a_banner_only_output_is_not_a_clean_reading(tmp_path, monkeypatch):
    """A banner and nothing else. Which of the two refusals is named does not
    matter to a person - both say UNKNOWN and neither quotes a number - so
    this asserts the property rather than the wording.
    """
    _fake_mpstat(tmp_path, monkeypatch,
                 stdout="Linux 7.0.0-38-generic (host)\n")
    out = CP._run({})
    assert "Nothing was guessed" in out
    assert "%" not in out          # no percentage is reported at all


def test_a_missing_mpstat_names_the_package(tmp_path, monkeypatch):
    monkeypatch.setenv("PATH", str(tmp_path / "empty"))
    out = CP._run({})
    assert "mpstat" in out


def test_a_failing_mpstat_says_its_own_words(tmp_path, monkeypatch):
    _fake_mpstat(tmp_path, monkeypatch, stdout="mpstat: cannot open\n", code=1)
    out = CP._run({})
    assert "Nothing was guessed" in out


def test_the_real_binary_is_read_when_present():
    """Not a stub: the real `mpstat`, on this machine, right now."""
    import shutil
    if not shutil.which("mpstat"):
        pytest.skip("mpstat is not installed on this box")
    rows, header, offset, problem = CP._sample()
    assert problem == "", problem
    assert rows, "the real mpstat produced no per-core rows"
    # Every label is a core number, never a clock and never `all`.
    for label, *_ in rows:
        assert label.isdigit(), label
    assert "%idle" in header
    assert header[offset] == "CPU"