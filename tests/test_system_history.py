"""`system_history`: what was this machine doing earlier today?

`disk_activity` and `cpu_per_core` answer **right now**. Nothing answered
"was this busy last night", which is the question about a machine that
misbehaved overnight.

**The report below is captured verbatim from a real `@blue` slot**, banner and
blank line and all.

**The bug this file's central test exists for** is not subtle but is invisible
in the output. The header line is

    06:40:39        CPU     %user     %nice   %system   %iowait   %steal   %idle

whose **first token is a time-format label, not a column** - that cell holds
`06:40:40` on the rows below. So the header has the same shape as a data row.
Treat it as the columns without dropping that token and every value lands on
its neighbour's name:

    columns[1:] = ['CPU', '%user', '%nice', ...]
    values      = ['20.59', '0.00',  '0.00',  ...]
    -> "CPU 20.6%, user 0.0%"

Every number right, every label off by one, and it reads perfectly. That is
why this is asserted on the *labels*, not on the presence of a number.
"""

from __future__ import annotations

import os
import pathlib
import subprocess
import sys

import pytest

_REPO = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_REPO / "usr" / "lib" / "shani-chronoa"))

from shani_chronoa.skills import system_history as SH  # noqa: E402

#: Captured on a real slot. The banner is on its own line, then a BLANK line,
#: then the header whose first cell is a time-format label.
SLOT = (
    "Linux 7.2.6-arch2-1 (shanios) \t10/10/26 \t_x86_64_\t(8 CPU)\n"
    "\n"
    "06:40:39        CPU     %user     %nice   %system   %iowait   "
    "%steal     %idle\n"
    "06:40:40        all     20.46      0.00      7.12      0.00     "
    "0.00     72.43\n"
    "06:40:41        all     20.72      0.00      6.44      0.00     "
    "0.00     72.84\n"
    "Average:        all     20.59      0.00      6.78      0.00     "
    "0.00     72.63\n"
)

NO_HISTORY = ("Cannot open /var/log/sa/sa10: No such file or directory\n")


def _fake_sar(tmp_path, monkeypatch, stdout, rc=0, stderr=""):
    bindir = tmp_path / "bin"
    bindir.mkdir(exist_ok=True)
    script = bindir / "sar"
    script.write_text(
        "#!/usr/bin/env python3\n"
        "import sys\n"
        f"sys.stdout.write({stdout!r})\n"
        f"sys.stderr.write({stderr!r})\n"
        f"sys.exit({rc})\n")
    script.chmod(0o755)
    monkeypatch.setenv("PATH", f"{bindir}:{os.environ['PATH']}")


def _no_sa_files(monkeypatch, tmp_path):
    """An empty accounting directory - the collector has never run."""
    sa = tmp_path / "sa"
    sa.mkdir()
    monkeypatch.setattr(SH, "_history_days", lambda: [])


# --- the parser -----------------------------------------------------------------

def test_the_banner_is_neither_the_header_nor_a_row():
    """`Linux 7.2.6-arch2-1 (shanios) 10/10/26 _x86_64_ (8 CPU)` ends in a
    run of numbers, so a parser that takes the last numeric-looking line as
    the average reads the kernel version as a load figure.

    **The `_BANNER` guard in the module is not what makes this pass** -
    measured, the banner contains no `%`, does not begin with a time and does
    not match `_AVERAGE`, so the other three branches reject it on their own
    and removing the guard leaves this test green. It is asserted here
    because the *behaviour* is what matters and the guard is only insurance
    against a banner that later grows a `%`.
    """
    columns, rows = SH._parse(SLOT)
    assert all("Linux" not in c for c in columns)
    assert all("Linux" not in r["when"] for r in rows)
    assert all("7.2.6" not in v for r in rows for v in r["values"])


def test_the_header_is_not_a_data_row():
    """**This is the one that matters.** `06:40:39 CPU %user ...` has the
    same shape as `06:40:40 all 20.46 ...`, so a parser that recognises rows
    by shape reads the header as a row and every value after it shifts."""
    _, rows = SH._parse(SLOT)
    assert [r["when"] for r in rows] == ["06:40:40", "06:40:41", "Average"]
    assert all(r["cpu"] == "all" for r in rows)


def test_every_value_sits_under_its_own_name():
    """**The off-by-one.** Dropping the header's leading time token is what
    puts `%user`'s 20.59 under `%user` instead of under `CPU`:

        with the token:    CPU 20.6%, user 0.0%, nice 6.8%   <- all shifted
        without it:        user 20.6%, nice 0.0%, system 6.8%  <- correct

    Asserted on the **labels**, because a check that only asks "is 20.6
    somewhere in there" is satisfied by the broken version.
    """
    columns, rows = SH._parse(SLOT)
    average = next(r for r in rows if r["when"] == "Average")
    said = SH._explain(columns, average["values"])
    assert "user 20.6%" in said
    assert "system 6.8%" in said
    assert "idle 72.6%" in said
    assert "CPU" not in said, "the header's time token leaked into the columns"


def test_the_average_is_the_measurement_not_a_sample():
    """The last line is `Average:` and it is the whole reading; the rows
    above it are one-second samples. A reader that takes the first row
    reports a single second as the average."""
    _, rows = SH._parse(SLOT)
    assert rows[-1]["when"] == "Average"
    assert rows[0]["when"] != "Average"


def test_a_per_cpu_report_is_still_readable():
    """`sar -u ALL` prints one row per core and `all` is only one of them -
    so the number of rows is the column's business, not a fixed two."""
    per_cpu = (
        "06:40:39        CPU     %user     %nice   %system   %iowait   "
        "%steal     %idle\n"
        "06:40:40          0     10.00      0.00      5.00      0.00     "
        "0.00     85.00\n"
        "06:40:40          1     31.00      0.00      9.00      0.00     "
        "0.00     60.00\n"
        "Average:          0     10.00      0.00      5.00      0.00     "
        "0.00     85.00\n"
        "Average:          1     31.00      0.00      9.00      0.00     "
        "0.00     60.00\n"
    )
    columns, rows = SH._parse(per_cpu)
    assert len(rows) == 4
    assert all(r["cpu"] in ("0", "1") for r in rows)
    assert "user 10.0%" in SH._explain(columns, rows[-2]["values"])
    assert "user 31.0%" in SH._explain(columns, rows[-1]["values"])


# --- the honest part ------------------------------------------------------------

def _sentences_about_idleness(text: str) -> "list[str]":
    """Every sentence that talks about being idle.

    Split on **newlines as well as sentence enders** - the answer separates
    the quoted refusal and the disclaimer with a blank line, and a splitter
    that only knows about `.` glues them into one chunk that starts with
    "sar said:" and therefore fails any assertion about what the disclaimer
    says.
    """
    chunks = []
    for line in text.splitlines():
        chunks.extend(part for part in line.replace("!", ".").split(".")
                      if part.strip())
    return [chunk.strip() for chunk in chunks if "idle" in chunk.lower()]


def test_no_history_is_not_an_idle_machine(tmp_path, monkeypatch):
    """**The precondition, and it is the whole point of the skill.** The
    accounting files only exist if the collector has run. Where it has not,
    "nothing recorded" is the opposite of "the machine was idle", and an
    answer that reported an idle machine would be a confident wrong answer
    about a machine nobody was watching.

    **Asserted as a count of the sentences that mention being idle, not as
    the presence of the disclaimer.** The first version asserted the
    disclaimer was there, and a mutation that left it in place while adding
    "This machine was idle when you asked" straight after it - so the answer
    contradicted itself inside two lines - passed the whole suite. A test that
    asks whether a caveat is *present* cannot tell a caveat from a caveat plus
    its opposite.
    """
    _fake_sar(tmp_path, monkeypatch, "", rc=2, stderr=NO_HISTORY)
    _no_sa_files(monkeypatch, tmp_path)
    out = SH._run_skill({})
    assert "no accounting history" in out

    talking = _sentences_about_idleness(out)
    assert len(talking) == 1, f"the answer must say one thing about idleness, not {talking}"
    assert talking[0].lower().startswith("this is not the same"), talking[0]


def test_an_empty_report_for_one_topic_is_a_gap_not_an_idle_machine(
        tmp_path, monkeypatch):
    """Other topics may have history while this one has none, so the message
    distinguishes "the record has a hole" from "nothing was recorded"."""
    _fake_sar(tmp_path, monkeypatch, "\n")
    monkeypatch.setattr(SH, "_history_days", lambda: ["sa10", "sa09"])
    out = SH._run_skill({"topic": "d"})
    assert "sa10" in out
    assert "gap in the record" in out


def test_the_sar_refusal_is_quoted_not_invented(tmp_path, monkeypatch):
    _fake_sar(tmp_path, monkeypatch, "", rc=2, stderr=NO_HISTORY)
    _no_sa_files(monkeypatch, tmp_path)
    assert "Cannot open /var/log/sa/sa10" in SH._run_skill({})


def test_a_hung_sar_is_not_an_empty_record(tmp_path, monkeypatch):
    _fake_sar(tmp_path, monkeypatch, SLOT)

    def boom(*a, **k):
        raise subprocess.TimeoutExpired("sar", 30)

    monkeypatch.setattr(SH.subprocess, "run", boom)
    assert "did not answer" in SH._run_skill({})


# --- the skill ------------------------------------------------------------------

def test_the_average_is_what_the_answer_leads_with(tmp_path, monkeypatch):
    _fake_sar(tmp_path, monkeypatch, SLOT)
    monkeypatch.setattr(SH, "_history_days", lambda: ["sa10"])
    out = SH._run_skill({})
    assert "recorded average" in out
    assert "user 20.6%" in out


def test_present_accounting_files_are_named(tmp_path, monkeypatch):
    """A reader who asks 'was it busy at lunchtime' needs to know whether the
    answer covers today or a fortnight ago, so the files present are shown."""
    _fake_sar(tmp_path, monkeypatch, SLOT)
    monkeypatch.setattr(SH, "_history_days", lambda: ["sa10", "sa09", "sa08"])
    assert "sa10, sa09, sa08" in SH._run_skill({})


def test_only_the_documented_topics_are_offered(tmp_path, monkeypatch):
    _fake_sar(tmp_path, monkeypatch, SLOT)
    assert "not a report this knows how to read" in SH._run_skill(
        {"topic": "network"})
    # `b` and `r` are real sar reports, not refusals.
    for topic in ("u", "d", "r", "b"):
        assert "not a report" not in SH._run_skill({"topic": topic})


def test_a_missing_sar_names_its_package(tmp_path, monkeypatch):
    monkeypatch.setenv("PATH", str(tmp_path / "empty"))
    assert "sysstat" in SH._run_skill({})


def test_it_never_deletes_the_accounting_files(tmp_path, monkeypatch):
    """`sar` can rewrite history on the same machine (`-f` plus a save mode),
    and this skill only ever runs the reading forms."""
    bindir = tmp_path / "bin"
    bindir.mkdir(exist_ok=True)
    log = bindir / "argv"
    script = bindir / "sar"
    script.write_text(
        "#!/usr/bin/env python3\n"
        "import sys\n"
        f"open({str(log)!r}, 'a').write(' '.join(sys.argv[1:]) + '\\n')\n"
        f"sys.stdout.write({SLOT!r})\n")
    script.chmod(0o755)
    monkeypatch.setenv("PATH", f"{bindir}:{os.environ['PATH']}")
    monkeypatch.setattr(SH, "_history_days", lambda: ["sa10"])
    SH._run_skill({})
    argv = log.read_text().split()
    assert argv[:1] == ["-u"], argv
    for flag in ("-f", "-o", "-S", "-e", "-I", "-s"):
        assert flag not in argv, f"system_history must not pass {flag}"